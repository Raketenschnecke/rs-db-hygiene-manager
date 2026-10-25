"""Punkt 312: gespeicherte Werte einer Entität (Chart im Entitäten-Dialog, Umschalter „Zuwachs / Werte“).

Liest strikt lesend aus der HA-Datenbank je eine Reihe für States, 5-Min-Statistik (statistics_short_term) und Langzeit
(statistics). Die Reihen werden in der Datenbank auf höchstens `punkte` Zeitfenster verdichtet (Mittelwert je Fenster), damit
das Chart auch bei Hunderttausenden Zeilen schnell bleibt. Statistikwert: mean, bei Zählern (ohne mean) der Zählerstand `state`.
"""
import time

import analyse

# nur Zeichen, die in einer Zahl vorkommen – „unknown“, „on“ usw. fallen heraus
_ZAHL = "state GLOB '*[0-9]*' AND state NOT GLOB '*[^0-9.eE+-]*'"


def _statistik(con, tabelle, meta_id, ab, fenster):
    if meta_id is None:
        return {"zeilen": 0, "punkte": []}
    zeilen = con.execute(
        f"SELECT CAST(start_ts / ? AS INTEGER) AS f, AVG(COALESCE(mean, state)), COUNT(*) FROM {tabelle} "
        "WHERE metadata_id = ? AND start_ts >= ? AND COALESCE(mean, state) IS NOT NULL GROUP BY f ORDER BY f",
        (fenster, meta_id, ab)).fetchall()
    return _reihe(zeilen, fenster)


def _reihe(zeilen, fenster):
    return {"zeilen": sum(z[2] for z in zeilen),
            "punkte": [[round((f + .5) * fenster), round(v, 4)] for f, v, _n in zeilen]}


def lies(db_pfad, entity_id, tage=90, punkte=400):
    """{entity_id, einheit, von, bis, fenster (s), reihen: {states, kurz, lts: {zeilen, punkte: [[ts, wert], …]}}}.
    tage = 0: alles, was die Datenbank noch hat."""
    jetzt = time.time()
    ab = jetzt - tage * 86400 if tage else 0
    con = analyse.oeffne_ha_db(db_pfad)
    try:
        if not ab:   # „alles“: frühester vorhandener Zeitstempel der drei Quellen
            fr = [x for x in (
                con.execute("SELECT MIN(last_updated_ts) FROM states s JOIN states_meta m ON m.metadata_id = s.metadata_id "
                            "WHERE m.entity_id = ?", (entity_id,)).fetchone()[0],
                con.execute("SELECT MIN(start_ts) FROM statistics s JOIN statistics_meta m ON m.id = s.metadata_id "
                            "WHERE m.statistic_id = ?", (entity_id,)).fetchone()[0]) if x]
            ab = min(fr) if fr else jetzt - 86400
        fenster = max(60, int((jetzt - ab) / punkte))
        meta = con.execute("SELECT id, unit_of_measurement FROM statistics_meta WHERE statistic_id = ?", (entity_id,)).fetchone()
        sm = con.execute("SELECT metadata_id FROM states_meta WHERE entity_id = ?", (entity_id,)).fetchone()
        st = {"zeilen": 0, "punkte": []}
        if sm:
            st = _reihe(con.execute(
                f"SELECT CAST(last_updated_ts / ? AS INTEGER) AS f, AVG(CAST(state AS REAL)), COUNT(*) FROM states "
                f"WHERE metadata_id = ? AND last_updated_ts >= ? AND {_ZAHL} GROUP BY f ORDER BY f",
                (fenster, sm[0], ab)).fetchall(), fenster)
        mid = meta[0] if meta else None
        return {"entity_id": entity_id, "einheit": meta[1] if meta else None, "von": round(ab), "bis": round(jetzt),
                "fenster": fenster,
                "reihen": {"states": st, "kurz": _statistik(con, "statistics_short_term", mid, ab, fenster),
                           "lts": _statistik(con, "statistics", mid, ab, fenster)}}
    finally:
        con.close()
