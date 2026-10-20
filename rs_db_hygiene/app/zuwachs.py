"""Zuwachs-Statistik: neue Zeilen pro Tag und Entität, dauerhaft gespeichert (zuwachs.db unter /data).

Je voll abgeschlossenem lokalen Kalendertag und Entität werden vier Zahlen festgehalten:
  states  neue Zeilen in states
  kurz    neue Zeilen in statistics_short_term (5-Min-Statistik)
  lts     neue Zeilen in statistics (Langzeit)
  attr    neue Zeilen in state_attributes, die an diesem Tag erstmals von States der Entität benutzt wurden
  events  neue Zeilen in events (alle Event-Typen) – gehören keiner Entität, stehen unter der Kennung ev:<Event-Typ> und
          zählen nur in der Summe über alle Entitäten (Punkt 284)

Anders als der Analyse-Cache wird die Datei nie neu aufgebaut, sondern nur fortgeschrieben: Der Recorder und die
Löschläufe der App entfernen alte Zeilen, die Tageswerte bleiben. Ein einmal erfasster Tag wird nie kleiner (MAX).
Tage ohne Zeile einer Entität heißen 0, solange der Tag in der Abdeckung der Quelle liegt (Tabelle info: <quelle>_von/_bis).

Quellen states/kurz/lts kommen ohne weitere Abfrage aus den Tagestabellen des frischen Analyse-Caches. Der jeweils älteste
Tag der Quelle wird nicht übernommen (der Recorder hat ihn angeschnitten). Beim ersten Lauf ist der Verlauf deshalb nur so
weit gefüllt, wie die Recorder-Datenbank noch Zeilen hat – bereits von der App gelöschte Tage sind zu niedrig erfasst.

attr lässt sich nicht rückwirkend ermitteln: state_attributes hat keinen Zeitstempel. Neue Zeilen haben aber immer eine
höhere attributes_id als alle früheren. Gemerkt wird die höchste bis zum letzten erfassten Tag benutzte Id (attr_grenze);
neu an einem Tag sind die Ids darüber, die States dieses Tages benutzen. Erfasst wird daher erst ab dem übernächsten Tag
nach dem ersten Lauf (attr_von).

Speicherdauer (Punkt 287): Die Tageswerte bleiben ZUWACHS_MONATE Monate (App-Option zuwachs_monate, 1 bis 240). Nach jedem Lauf werden ältere
Tage entfernt und die Abdeckung der Quellen entsprechend gekürzt; neu erfasst wird nichts, was vor der Grenze liegt.

Nur Standardbibliothek; schreibt ausschließlich in die eigene Datei, die HA-Datenbank wird nur lesend geöffnet.
"""
import calendar
import datetime
import fnmatch
import logging
import sqlite3
import time

_LOG = logging.getLogger("zuwachs")
TAG = 86400
QUELLEN = ("states", "kurz", "lts", "attr", "events")
MAX_NACHHOLEN = 31            # so viele Tage holt die Attribut-Erfassung nach; bei mehr beginnt sie neu
UEBERLAPP = 1                 # letzte erfasste Tage werden beim Fortschreiben nochmals eingelesen (Nachbuchungen)
MAX_BEREICH = 3700            # Tage je Abfrage der GUI
ZUWACHS_MONATE = 24           # Vorgabe der Speicherdauer in Monaten (App-Option zuwachs_monate, 1 bis 240)

SCHEMA = """
CREATE TABLE IF NOT EXISTS info (schluessel TEXT PRIMARY KEY, wert TEXT);
CREATE TABLE IF NOT EXISTS tage (
  entity_id TEXT NOT NULL, tag INTEGER NOT NULL,
  states INTEGER NOT NULL DEFAULT 0, kurz INTEGER NOT NULL DEFAULT 0,
  lts INTEGER NOT NULL DEFAULT 0, attr INTEGER NOT NULL DEFAULT 0, events INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (entity_id, tag)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_tage_tag ON tage (tag);
"""

# Quelle -> (Abfrage im Analyse-Cache mit Platzhaltern für von/bis-Tag, kleinster Tag der Tabelle)
_CACHE_QUELLEN = {
    "states": ("SELECT e.entity_id, t.tag, t.n FROM st_tage t JOIN entitaet e ON e.metadata_id = t.metadata_id "
               "WHERE t.tag >= ? AND t.tag <= ?", "SELECT MIN(tag) FROM st_tage"),
    "kurz": ("SELECT e.entity_id, t.tag, t.n FROM kurz_tage t JOIN entitaet e ON e.lts_id = t.lts_id "
             "WHERE t.tag >= ? AND t.tag <= ?", "SELECT MIN(tag) FROM kurz_tage"),
    "lts": ("SELECT e.entity_id, t.tag, t.n FROM lts_tage t JOIN entitaet e ON e.lts_id = t.lts_id "
            "WHERE t.tag >= ? AND t.tag <= ?", "SELECT MIN(tag) FROM lts_tage"),
    # Punkt 284: Events je Event-Typ; die Kennung ev: hält sie von den Entity-IDs getrennt
    "events": ("SELECT 'ev:' || event_type, tag, n FROM ev_tage WHERE tag >= ? AND tag <= ?", "SELECT MIN(tag) FROM ev_tage"),
}


def _migriere(con):
    """Ältere zuwachs.db: Spalte events nachrüsten (die Daten bleiben)."""
    if "events" not in {z[1] for z in con.execute("PRAGMA table_info(tage)")}:
        con.execute("ALTER TABLE tage ADD COLUMN events INTEGER NOT NULL DEFAULT 0")


def grenze_tag(heute, monate):
    """Ältester Tag (Tageszahl), der bei `monate` Monaten Speicherdauer noch bleibt; None bei 0 oder weniger (= keine Grenze).

    Kalendermonate: heute = 19.10.2026, 24 Monate → 19.10.2024. Gibt es den Tag im Zielmonat nicht (31.), gilt dessen letzter Tag."""
    if not monate or monate <= 0:
        return None
    d = datetime.date(1970, 1, 1) + datetime.timedelta(days=heute)
    jahr, monat = divmod(d.year * 12 + d.month - 1 - int(monate), 12)
    tag = min(d.day, calendar.monthrange(jahr, monat + 1)[1])
    return (datetime.date(jahr, monat + 1, tag) - datetime.date(1970, 1, 1)).days


def tag_beginn(tag):
    """Zeitstempel von 0:00 Ortszeit des lokalen Kalendertags (Tage seit 1.1.1970)."""
    d = datetime.date(1970, 1, 1) + datetime.timedelta(days=tag)
    return time.mktime((d.year, d.month, d.day, 0, 0, 0, 0, 0, -1))


def tag_datum(tag):
    return (datetime.date(1970, 1, 1) + datetime.timedelta(days=tag)).isoformat()


def datum_tag(text):
    """'2026-10-18' oder die Tageszahl als Text -> Tageszahl; sonst ValueError."""
    text = str(text).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    return (datetime.date.fromisoformat(text) - datetime.date(1970, 1, 1)).days


def _info_lesen(con):
    return dict(con.execute("SELECT schluessel, wert FROM info"))


def _info_setzen(con, **werte):
    con.executemany("INSERT OR REPLACE INTO info VALUES (?,?)", ((k, str(v)) for k, v in werte.items()))


def _schreibe(con, quelle, zeilen):
    con.executemany(
        f"INSERT INTO tage (entity_id, tag, {quelle}) VALUES (?,?,?) "
        f"ON CONFLICT(entity_id, tag) DO UPDATE SET {quelle} = MAX({quelle}, excluded.{quelle})", zeilen)


def _aus_cache(con, cache_pfad, heute, info, grenze=None):
    """states, kurz und lts aus den Tagestabellen des Analyse-Caches übernehmen (nur volle Tage, nicht der älteste)."""
    cache = sqlite3.connect(f"file:{cache_pfad}?mode=ro", uri=True)
    try:
        for quelle, (abfrage, min_abfrage) in _CACHE_QUELLEN.items():
            try:
                kleinster = cache.execute(min_abfrage).fetchone()[0]
            except sqlite3.OperationalError:     # Cache ohne ev_tage (alter Aufbau)
                continue
            if kleinster is None:
                continue
            neu_von, bis = kleinster + 1, heute - 1
            if grenze is not None:           # Aufbewahrung: nichts übernehmen, was ohnehin gleich wieder entfällt
                neu_von = max(neu_von, grenze)
            if neu_von > bis:
                continue
            alt_von, alt_bis = info.get(f"{quelle}_von"), info.get(f"{quelle}_bis")
            if alt_von is None or int(alt_bis) < neu_von - 1:       # erste Erfassung oder Lücke
                ab, von = neu_von, neu_von
            else:
                ab, von = max(neu_von, int(alt_bis) - UEBERLAPP), min(int(alt_von), neu_von)
            zeilen = cache.execute(abfrage, (ab, bis)).fetchall()
            _schreibe(con, quelle, zeilen)
            _info_setzen(con, **{f"{quelle}_von": von, f"{quelle}_bis": max(bis, int(alt_bis or bis))})
            _LOG.info("Zuwachs %s: %d Tageswerte bis %s", quelle, len(zeilen), tag_datum(bis))
    finally:
        cache.close()


def _beschneide(con, grenze):
    """Tageswerte vor der Grenze löschen und die Abdeckung der Quellen kürzen (Aufbewahrung). Liefert die Zahl gelöschter Zeilen."""
    if grenze is None:
        return 0
    weg = con.execute("DELETE FROM tage WHERE tag < ?", (grenze,)).rowcount
    info = _info_lesen(con)
    for q in QUELLEN:
        if f"{q}_von" in info and int(info[f"{q}_von"]) < grenze:
            _info_setzen(con, **{f"{q}_von": grenze})
    return weg


def _attribute(con, ha_pfad, meta, heute, info):
    """Neue state_attributes-Zeilen je Entität für die abgeschlossenen Tage seit dem letzten Lauf (siehe Modulkopf)."""
    ha = sqlite3.connect(f"file:{ha_pfad}?mode=ro", uri=True, timeout=60)
    ha.execute("PRAGMA query_only = 1")
    try:
        bis = info.get("attr_bis")
        if bis is None or heute - 1 - int(bis) > MAX_NACHHOLEN:
            grenze = ha.execute("SELECT MAX(attributes_id) FROM states").fetchone()[0] or 0
            _info_setzen(con, attr_bis=heute - 1, attr_grenze=grenze, attr_von=heute + 1)
            _LOG.info("Zuwachs attr: Start, erfasst wird ab %s", tag_datum(heute + 1))
            return
        von = int(info["attr_von"])
        grenze = int(info["attr_grenze"])
        for tag in range(int(bis) + 1, heute):
            zeilen = ha.execute(
                "SELECT metadata_id, COUNT(DISTINCT attributes_id), MAX(attributes_id) FROM states "
                "WHERE last_updated_ts >= ? AND last_updated_ts < ? AND attributes_id > ? GROUP BY metadata_id",
                (tag_beginn(tag), tag_beginn(tag + 1), grenze)).fetchall()
            if tag >= von:
                _schreibe(con, "attr", [(meta[m], tag, n) for m, n, _mx in zeilen if m in meta])
            grenze = max([grenze] + [mx for _m, _n, mx in zeilen])
            _info_setzen(con, attr_bis=tag, attr_grenze=grenze)
            con.commit()
    finally:
        ha.close()


def fortschreiben(zuwachs_pfad, cache_pfad, ha_pfad, meta, heute, monate=ZUWACHS_MONATE):
    """Nach einer Analyse aufrufen. meta = {metadata_id: entity_id} der Analyse, heute = lokaler Kalendertag,
    monate = Speicherdauer der Tageswerte; 0 oder None hebt die Grenze auf (nur intern, die App-Option beginnt bei 1)."""
    start = time.time()
    con = sqlite3.connect(zuwachs_pfad, timeout=60)
    try:
        con.executescript(SCHEMA)
        _migriere(con)
        info = _info_lesen(con)
        grenze = grenze_tag(heute, monate)
        _aus_cache(con, cache_pfad, heute, info, grenze)
        con.commit()
        _attribute(con, ha_pfad, meta, heute, _info_lesen(con))
        weg = _beschneide(con, grenze)
        if weg:
            _LOG.info("Zuwachs: %d Tageswerte vor %s entfernt (Aufbewahrung %d Monate)", weg, tag_datum(grenze), monate)
        _info_setzen(con, stand=time.time())
        con.commit()
    finally:
        con.close()
    _LOG.info("Zuwachs fortgeschrieben in %.1f s", time.time() - start)


def lies_events(zuwachs_pfad, von=None, bis=None, muster=None):
    """Neue Event-Zeilen pro Tag: Summe über alle Event-Typen und der Verlauf je Typ (Punkt 286).

    muster (Punkt 292): Liste von Event-Typen bzw. Mustern mit * und ? wie in Event-Regeln – dann nur die passenden Typen,
    die Summe läuft über sie (Zuwachs-Chart in der Event-Regelkarte). Leere Liste = keine Typen.

    Liefert {"abdeckung", "tage": [{"tag", "datum", "events"}], "typen": [{"event_type", "summe", "werte": [je Tag]}]};
    die Typen sind nach ihrer Summe im Bereich absteigend sortiert, "werte" läuft parallel zu "tage" (0 = keine Events an dem Tag)."""
    con = sqlite3.connect(f"file:{zuwachs_pfad}?mode=ro", uri=True, timeout=60)
    try:
        info = _info_lesen(con)
        if "events_von" not in info or "events_bis" not in info or int(info["events_von"]) > int(info["events_bis"]):
            return {"abdeckung": {}, "tage": [], "typen": []}
        a_von, a_bis = int(info["events_von"]), int(info["events_bis"])
        von = a_von if von is None else max(von, a_von)
        bis = a_bis if bis is None else min(bis, a_bis)
        if bis - von + 1 > MAX_BEREICH:
            von = bis - MAX_BEREICH + 1
        # Bereichsabfrage auf dem Primärschlüssel (entity_id, tag): 'ev:' <= Kennung < 'ev;'
        zeilen = con.execute("SELECT entity_id, tag, events FROM tage WHERE entity_id >= 'ev:' AND entity_id < 'ev;' AND tag BETWEEN ? AND ?",
                             (von, bis)).fetchall()
        je_typ = {}
        for eid, tag, n in zeilen:
            je_typ.setdefault(eid[3:], {})[tag] = n
        if muster is not None:      # Punkt 292: gleiche Prüfung wie regeln.event_regel (genauer Name oder fnmatch, groß/klein genau)
            je_typ = {t: d for t, d in je_typ.items() if any(m == t or fnmatch.fnmatchcase(t, m) for m in muster)}
        tage = list(range(von, bis + 1))
        typen = sorted(({"event_type": t, "summe": sum(d.values()), "werte": [d.get(tag, 0) for tag in tage]} for t, d in je_typ.items()),
                       key=lambda x: (-x["summe"], x["event_type"]))
        summen = [sum(t["werte"][i] for t in typen) for i in range(len(tage))]
        return {"abdeckung": {"events": {"von": a_von, "bis": a_bis, "von_datum": tag_datum(a_von), "bis_datum": tag_datum(a_bis)}},
                "tage": [{"tag": tag, "datum": tag_datum(tag), "events": s} for tag, s in zip(tage, summen)], "typen": typen,
                "stand": float(info.get("stand") or 0)}
    finally:
        con.close()


def lies(zuwachs_pfad, entity_id=None, von=None, bis=None):
    """Tageswerte für die GUI/Charts. Ohne entity_id die Summe über alle Entitäten.

    Liefert {"abdeckung": {quelle: {von, bis}}, "tage": [{"tag", "datum", states, kurz, lts, attr}]};
    ein Wert ist None, wenn der Tag außerhalb der Abdeckung dieser Quelle liegt, sonst eine Zahl (0 = keine neuen Zeilen)."""
    con = sqlite3.connect(f"file:{zuwachs_pfad}?mode=ro", uri=True, timeout=60)
    try:
        info = _info_lesen(con)
        abd = {q: {"von": int(info[f"{q}_von"]), "bis": int(info[f"{q}_bis"])} for q in QUELLEN
               if f"{q}_von" in info and f"{q}_bis" in info and int(info[f"{q}_von"]) <= int(info[f"{q}_bis"])}
        if not abd:
            return {"abdeckung": {}, "tage": [], "entity_id": entity_id}
        for a in abd.values():
            a["von_datum"], a["bis_datum"] = tag_datum(a["von"]), tag_datum(a["bis"])
        erster, letzter = min(a["von"] for a in abd.values()), max(a["bis"] for a in abd.values())
        von = erster if von is None else max(von, erster)
        bis = letzter if bis is None else min(bis, letzter)
        if bis - von + 1 > MAX_BEREICH:
            von = bis - MAX_BEREICH + 1
        vorhanden = [q for q in QUELLEN if q in {z[1] for z in con.execute("PRAGMA table_info(tage)")}]
        spalten = ", ".join(vorhanden)
        if entity_id:
            zeilen = con.execute(f"SELECT tag, {spalten} FROM tage WHERE entity_id = ? AND tag BETWEEN ? AND ?",
                                 (entity_id, von, bis)).fetchall()
        else:
            summen = ", ".join(f"SUM({q})" for q in vorhanden)
            zeilen = con.execute(f"SELECT tag, {summen} FROM tage WHERE tag BETWEEN ? AND ? GROUP BY tag", (von, bis)).fetchall()
        je_tag = {z[0]: dict(zip(vorhanden, z[1:])) for z in zeilen}
        tage = []
        for tag in range(von, bis + 1):
            werte = je_tag.get(tag, {})
            eintrag = {"tag": tag, "datum": tag_datum(tag)}
            for q in QUELLEN:
                # Events gehören keiner Entität: je Entität None, nur die Summe führt sie
                gueltig = q in vorhanden and q in abd and abd[q]["von"] <= tag <= abd[q]["bis"] and not (entity_id and q == "events")
                eintrag[q] = (werte.get(q) or 0) if gueltig else None
            tage.append(eintrag)
        return {"abdeckung": abd, "tage": tage, "entity_id": entity_id, "stand": float(info.get("stand") or 0)}
    finally:
        con.close()
