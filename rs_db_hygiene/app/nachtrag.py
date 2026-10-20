"""Nachtrag der Zuwachs-Statistik aus einer älteren Kopie der Recorder-Datenbank (Punkt 294).

Die zuwachs.db erfasst beim ersten Lauf nur, was die Recorder-Datenbank noch hält; Tage, deren Zeilen die App oder der
Recorder schon gelöscht hatten, stehen zu niedrig oder gar nicht drin. Eine Kopie der Datenbank von vorher (etwa aus
einem Backup) enthält diese Tage noch vollständig. Dieses Skript zählt darin je vollem lokalem Kalendertag dieselben
Werte wie die Analyse und schreibt sie in die zuwachs.db:

  states, kurz, lts  wie analyse.py (st_tage, kurz_tage, lts_tage); kurz/lts nur für Statistik-IDs, die auch Entitäten sind
  events             je Event-Typ unter ev:<Event-Typ>
  attr               wie zuwachs._attribute: neue attributes_id je Tag und Entität, Grenze = höchste Id vor dem ersten Tag

Der erste und der letzte Tag der Kopie werden nicht übernommen (angeschnitten). Geschrieben wird mit MAX wie beim
Fortschreiben – ein Wert wird nie kleiner. Die Abdeckung (info: <quelle>_von) wird bis zum ersten nachgetragenen Tag
vorgezogen; liegt zwischen dem Nachtrag und der bisherigen Abdeckung eine Lücke, kommt sie in <quelle>_luecken und die
GUI zeigt dort keinen Wert statt 0. attr_bis und attr_grenze bleiben unverändert, die laufende Erfassung geht normal weiter.

Die Kopie wird nur lesend geöffnet. Vorher die zuwachs.db sichern; mit --probe wird nichts geschrieben.

  python3 nachtrag.py --alt "/homeassistant/home-assistant_v2 bis 20261010.db" --ziel /data/zuwachs.db [--probe]
"""
import argparse
import json
import logging
import sqlite3
import time
import urllib.parse

import zuwachs
from analyse import lokaler_tag, tag_sql, versatz_abschnitte

_LOG = logging.getLogger("nachtrag")


def _oeffne(pfad):
    con = sqlite3.connect(f"file:{urllib.parse.quote(pfad)}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA query_only = 1")
    return con


def _volle_tage(ha, tabelle, spalte):
    """Erster und letzter volle lokale Kalendertag einer Tabelle (ohne die angeschnittenen Randtage); None ohne Zeilen."""
    mn, mx = ha.execute(f"SELECT MIN({spalte}), MAX({spalte}) FROM {tabelle}").fetchone()
    if mn is None:
        return None
    von, bis = lokaler_tag(mn) + 1, lokaler_tag(mx) - 1
    return (von, bis) if von <= bis else None


def zaehle(ha):
    """Tageswerte der Kopie: {quelle: (von, bis, [(kennung, tag, n), …])}."""
    meta = dict(ha.execute("SELECT metadata_id, entity_id FROM states_meta"))
    entitaeten = set(meta.values())
    lts_meta = {lid: sid for lid, sid in ha.execute("SELECT id, statistic_id FROM statistics_meta") if sid in entitaeten}
    erg = {}
    for quelle, tabelle, spalte in (("states", "states", "last_updated_ts"), ("kurz", "statistics_short_term", "start_ts"),
                                    ("lts", "statistics", "start_ts"), ("events", "events", "time_fired_ts")):
        t = time.time()
        b = _volle_tage(ha, tabelle, spalte)
        if not b:
            continue
        von, bis = b
        a, e = zuwachs.tag_beginn(von), zuwachs.tag_beginn(bis + 1)
        ab = versatz_abschnitte(a, e)
        if quelle == "events":
            zeilen = ha.execute(f"SELECT 'ev:' || t.event_type, {tag_sql('e.time_fired_ts', ab)}, COUNT(*) FROM events e "
                                "JOIN event_types t ON e.event_type_id = t.event_type_id "
                                "WHERE e.time_fired_ts >= ? AND e.time_fired_ts < ? GROUP BY 1, 2", (a, e)).fetchall()
        else:
            roh = ha.execute(f"SELECT metadata_id, {tag_sql(spalte, ab)}, COUNT(*) FROM {tabelle} "
                             f"WHERE {spalte} >= ? AND {spalte} < ? GROUP BY 1, 2", (a, e)).fetchall()
            namen = meta if quelle == "states" else lts_meta
            zeilen = [(namen[m], tag, n) for m, tag, n in roh if m in namen]
        erg[quelle] = (von, bis, zeilen)
        _LOG.info("%s: %s bis %s, %d Tageswerte, %d Zeilen (%.0f s)", quelle, zuwachs.tag_datum(von), zuwachs.tag_datum(bis),
                  len(zeilen), sum(z[2] for z in zeilen), time.time() - t)

    if "states" in erg:          # attr wie zuwachs._attribute, Tag für Tag
        t = time.time()
        von, bis, _ = erg["states"]
        grenze = ha.execute("SELECT MAX(attributes_id) FROM states WHERE last_updated_ts < ?",
                            (zuwachs.tag_beginn(von),)).fetchone()[0] or 0
        zeilen = []
        for tag in range(von, bis + 1):
            roh = ha.execute(
                "SELECT metadata_id, COUNT(DISTINCT attributes_id), MAX(attributes_id) FROM states "
                "WHERE last_updated_ts >= ? AND last_updated_ts < ? AND attributes_id > ? GROUP BY metadata_id",
                (zuwachs.tag_beginn(tag), zuwachs.tag_beginn(tag + 1), grenze)).fetchall()
            zeilen += [(meta[m], tag, n) for m, n, _mx in roh if m in meta]
            grenze = max([grenze] + [mx for _m, _n, mx in roh])
        erg["attr"] = (von, bis, zeilen)
        _LOG.info("attr: %d Tageswerte, %d Zeilen (%.0f s)", len(zeilen), sum(z[2] for z in zeilen), time.time() - t)
    return erg


def schreibe(ziel, erg, probe=False):
    """Tageswerte mit MAX übernehmen, Abdeckung vorziehen, Lücken vermerken. Liefert je Quelle einen kurzen Bericht."""
    con = sqlite3.connect(ziel, timeout=60)
    try:
        con.executescript(zuwachs.SCHEMA)
        zuwachs._migriere(con)
        info = zuwachs._info_lesen(con)
        bericht = {}
        for quelle, (von, bis, zeilen) in erg.items():
            alt_von, alt_bis = info.get(f"{quelle}_von"), info.get(f"{quelle}_bis")
            luecken = json.loads(info.get(f"{quelle}_luecken") or "[]")
            vorher = dict(((k, t), n) for k, t, n in con.execute(
                f"SELECT entity_id, tag, {quelle} FROM tage WHERE tag BETWEEN ? AND ?", (von, bis)))
            hoeher = sum(1 for k, t, n in zeilen if n > vorher.get((k, t), 0))
            plus = sum(max(0, n - vorher.get((k, t), 0)) for k, t, n in zeilen)
            neu = {}
            if alt_von is None:
                neu = {f"{quelle}_von": von, f"{quelle}_bis": bis}
            else:
                alt_von, alt_bis = int(alt_von), int(alt_bis)
                if von < alt_von:
                    neu[f"{quelle}_von"] = von
                    if bis + 1 < alt_von:                 # Lücke zwischen Nachtrag und bisheriger Abdeckung
                        luecken.append([bis + 1, alt_von - 1])
                        neu[f"{quelle}_luecken"] = json.dumps(sorted(luecken))
            bericht[quelle] = {"von": zuwachs.tag_datum(von), "bis": zuwachs.tag_datum(bis), "tageswerte": len(zeilen),
                               "erhoeht": hoeher, "zeilen_mehr": plus, "info": neu}
            if not probe:
                zuwachs._schreibe(con, quelle, zeilen)
                if neu:
                    zuwachs._info_setzen(con, **neu)
        if not probe:
            zuwachs._info_setzen(con, nachtrag=json.dumps({"zeit": time.time(), "quellen": list(erg)}))
            con.commit()
        return bericht
    finally:
        con.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description="Zuwachs-Statistik aus einer älteren Kopie der Recorder-Datenbank nachtragen")
    p.add_argument("--alt", required=True, help="Kopie der Recorder-Datenbank (wird nur gelesen)")
    p.add_argument("--ziel", required=True, help="zuwachs.db, in die nachgetragen wird")
    p.add_argument("--quellen", default="states,kurz,lts,events,attr")
    p.add_argument("--probe", action="store_true", help="nur zählen und berichten, nichts schreiben")
    arg = p.parse_args()
    ha = _oeffne(arg.alt)
    try:
        werte = zaehle(ha)
    finally:
        ha.close()
    werte = {q: w for q, w in werte.items() if q in arg.quellen.split(",")}
    print(json.dumps(schreibe(arg.ziel, werte, arg.probe), indent=1, ensure_ascii=False))
