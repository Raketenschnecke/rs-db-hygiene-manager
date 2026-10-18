"""Zuwachs-Statistik: neue Zeilen pro Tag und Entität, dauerhaft gespeichert (zuwachs.db unter /data).

Je voll abgeschlossenem lokalen Kalendertag und Entität werden vier Zahlen festgehalten:
  states  neue Zeilen in states
  kurz    neue Zeilen in statistics_short_term (5-Min-Statistik)
  lts     neue Zeilen in statistics (Langzeit)
  attr    neue Zeilen in state_attributes, die an diesem Tag erstmals von States der Entität benutzt wurden

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

Nur Standardbibliothek; schreibt ausschließlich in die eigene Datei, die HA-Datenbank wird nur lesend geöffnet.
"""
import datetime
import logging
import sqlite3
import time

_LOG = logging.getLogger("zuwachs")
TAG = 86400
QUELLEN = ("states", "kurz", "lts", "attr")
MAX_NACHHOLEN = 31            # so viele Tage holt die Attribut-Erfassung nach; bei mehr beginnt sie neu
UEBERLAPP = 1                 # letzte erfasste Tage werden beim Fortschreiben nochmals eingelesen (Nachbuchungen)
MAX_BEREICH = 3700            # Tage je Abfrage der GUI

SCHEMA = """
CREATE TABLE IF NOT EXISTS info (schluessel TEXT PRIMARY KEY, wert TEXT);
CREATE TABLE IF NOT EXISTS tage (
  entity_id TEXT NOT NULL, tag INTEGER NOT NULL,
  states INTEGER NOT NULL DEFAULT 0, kurz INTEGER NOT NULL DEFAULT 0,
  lts INTEGER NOT NULL DEFAULT 0, attr INTEGER NOT NULL DEFAULT 0,
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
}


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


def _aus_cache(con, cache_pfad, heute, info):
    """states, kurz und lts aus den Tagestabellen des Analyse-Caches übernehmen (nur volle Tage, nicht der älteste)."""
    cache = sqlite3.connect(f"file:{cache_pfad}?mode=ro", uri=True)
    try:
        for quelle, (abfrage, min_abfrage) in _CACHE_QUELLEN.items():
            kleinster = cache.execute(min_abfrage).fetchone()[0]
            if kleinster is None:
                continue
            neu_von, bis = kleinster + 1, heute - 1
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


def fortschreiben(zuwachs_pfad, cache_pfad, ha_pfad, meta, heute):
    """Nach einer Analyse aufrufen. meta = {metadata_id: entity_id} der Analyse, heute = lokaler Kalendertag."""
    start = time.time()
    con = sqlite3.connect(zuwachs_pfad, timeout=60)
    try:
        con.executescript(SCHEMA)
        info = _info_lesen(con)
        _aus_cache(con, cache_pfad, heute, info)
        con.commit()
        _attribute(con, ha_pfad, meta, heute, _info_lesen(con))
        _info_setzen(con, stand=time.time())
        con.commit()
    finally:
        con.close()
    _LOG.info("Zuwachs fortgeschrieben in %.1f s", time.time() - start)


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
        spalten = ", ".join(QUELLEN)
        if entity_id:
            zeilen = con.execute(f"SELECT tag, {spalten} FROM tage WHERE entity_id = ? AND tag BETWEEN ? AND ?",
                                 (entity_id, von, bis)).fetchall()
        else:
            summen = ", ".join(f"SUM({q})" for q in QUELLEN)
            zeilen = con.execute(f"SELECT tag, {summen} FROM tage WHERE tag BETWEEN ? AND ? GROUP BY tag", (von, bis)).fetchall()
        je_tag = {z[0]: z[1:] for z in zeilen}
        tage = []
        for tag in range(von, bis + 1):
            werte = je_tag.get(tag, (0,) * len(QUELLEN))
            eintrag = {"tag": tag, "datum": tag_datum(tag)}
            for q, w in zip(QUELLEN, werte):
                eintrag[q] = w if q in abd and abd[q]["von"] <= tag <= abd[q]["bis"] else None
            tage.append(eintrag)
        return {"abdeckung": abd, "tage": tage, "entity_id": entity_id, "stand": float(info.get("stand") or 0)}
    finally:
        con.close()
