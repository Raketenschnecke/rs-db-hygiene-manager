"""Aufschlüsselung der Events je Typ (Drilldown im Reiter Events): welcher Dienst, welche Automation … treibt einen Event-Typ?

Nur lesend auf der Recorder-DB. Die Nutzdaten eines Events liegen dedupliziert in event_data.shared_data (JSON); die Abfrage zählt
deshalb zuerst die Events je data_id im Fenster und wertet das JSON erst danach für die verschiedenen data_id aus (nicht je Event).

Stufe 1: Untergruppen eines Typs (call_service → domain.service, automation_triggered/script_started → entity_id).
Stufe 2 (nur call_service): Parameter eines Dienstes (entity_id, topic oder Beginn der Meldung).
Spalten: Ø je Tag der letzten (bis zu) 7 vollen Tage, gestern, Ø der Tage davor (Vergleich für Ausreißer wie 0 → 49.000 an einem Tag).
Hält die Vorhaltezeit des Typs weniger als 7 Tage (z. B. call_service 7 d), zählen nur die noch vollständigen Tage;
ohne Vergleichstage ist „davor“ None.
"""
import time
from datetime import date, datetime, time as uhr, timedelta

import analyse

MAX_ZEILEN = 50                 # Untergruppen je Antwort, der Rest wird als „weitere“ zusammengefasst

DIENST = "json_extract(d.shared_data, '$.domain') || '.' || json_extract(d.shared_data, '$.service')"
# Typ → (Ausdruck der Untergruppe, Ausdruck der Parameter-Stufe oder None)
TYPEN = {
    "call_service": (DIENST,
                     "COALESCE(json_extract(d.shared_data, '$.service_data.entity_id'), json_extract(d.shared_data, '$.service_data.topic'), "
                     "substr(json_extract(d.shared_data, '$.service_data.message'), 1, 80), "
                     "json_extract(d.shared_data, '$.service_data.device_id'), '(ohne Parameter)')"),
    "automation_triggered": ("COALESCE(json_extract(d.shared_data, '$.entity_id'), json_extract(d.shared_data, '$.name'))", None),
    "script_started": ("COALESCE(json_extract(d.shared_data, '$.entity_id'), json_extract(d.shared_data, '$.name'))", None),
    "logbook_entry": ("COALESCE(json_extract(d.shared_data, '$.domain'), '(ohne Domain)')", None),
}


def unterstuetzt():
    """{Event-Typ: hat Stufe 2} – damit die Oberfläche weiß, wo der Aufklapp-Pfeil erscheint."""
    return {t: v[1] is not None for t, v in TYPEN.items()}


def _grenzen(frueh, jetzt=None):
    """(heute 0 Uhr, gestern 0 Uhr, Start, Tage) als Zeitstempel (lokale Zeit): Start = älteste Mitternacht, ab der der Typ noch
    vollständige Tage hat, höchstens 7 Tage vor heute; Tage = Zahl der vollen Tage von Start bis heute 0 Uhr."""
    heute = datetime.combine(date.fromtimestamp(jetzt or time.time()), uhr.min)
    start = heute - timedelta(days=7)
    if frueh:
        m = datetime.combine(date.fromtimestamp(frueh), uhr.min)
        if m.timestamp() < frueh:
            m += timedelta(days=1)
        start = max(start, m)
    return int(heute.timestamp()), int((heute - timedelta(days=1)).timestamp()), int(start.timestamp()), (heute - start).days


def lies(db_pfad, typ, schluessel=None):
    """Stufe 1 (schluessel None) oder Stufe 2 (Parameter des Dienstes `schluessel`, nur call_service).
    {typ, stufe, schluessel, stand, gestern_tag, tage, zeilen: [{name, n7, pro_tag, gestern, davor_pro_tag}], weitere, gesamt, hat_stufe2}"""
    if typ not in TYPEN:
        raise ValueError("Für diesen Event-Typ gibt es keine Aufschlüsselung")
    schl_sql, detail_sql = TYPEN[typ]
    if schluessel is not None and detail_sql is None:
        raise ValueError("Für diesen Event-Typ gibt es keine zweite Stufe")
    gruppe_sql = schl_sql if schluessel is None else detail_sql
    con = analyse.oeffne_ha_db(db_pfad)
    try:
        frueh = con.execute("SELECT MIN(e.time_fired_ts) FROM events e JOIN event_types t ON t.event_type_id = e.event_type_id "
                            "WHERE t.event_type = ?", (typ,)).fetchone()[0]
        heute, gestern, start, tage = _grenzen(frueh)
        sql = (
            "SELECT g, SUM(n7), SUM(ng), SUM(nd) FROM ("
            f" SELECT {gruppe_sql} AS g, x.n7 n7, x.ng ng, x.nd nd FROM ("
            "  SELECT e.data_id AS data_id,"
            "   COUNT(*) n7,"                          # volle Tage von Start bis heute 0 Uhr (inkl. gestern)
            "   SUM(e.time_fired_ts >= ?) ng,"         # gestern
            "   SUM(e.time_fired_ts < ?) nd"           # die Tage davor (Start bis gestern 0 Uhr)
            "  FROM events e JOIN event_types t ON t.event_type_id = e.event_type_id"
            "  WHERE t.event_type = ? AND e.time_fired_ts >= ? AND e.time_fired_ts < ? GROUP BY e.data_id) x"
            " LEFT JOIN event_data d ON d.data_id = x.data_id"
            + (f" WHERE ({schl_sql}) = ?" if schluessel is not None else "") +
            ") GROUP BY g ORDER BY SUM(n7) DESC")
        par = [gestern, gestern, typ, start, heute] + ([schluessel] if schluessel is not None else [])
        roh = con.execute(sql, par).fetchall()
    finally:
        con.close()

    def zeile(name, n7, ng, nd):
        n7, ng, nd = n7 or 0, ng or 0, nd or 0
        return {"name": name if name is not None else "(ohne Nutzdaten)", "n7": n7, "gestern": ng,
                "pro_tag": round(n7 / tage) if tage else None,
                "davor_pro_tag": round(nd / (tage - 1)) if tage > 1 else None,    # None: keine Vergleichstage
                "davor_summe": nd}
    alle = [zeile(*r) for r in roh]
    gesamt = zeile("gesamt", *(sum(r[i] or 0 for r in roh) for i in (1, 2, 3)))
    rest = roh[MAX_ZEILEN:]
    weitere = None
    if rest:
        weitere = zeile(f"{len(rest)} weitere", *(sum(r[i] or 0 for r in rest) for i in (1, 2, 3)))
        weitere["anzahl"] = len(rest)
    return {"typ": typ, "stufe": 1 if schluessel is None else 2, "schluessel": schluessel, "stand": int(time.time()),
            "gestern_tag": gestern, "tage": tage, "zeilen": alle[:MAX_ZEILEN], "weitere": weitere, "gesamt": gesamt,
            "hat_stufe2": detail_sql is not None}
