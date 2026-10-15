"""Analyse der Recorder-Datenbank – ausschließlich lesend.

Liest home-assistant_v2.db mit mode=ro und schreibt die Kennzahlen in einen eigenen Cache
(SQLite unter /data). Der Cache wird als neue Datei aufgebaut und erst am Ende ausgetauscht,
damit die GUI während eines Laufs den alten Stand weiter anzeigen kann.

Nur Standardbibliothek, damit das Modul auch ohne Add-on (z. B. per SSH) testbar ist:
    python3 analyse.py --db /homeassistant/home-assistant_v2.db --cache /tmp/cache.db \
        --registry /homeassistant/.storage/core.entity_registry --states states.json
"""
import json
import logging
import os
import re
import sqlite3
import time
import urllib.request

_LOG = logging.getLogger("analyse")
TAG = 86400
# Version des Cache-Aufbaus; ältere Caches werden beim Start neu analysiert (2 = mit Herkunft, ab 0.1.4; 3 = Events je Typ, ab 0.3.0;
# 4 = exakte erste/letzte Zeitstempel von 5-Min-Statistik und Langzeit, ab 0.4.11, Punkte 118/120;
# 5 = Tage als lokale Kalendertage statt UTC-Tage, ab 0.4.12, Punkt 121;
# 6 = Status deaktiviert, Klasse/Einheit deaktivierter Entitäten aus der Registry, ab 0.5.5b5, Punkte 185/186;
# 7 = Integration (platform der Registry) je Entität für den Regeltyp herkunft, ab 0.5.5b9, Punkt 196)
CACHE_VERSION = 7

CACHE_SCHEMA = """
CREATE TABLE info (schluessel TEXT PRIMARY KEY, wert TEXT);
CREATE TABLE entitaet (
  entity_id TEXT PRIMARY KEY, metadata_id INTEGER, domain TEXT,
  rows INTEGER, first_ts REAL, last_ts REAL, rows_24h INTEGER,
  in_registry INTEGER, has_state INTEGER, status TEXT,
  state_class TEXT, device_class TEXT, unit TEXT,
  lts_id INTEGER, lts_rows INTEGER, lts_first_ts REAL, lts_last_ts REAL, kurz_rows INTEGER,
  herkunft_art TEXT, herkunft TEXT, herkunft_info TEXT, device_id TEXT,
  kurz_first_ts REAL, kurz_last_ts REAL, plattform TEXT);
CREATE TABLE st_tage (metadata_id INTEGER, tag INTEGER, n INTEGER, PRIMARY KEY (metadata_id, tag));
CREATE TABLE lts_tage (lts_id INTEGER, tag INTEGER, n INTEGER, PRIMARY KEY (lts_id, tag));
CREATE TABLE kurz_tage (lts_id INTEGER, tag INTEGER, n INTEGER, PRIMARY KEY (lts_id, tag));
CREATE TABLE lts_ohne_entitaet (statistic_id TEXT PRIMARY KEY, source TEXT, rows INTEGER, in_registry INTEGER);
-- Events je Typ und Tag (ab 0.3.0, für Vorhaltezeiten je Event-Typ)
CREATE TABLE ev_tage (event_type TEXT, tag INTEGER, n INTEGER, PRIMARY KEY (event_type, tag));
"""

ATTR_SCHEMA = """
CREATE TABLE info (schluessel TEXT PRIMARY KEY, wert TEXT);
-- art 'erst' = Tag der ersten Verwendung (Zuwachs), 'letzt' = Tag der letzten Verwendung (löschbar ab)
CREATE TABLE attr_tage (metadata_id INTEGER, tag INTEGER, art TEXT, zeilen INTEGER, bytes INTEGER,
                        PRIMARY KEY (metadata_id, tag, art));
"""


def versatz_abschnitte(von_ts, bis_ts):
    """Punkt 121 (0.4.12): lückenlose Abschnitte [beginn, ende, versatz_s] mit gleichem UTC-Versatz der Ortszeit
    (Sommer-/Winterzeit), Grenzen auf UTC-Tagesbeginn. Die Umstellung liegt nachts (1–3 Uhr) und nie an einer lokalen
    Tagesgrenze; für die Zuordnung zum Kalendertag genügt deshalb der Versatz um 12 Uhr UTC des jeweiligen Tages."""
    out = []
    for t in range(int(von_ts // TAG), int(bis_ts // TAG) + 2):
        v = time.localtime(t * TAG + TAG // 2).tm_gmtoff
        if out and out[-1][2] == v:
            out[-1][1] = (t + 1) * TAG
        else:
            out.append([t * TAG, (t + 1) * TAG, v])
    return out


def tag_sql(ausdruck, abschnitte):
    """SQL für den lokalen Kalendertag (Tage seit 1.1.1970, Ortszeit) eines Zeitstempels. Statt strftime(…, 'localtime')
    (gemessen +45 % Laufzeit) ein Versatz je Abschnitt; die jüngsten Abschnitte zuerst, dort liegen die meisten Zeilen."""
    if len(abschnitte) == 1:
        return f"CAST(({ausdruck} + {abschnitte[0][2]}) / 86400 AS INTEGER)"
    faelle = " ".join(f"WHEN {ausdruck} >= {a[0]} THEN {a[2]}" for a in reversed(abschnitte[1:]))
    return f"CAST(({ausdruck} + CASE {faelle} ELSE {abschnitte[0][2]} END) / 86400 AS INTEGER)"


def lokaler_tag(ts):
    """Lokaler Kalendertag (Tage seit 1.1.1970, Ortszeit) – passend zu tag_sql()."""
    return int((ts + time.localtime(ts).tm_gmtoff) // TAG)


def _abschnitte_fuer(ha, jetzt):
    """Versatz-Abschnitte vom ältesten Zeitstempel der Recorder-Tabellen bis heute (MIN über die Zeitindizes, schnell)."""
    von = [ha.execute(f"SELECT MIN({sp}) FROM {tab}").fetchone()[0] for tab, sp in
           (("states", "last_updated_ts"), ("statistics", "start_ts"), ("statistics_short_term", "start_ts"), ("events", "time_fired_ts"))]
    von = [v for v in von if v]
    return versatz_abschnitte(min(von) if von else jetzt, jetzt)


def oeffne_ha_db(pfad):
    """Öffnet die HA-Datenbank strikt lesend."""
    con = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA query_only = 1")
    return con


def lies_registry(pfad):
    """Liefert (aktive entity_ids, gelöschte entity_ids) aus core.entity_registry."""
    with open(pfad, encoding="utf-8") as f:
        daten = json.load(f)["data"]
    aktiv = {e["entity_id"] for e in daten.get("entities", [])}
    geloescht = {e["entity_id"] for e in daten.get("deleted_entities", [])}
    return aktiv, geloescht


def lies_registry_details(pfad):
    """Punkte 185/186: je Entität in der Registry {deaktiviert, device_class, state_class, unit}.

    Zustandslose Entitäten (deaktivierte) haben keine Attribute; die Klasse kommt dann aus der Registry – die eigene
    Einstellung device_class vor original_device_class, die state_class aus capabilities."""
    with open(pfad, encoding="utf-8") as f:
        daten = json.load(f)["data"]
    out = {e["entity_id"]: {"deaktiviert": bool(e.get("disabled_by")),
                            "device_class": e.get("device_class") or e.get("original_device_class") or "",
                            "state_class": (e.get("capabilities") or {}).get("state_class") or "",
                            "unit": e.get("unit_of_measurement") or "", "plattform": e.get("platform") or ""}
           for e in daten.get("entities", [])}
    # Punkt 196: Integration auch für gelöschte Einträge – nur dieses Feld
    for e in daten.get("deleted_entities", []):
        out.setdefault(e["entity_id"], {"plattform": e.get("platform") or ""})
    return out


# Punkt 188: Einheiten der device_class data_size → Bytes
_BYTES_JE = {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12, "PB": 1e15,
             "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40, "PiB": 2**50,
             "bit": 1 / 8, "kbit": 1e3 / 8, "Mbit": 1e6 / 8, "Gbit": 1e9 / 8}


def historie_groesse(db_pfad, registry_pfad, tage=730):
    """Punkt 188: bisherige DB-Größe aus der Langzeitstatistik des eigenen Sensors „DB-Größe“ (unique_id
    rs_db_hygiene_db_groesse), ein Wert je Tag (Mittel der Stundenwerte) über höchstens `tage` Tage.

    Liefert {entity_id, punkte: [[Tagesbeginn UTC in s, Bytes], …]} oder {entity_id, punkte: [], grund}."""
    with open(registry_pfad, encoding="utf-8") as f:
        ents = json.load(f)["data"].get("entities", [])
    eid = next((e["entity_id"] for e in ents if e.get("platform") == "rs_db_hygiene"
                and e.get("unique_id") == "rs_db_hygiene_db_groesse"), None)
    if not eid:
        return {"entity_id": None, "punkte": [], "grund": "Sensor „DB-Größe“ der Integration nicht gefunden"}
    con = oeffne_ha_db(db_pfad)
    try:
        meta = con.execute("SELECT id, unit_of_measurement FROM statistics_meta WHERE statistic_id = ?", (eid,)).fetchone()
        if not meta:
            return {"entity_id": eid, "punkte": [], "grund": "keine Langzeitstatistik"}
        faktor = _BYTES_JE.get(meta[1] or "GB")
        if not faktor:
            return {"entity_id": eid, "punkte": [], "grund": f"unbekannte Einheit {meta[1]}"}
        ab = time.time() - tage * 86400
        zeilen = con.execute("SELECT CAST(start_ts / 86400 AS INTEGER) AS tag, AVG(mean) FROM statistics "
                             "WHERE metadata_id = ? AND start_ts >= ? AND mean IS NOT NULL GROUP BY tag ORDER BY tag",
                             (meta[0], ab)).fetchall()
    finally:
        con.close()
    return {"entity_id": eid, "punkte": [[t * 86400, round(m * faktor)] for t, m in zeilen if m and m > 0]}


def hole_zustaende(url="http://supervisor/core/api/states", token=None):
    """Aktuelle Zustände über die HA-API (im Add-on mit SUPERVISOR_TOKEN)."""
    token = token or os.environ.get("SUPERVISOR_TOKEN")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def lies_purge_keep_days(config_pfad):
    """purge_keep_days aus dem recorder-Block der configuration.yaml (HA-Standard: 10)."""
    return lies_recorder(config_pfad)["purge_keep_days"]


def _ja(wert):
    return wert.lower() not in ("false", "no", "off", "0")


def lies_recorder(config_pfad):
    """Recorder-Einstellungen aus der configuration.yaml; fehlende Einträge = HA-Standard
    (purge_keep_days 10, auto_purge und auto_repack an, db_url = SQLite home-assistant_v2.db).

    Punkt 133 (0.5.3): zusätzlich auto_repack und db_url, welche Einträge gesetzt sind („gesetzt“), ob ein Block
    gefunden wurde („block“) und `recorder: !include datei.yaml` (Pfad in „include“, relativ zur configuration.yaml).
    """
    erg = {"purge_keep_days": 10, "auto_purge": True, "auto_repack": True, "db_url": None,
           "gesetzt": [], "block": False, "include": None}
    try:
        with open(config_pfad, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return erg
    inhalt = None
    m = re.search(r"^recorder:[ \t]*!include[ \t]+(\S+)[ \t]*$", text, re.M)
    if m:
        pfad = os.path.join(os.path.dirname(config_pfad), m.group(1).strip("'\""))
        erg["include"] = pfad
        try:
            with open(pfad, encoding="utf-8") as f:
                inhalt = re.sub(r"^", "  ", f.read(), flags=re.M) + "\n"   # wie ein eingerückter Block lesen
        except OSError:
            return erg
    else:
        block = re.search(r"^recorder:[ \t]*(?:#.*)?\n((?:[ \t]+.*\n|\s*\n)*)", text + "\n", re.M)
        if block:
            inhalt = block.group(1)
    if inhalt is None:
        return erg
    erg["block"] = True
    m = re.search(r"^\s+purge_keep_days:\s*(\d+)", inhalt, re.M)
    if m:
        erg["purge_keep_days"] = int(m.group(1))
        erg["gesetzt"].append("purge_keep_days")
    for k in ("auto_purge", "auto_repack"):
        m = re.search(rf"^\s+{k}:\s*(\w+)", inhalt, re.M)
        if m:
            erg[k] = _ja(m.group(1))
            erg["gesetzt"].append(k)
    m = re.search(r"^\s+db_url:\s*['\"]?([^'\"\s#]+)", inhalt, re.M)
    if m:
        erg["db_url"] = m.group(1)
        erg["gesetzt"].append("db_url")
    return erg


def _ersetze_datei(neu, ziel):
    for endung in ("-wal", "-shm"):
        if os.path.exists(ziel + endung):
            os.remove(ziel + endung)
    os.replace(neu, ziel)


# Punkt 109 (0.4.9): Fortschritt für den Herzschlag im App-Log (main._herzschlag)
FORTSCHRITT = {}
ANALYSE_SCHRITTE = ("states_meta", "states_tage", "states_24h", "statistics", "statistics_short_term", "events")


def _schritt_fertig(name, nr, dauer):
    _LOG.info("Analyse: Schritt %d/%d %s fertig (%.1f s)", nr, len(ANALYSE_SCHRITTE), name, dauer)
    if nr < len(ANALYSE_SCHRITTE):
        FORTSCHRITT.update(nr=nr + 1, schritt=ANALYSE_SCHRITTE[nr])


def analyse(db_pfad, cache_pfad, zustaende, registry, geloescht, purge_keep_days, herkunft=None, reg_details=None):
    """Vollanalyse von states, statistics und statistics_short_term (ohne Attribute).

    herkunft: Ergebnis von herkunft.ermittle() – {entity_id: {art, text, info, device_id}}.
    reg_details: Ergebnis von lies_registry_details() (ab 0.5.5b5): Status deaktiviert und Klasse ohne Zustand.
    Dauer bei 22 GB / 73 Mio. States ca. 1–2 min.
    """
    herkunft = herkunft or {}
    reg_details = reg_details or {}
    start = time.time()
    jetzt = time.time()
    heute = lokaler_tag(jetzt)        # Punkt 121: lokaler Kalendertag (bis 0.4.11 UTC-Tag)
    schritte = {}
    FORTSCHRITT.clear()
    FORTSCHRITT.update(art="analyse", nr=1, von=len(ANALYSE_SCHRITTE), schritt=ANALYSE_SCHRITTE[0])

    ha = oeffne_ha_db(db_pfad)
    abschnitte = _abschnitte_fuer(ha, jetzt)
    t = time.time()
    meta = dict(ha.execute("SELECT metadata_id, entity_id FROM states_meta"))
    schritte["states_meta"] = time.time() - t
    _schritt_fertig("states_meta", 1, schritte["states_meta"])

    t = time.time()
    st_tage = ha.execute(
        f"SELECT metadata_id, {tag_sql('last_updated_ts', abschnitte)}, COUNT(*), "
        "MIN(last_updated_ts), MAX(last_updated_ts) FROM states GROUP BY 1, 2").fetchall()
    schritte["states_tage"] = time.time() - t
    _schritt_fertig("states_tage", 2, schritte["states_tage"])

    # Punkt 192 (0.5.5b9): Zuwachs je Tag = Mittel der 7 vollen Kalendertage vor heute (fest ÷ 7, auch bei jüngeren
    # Entitäten), aus den Tageszeilen statt einer eigenen Abfrage der letzten 24 h. Feldname rows_24h bleibt.
    t = time.time()
    summe7 = {}
    for mid, tag, n, _mn, _mx in st_tage:
        if heute - 7 <= tag < heute:
            summe7[mid] = summe7.get(mid, 0) + n
    st_24h = {mid: round(n / 7) for mid, n in summe7.items()}
    schritte["states_24h"] = time.time() - t
    _schritt_fertig("states_24h", 3, schritte["states_24h"])

    t = time.time()
    lts_meta = ha.execute("SELECT id, statistic_id, source FROM statistics_meta").fetchall()
    # ab 0.4.11 (Punkte 118/120) mit exaktem erstem und letztem start_ts je Tag
    lts_tage = ha.execute(
        f"SELECT metadata_id, {tag_sql('start_ts', abschnitte)}, COUNT(*), MIN(start_ts), MAX(start_ts) FROM statistics GROUP BY 1, 2").fetchall()
    schritte["statistics"] = time.time() - t
    _schritt_fertig("statistics", 4, schritte["statistics"])

    t = time.time()
    kurz_tage = ha.execute(
        f"SELECT metadata_id, {tag_sql('start_ts', abschnitte)}, COUNT(*), MIN(start_ts), MAX(start_ts) FROM statistics_short_term GROUP BY 1, 2").fetchall()
    schritte["statistics_short_term"] = time.time() - t
    _schritt_fertig("statistics_short_term", 5, schritte["statistics_short_term"])

    t = time.time()
    events_7d = ha.execute("SELECT COUNT(*) FROM events WHERE time_fired_ts >= ?", (jetzt - 7 * TAG,)).fetchone()[0]
    ev_tage = ha.execute(
        f"SELECT t.event_type, {tag_sql('e.time_fired_ts', abschnitte)}, COUNT(*) FROM events e "
        "JOIN event_types t ON e.event_type_id = t.event_type_id GROUP BY 1, 2").fetchall()
    schritte["events"] = time.time() - t
    _schritt_fertig("events", 6, schritte["events"])
    ha.close()

    # Aggregation pro Entität
    info = {}
    for mid, tag, n, mn, mx in st_tage:
        i = info.setdefault(mid, [0, mn, mx])
        i[0] += n
        i[1] = min(i[1], mn)
        i[2] = max(i[2], mx)

    lts_id_von = {sid: lid for lid, sid, _src in lts_meta}
    lts_sum, kurz_sum = {}, {}
    lts_first, lts_last, kurz_first, kurz_last = {}, {}, {}, {}
    for lid, tag, n, mn, mx in lts_tage:          # ab 0.4.11 exakte Zeitstempel statt Tagesbeginn
        lts_sum[lid] = lts_sum.get(lid, 0) + n
        lts_first[lid] = min(lts_first.get(lid, mn), mn)
        lts_last[lid] = max(lts_last.get(lid, mx), mx)
    for lid, tag, n, mn, mx in kurz_tage:
        kurz_sum[lid] = kurz_sum.get(lid, 0) + n
        kurz_first[lid] = min(kurz_first.get(lid, mn), mn)
        kurz_last[lid] = max(kurz_last.get(lid, mx), mx)

    zust = {z["entity_id"]: z.get("attributes", {}) for z in zustaende}

    neu = cache_pfad + ".neu"
    if os.path.exists(neu):
        os.remove(neu)
    c = sqlite3.connect(neu)
    c.executescript(CACHE_SCHEMA)

    zeilen = []
    for mid, eid in meta.items():
        n, mn, mx = info.get(mid, (0, None, None))
        in_reg = eid in registry
        hat_state = eid in zust
        rd = reg_details.get(eid) or {}
        if in_reg or hat_state:
            status = "deaktiviert" if rd.get("deaktiviert") and not hat_state else "aktiv"     # Punkt 186
        elif eid in geloescht:
            status = "verwaist_geloescht"
        else:
            status = "verwaist"
        a = zust.get(eid) or {"state_class": rd.get("state_class"), "device_class": rd.get("device_class"),   # Punkt 185:
                              "unit_of_measurement": rd.get("unit")}                                         # ohne Zustand aus der Registry
        lid = lts_id_von.get(eid)
        h = herkunft.get(eid) or ({"art": "verwaist", "text": "verwaist, ohne Spur in der Registry", "info": ""}
                                  if status.startswith("verwaist") else {"art": "", "text": "", "info": ""})
        zeilen.append((
            eid, mid, eid.split(".", 1)[0], n, mn, mx, st_24h.get(mid, 0),
            int(in_reg), int(hat_state), status,
            a.get("state_class") or "", a.get("device_class") or "", a.get("unit_of_measurement") or "",
            lid, lts_sum.get(lid, 0) if lid else 0,
            lts_first.get(lid), lts_last.get(lid),
            kurz_sum.get(lid, 0) if lid else 0,
            h.get("art", ""), h.get("text", ""), h.get("info", ""), h.get("device_id"),
            kurz_first.get(lid), kurz_last.get(lid), rd.get("plattform") or ""))
    c.executemany("INSERT INTO entitaet VALUES (" + ",".join("?" * 25) + ")", zeilen)
    c.executemany("INSERT INTO st_tage VALUES (?,?,?)", ((m, t_, n) for m, t_, n, _a, _b in st_tage))
    c.executemany("INSERT INTO lts_tage VALUES (?,?,?)", ((m, t_, n) for m, t_, n, _a, _b in lts_tage))
    c.executemany("INSERT INTO kurz_tage VALUES (?,?,?)", ((m, t_, n) for m, t_, n, _a, _b in kurz_tage))

    bekannte = set(meta.values())
    ohne = [(sid, src, lts_sum.get(lid, 0), int(sid in registry))
            for lid, sid, src in lts_meta if src == "recorder" and sid not in bekannte and sid not in zust]
    c.executemany("INSERT INTO lts_ohne_entitaet VALUES (?,?,?,?)", ohne)
    c.executemany("INSERT OR REPLACE INTO ev_tage VALUES (?,?,?)", ev_tage)

    dauer = time.time() - start
    werte = {
        "analyse_zeit": jetzt, "analyse_dauer_s": round(dauer, 1), "heute_tag": heute,
        "db_bytes": os.path.getsize(db_pfad), "purge_keep_days": purge_keep_days,
        "events_pro_tag": round(events_7d / 7), "entitaeten": len(meta), "cache_version": CACHE_VERSION,
        "schritte": json.dumps({k: round(v, 1) for k, v in schritte.items()}),
    }
    c.executemany("INSERT INTO info VALUES (?,?)", ((k, str(v)) for k, v in werte.items()))
    c.commit()
    c.close()
    _ersetze_datei(neu, cache_pfad)
    _LOG.info("Analyse fertig in %.1f s: %s", dauer, werte["schritte"])
    return werte


def attribut_analyse(db_pfad, attr_pfad):
    """Attribut-Volumen pro Entität und Tag (erste/letzte Verwendung je Attributzeile).

    Schwer: liest alle 73 Mio. States über den Index ix_states_attributes_id, ca. 10 min.
    Nur auf Knopfdruck, nicht im Nachtlauf.
    """
    start = time.time()
    erwartet = None                        # Punkt 109: Zuordnungen der letzten Attribut-Analyse = ungefähre Gesamtzahl
    try:
        c0 = sqlite3.connect(f"file:{attr_pfad}?mode=ro", uri=True)
        erwartet = int(dict(c0.execute("SELECT schluessel, wert FROM info")).get("zuordnungen") or 0) or None
        c0.close()
    except (sqlite3.Error, ValueError):
        pass
    FORTSCHRITT.clear()
    FORTSCHRITT.update(art="attribute", zeilen=0, erwartet=erwartet, schritt="SQLite liest und gruppiert die States")
    _LOG.info("Attribut-Analyse gestartet%s", f" (letztes Mal {erwartet:,} Zuordnungen)".replace(",", ".") if erwartet else "")
    ha = oeffne_ha_db(db_pfad)
    abschnitte = _abschnitte_fuer(ha, time.time())          # Punkt 121: lokale Kalendertage wie die Analyse
    cur = ha.execute(
        f"SELECT s.metadata_id, {tag_sql('MIN(s.last_updated_ts)', abschnitte)}, "
        f"{tag_sql('MAX(s.last_updated_ts)', abschnitte)}, LENGTH(a.shared_attrs) "
        "FROM states s JOIN state_attributes a ON a.attributes_id = s.attributes_id "
        "GROUP BY s.attributes_id, s.metadata_id")
    agg = {}
    anzahl = 0
    for mid, erst, letzt, laenge in cur:
        anzahl += 1
        if anzahl % 20000 == 0:
            FORTSCHRITT.update(zeilen=anzahl, schritt="Zuordnungen auswerten")
        laenge = laenge or 0
        for art, tag in (("erst", erst), ("letzt", letzt)):
            k = (mid, tag, art)
            z = agg.get(k)
            if z:
                z[0] += 1
                z[1] += laenge
            else:
                agg[k] = [1, laenge]
    ha.close()
    FORTSCHRITT.update(zeilen=anzahl, schritt="Ergebnis speichern")

    neu = attr_pfad + ".neu"
    if os.path.exists(neu):
        os.remove(neu)
    c = sqlite3.connect(neu)
    c.executescript(ATTR_SCHEMA)
    c.executemany("INSERT INTO attr_tage VALUES (?,?,?,?,?)", ((k[0], k[1], k[2], v[0], v[1]) for k, v in agg.items()))
    dauer = time.time() - start
    c.executemany("INSERT INTO info VALUES (?,?)", [
        ("analyse_zeit", str(time.time())), ("analyse_dauer_s", str(round(dauer, 1))), ("zuordnungen", str(anzahl))])
    c.commit()
    c.close()
    _ersetze_datei(neu, attr_pfad)
    _LOG.info("Attribut-Analyse fertig in %.1f s (%d Zuordnungen)", dauer, anzahl)
    return {"dauer_s": round(dauer, 1), "zuordnungen": anzahl}


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--cache", required=True)
    p.add_argument("--registry", required=True)
    p.add_argument("--states", required=True, help="JSON-Datei mit /api/states")
    p.add_argument("--config", default="/homeassistant/configuration.yaml")
    p.add_argument("--attribute", help="Pfad für den Attribut-Cache (startet die schwere Attribut-Analyse)")
    arg = p.parse_args()
    import herkunft as hk
    reg, gel = lies_registry(arg.registry)
    with open(arg.states, encoding="utf-8") as f:
        zs = json.load(f)
    hku = hk.ermittle(os.path.dirname(arg.config), zs)
    print(json.dumps(analyse(arg.db, arg.cache, zs, reg, gel, lies_purge_keep_days(arg.config), hku), indent=1))
    if arg.attribute:
        print(json.dumps(attribut_analyse(arg.db, arg.attribute), indent=1))
