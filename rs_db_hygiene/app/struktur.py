"""Datenbank-Struktur (Punkt 288): Tagesstand der Recorder-Tabellen für den Reiter Dashboard (struktur.db unter /data).

Je lokalem Kalendertag ein Stand als JSON (der letzte Lauf des Tages gewinnt):
  zeilen       Zeilen je Tabelle – states, statistics, statistics_short_term und events aus den Tageszahlen der Analyse
               (kein zweiter Durchlauf), die übrigen per COUNT(*) (kleine Tabellen, Bruchteile einer Sekunde)
  bytes        Platz je Tabelle, getrennt nach Daten und Indizes, aus der virtuellen Tabelle dbstat (ca. 12 s bei 11 GB)
  datei        Seitengröße, Seiten, freie Seiten
  zeitraum     ältester und jüngster Zeitstempel der vier großen Tabellen (MIN/MAX über die Zeitindizes)
  details      Domains der Entitäten, häufigste Event-Typen und Einheiten, Attributlänge, Schema-Versionen, Läufe
  planer       Zeilenzahlen aus sqlite_stat1 (Statistik des Abfrageplaners) zum Vergleich mit den echten Zahlen

Wie zuwachs.db wird die Datei nur fortgeschrieben; Stände älter als die Speicherdauer der Zuwachsdaten entfallen.
Nur Standardbibliothek; die HA-Datenbank wird nur lesend benutzt (Verbindung kommt von der Analyse oder per --db).

Test ohne Add-on:  python3 struktur.py --db /homeassistant/home-assistant_v2.db --ziel /tmp/struktur.db
"""
import json
import logging
import os
import sqlite3
import time

import zuwachs

_LOG = logging.getLogger("struktur")
TABELLEN = ("states_meta", "states", "state_attributes", "event_types", "events", "event_data", "statistics_meta",
            "statistics", "statistics_short_term", "recorder_runs", "statistics_runs", "schema_changes", "migration_changes")
# Zeitspalte mit Index je großer Tabelle
ZEITSPALTE = {"states": "last_updated_ts", "events": "time_fired_ts", "statistics": "start_ts", "statistics_short_term": "start_ts"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS info (schluessel TEXT PRIMARY KEY, wert TEXT);
CREATE TABLE IF NOT EXISTS stand (tag INTEGER PRIMARY KEY, zeit REAL NOT NULL, daten TEXT NOT NULL);
"""


def _bytes(ha):
    """Platz je Tabelle aus dbstat: {tabelle: {daten, indizes, index: {name: bytes}}} – Indizes ihrer Tabelle zugeordnet."""
    zu = {n: t for n, t in ha.execute("SELECT name, tbl_name FROM sqlite_schema WHERE type IN ('table', 'index')")}
    out = {}
    for name, groesse in ha.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"):
        tab = zu.get(name, name)
        e = out.setdefault(tab, {"daten": 0, "indizes": 0, "index": {}})
        if name == tab:
            e["daten"] += groesse
        else:
            e["indizes"] += groesse
            e["index"][name] = groesse
    return out


def _planer(ha):
    """Zeilenzahl je Tabelle laut sqlite_stat1 (erste Zahl der Spalte stat); leer, wenn nie ANALYZE lief."""
    try:
        zeilen = ha.execute("SELECT tbl, MAX(CAST(stat AS INTEGER)) FROM sqlite_stat1 GROUP BY tbl").fetchall()
    except sqlite3.Error:
        return {}
    return {t: n for t, n in zeilen if n}


def erfasse(ha, ziel_pfad, zeilen=None, extra=None, monate=None):
    """Stand der Datenbank erfassen und in ziel_pfad fortschreiben. ha: lesende Verbindung zur HA-Datenbank.

    zeilen: bereits bekannte Zeilenzahlen (z. B. aus der Analyse); fehlende Tabellen werden gezählt.
    extra: zusätzliche Werte für details (z. B. Zahl der Statistik-IDs mit 5-Min-Werten).
    Liefert den Stand als dict."""
    start = time.time()
    zeilen = dict(zeilen or {})
    vorhanden = {n for (n,) in ha.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
    for t in TABELLEN:
        if t in vorhanden and t not in zeilen:
            zeilen[t] = ha.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    zeitraum = {}
    for t, sp in ZEITSPALTE.items():
        if t in vorhanden:
            zeitraum[t] = list(ha.execute(f"SELECT MIN({sp}), MAX({sp}) FROM {t}").fetchone())

    d = dict(extra or {})
    domains = {}
    for (eid,) in ha.execute("SELECT entity_id FROM states_meta"):
        dom = (eid or "").split(".", 1)[0]
        domains[dom] = domains.get(dom, 0) + 1
    d["domains"] = sorted(domains.items(), key=lambda x: -x[1])
    d["event_typen"] = ha.execute(
        "SELECT t.event_type, COUNT(e.event_id) n FROM event_types t LEFT JOIN events e ON e.event_type_id = t.event_type_id "
        "GROUP BY t.event_type_id ORDER BY n DESC").fetchall()
    d["einheiten"] = ha.execute("SELECT COALESCE(unit_of_measurement, ''), COUNT(*) FROM statistics_meta GROUP BY 1 "
                                "ORDER BY 2 DESC LIMIT 6").fetchall()
    d["mit_sum"] = ha.execute("SELECT COUNT(*) FROM statistics_meta WHERE has_sum").fetchone()[0]
    d["quellen"] = ha.execute("SELECT source, COUNT(*) FROM statistics_meta GROUP BY 1 ORDER BY 2 DESC").fetchall()
    d["attr_laenge"] = list(ha.execute("SELECT AVG(LENGTH(shared_attrs)), MAX(LENGTH(shared_attrs)) FROM state_attributes").fetchone())
    d["schema"] = ha.execute("SELECT schema_version, changed FROM schema_changes ORDER BY change_id").fetchall()
    d["recorder_unsauber"] = ha.execute("SELECT COUNT(*) FROM recorder_runs WHERE closed_incorrect").fetchone()[0]
    d["max_state_id"] = ha.execute("SELECT MAX(state_id) FROM states").fetchone()[0]

    t_dbstat = time.time()
    try:
        groessen = _bytes(ha)
    except sqlite3.Error as e:                     # dbstat fehlt in manchen SQLite-Builds – dann ohne Platz je Tabelle
        _LOG.warning("dbstat nicht verfügbar: %s", e)
        groessen = {}
    t_dbstat = time.time() - t_dbstat

    seite = ha.execute("PRAGMA page_size").fetchone()[0]
    datei = {"seite": seite, "seiten": ha.execute("PRAGMA page_count").fetchone()[0],
             "frei": ha.execute("PRAGMA freelist_count").fetchone()[0]}
    jetzt = time.time()
    stand = {"zeit": jetzt, "zeilen": zeilen, "bytes": groessen, "datei": datei, "zeitraum": zeitraum,
             "details": d, "planer": _planer(ha), "dauer_s": round(jetzt - start, 1), "dbstat_s": round(t_dbstat, 1)}

    heute = int((jetzt + time.localtime(jetzt).tm_gmtoff) // 86400)      # lokaler Kalendertag wie analyse.lokaler_tag
    con = sqlite3.connect(ziel_pfad)
    try:
        con.executescript(SCHEMA)
        con.execute("INSERT OR REPLACE INTO stand VALUES (?,?,?)", (heute, jetzt, json.dumps(stand, separators=(",", ":"))))
        grenze = zuwachs.grenze_tag(heute, zuwachs.ZUWACHS_MONATE if monate is None else monate)
        if grenze is not None:
            con.execute("DELETE FROM stand WHERE tag < ?", (grenze,))
        con.commit()
    finally:
        con.close()
    _LOG.info("Struktur erfasst in %.1f s (dbstat %.1f s)", stand["dauer_s"], t_dbstat)
    return stand


def lies(pfad):
    """Letzter Stand und der Stand davor (für die Veränderung): {stand, vorher, tage}; stand None ohne Daten."""
    if not os.path.exists(pfad):
        return {"stand": None, "vorher": None, "tage": 0}
    con = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
    try:
        zeilen = con.execute("SELECT tag, daten FROM stand ORDER BY tag DESC LIMIT 2").fetchall()
        tage = con.execute("SELECT COUNT(*) FROM stand").fetchone()[0]
    finally:
        con.close()
    erg = {"stand": None, "vorher": None, "tage": tage}
    if zeilen:
        erg["stand"] = {"tag": zeilen[0][0], **json.loads(zeilen[0][1])}
    if len(zeilen) > 1:
        erg["vorher"] = {"tag": zeilen[1][0], **json.loads(zeilen[1][1])}
    return erg


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--ziel", required=True)
    arg = p.parse_args()
    verbindung = sqlite3.connect(f"file:{arg.db}?mode=ro", uri=True, timeout=60)
    verbindung.execute("PRAGMA query_only = 1")
    s = erfasse(verbindung, arg.ziel)
    verbindung.close()
    print(json.dumps({k: s[k] for k in ("zeilen", "datei", "dauer_s", "dbstat_s")}, indent=1))
