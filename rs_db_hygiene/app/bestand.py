"""Bestand je Entität und Tag (Punkt 334, bestand.db unter /data) – das Gegenstück zur Zuwachs-Statistik.

Je lokalem Kalendertag, an dem eine Analyse lief, und Entität werden fünf Zahlen festgehalten (wie in zuwachs.db, aber als Stand, nicht als Zuwachs):
  states  Zeilen in states            (Analyse-Cache, entitaet.rows)
  kurz    Zeilen in statistics_short_term (kurz_rows)
  lts     Zeilen in statistics        (lts_rows)
  attr    Attributzeilen, die der Entität zugeordnet sind (Attribut-Analyse, Summe der „letzt“-Zeilen); nur gemessen, nie zurückgerechnet
  events  Zeilen in events je Event-Typ, Kennung ev:<Event-Typ> (Summe ev_tage)
Der letzte Lauf eines Tages gewinnt. Diese Tage sind gemessen („echt“).

Rückfüllung: Beim ersten Lauf werden die Tage davor aus den Tageswerten der Zuwachs-Statistik zurückgerechnet („geschätzt“, info echt_von
trennt beides). Ausgangspunkt ist der heutige Stand je Entität:
  Langzeit und Entitäten, bei denen innerhalb der Zuwachs-Abdeckung noch nichts gelöscht wurde: Stand heute minus alles, was danach dazukam.
  Übrige (States, 5-Min, Attribute, Events mit Löschungen): Summe der neuen Zeilen der letzten K Tage, K = Alter der ältesten heute noch
  vorhandenen Zeile (die wirksame Vorhaltezeit von heute). Reicht die Abdeckung dafür nicht zurück, gilt die erste Rechnung.
Frühere Vorhaltezeiten und Löschungen sind nirgends gespeichert; die Rückrechnung zeigt also, wie der Bestand mit der heutigen Vorhaltezeit gewesen wäre.

Dichte (Platz): die letzten DICHT_TAGE Tage täglich, ältere nur als Sonntag und Monatsletzter. Die Speicherdauer ist die der Zuwachs-Statistik.
Die HA-Datenbank wird nicht berührt; gelesen werden nur die Analyse-Dateien und zuwachs.db. Nur Standardbibliothek.
"""
import logging
import os
import sqlite3
import time

import zuwachs

_LOG = logging.getLogger("bestand")
QUELLEN = zuwachs.QUELLEN                 # states, kurz, lts, attr, events
DICHT_TAGE = 90                           # so viele Tage täglich, ältere nur noch Sonntag und Monatsletzter
KURZ_FENSTER = 1                          # Mindestfenster der Rückrechnung in Tagen
ANLAUF_TAGE = 30                          # Quellen mit Löschungen werden erst ab so vielen Tagen nach Beginn der Zuwachs-Abdeckung zurückgerechnet (volle Fenster)

SCHEMA = zuwachs.SCHEMA


def _raster(tag, heute):
    """Bleibt dieser Tag in der Dichte erhalten?"""
    if heute - tag <= DICHT_TAGE:
        return True
    return (tag + 3) % 7 == 6 or zuwachs.tag_datum(tag + 1).endswith("-01")      # Sonntag oder Monatsletzter


def _stand_lesen(cache_pfad, attr_pfad):
    """Heutiger Stand je Entität: ({entity_id: [states, kurz, lts, attr, events]}, {entity_id: (erster Tag states, erster Tag kurz)}).
    Events (Kennung ev:) tragen ihren ersten Tag in der Spalte states des zweiten Wörterbuchs."""
    cache = sqlite3.connect(f"file:{cache_pfad}?mode=ro", uri=True)
    try:
        stand, erster = {}, {}
        st_min = dict(cache.execute("SELECT metadata_id, MIN(tag) FROM st_tage GROUP BY metadata_id"))
        kz_min = dict(cache.execute("SELECT lts_id, MIN(tag) FROM kurz_tage GROUP BY lts_id"))
        mid_eid = {}
        for eid, mid, lid, rows, kurz, lts in cache.execute(
                "SELECT entity_id, metadata_id, lts_id, rows, kurz_rows, lts_rows FROM entitaet"):
            stand[eid] = [rows or 0, kurz or 0, lts or 0, 0, 0]
            erster[eid] = (st_min.get(mid), kz_min.get(lid))
            if mid is not None:
                mid_eid[mid] = eid
        try:
            for typ, summe, ab in cache.execute("SELECT event_type, SUM(n), MIN(tag) FROM ev_tage GROUP BY event_type"):
                stand["ev:" + typ] = [0, 0, 0, 0, summe or 0]
                erster["ev:" + typ] = (ab, None)
        except sqlite3.OperationalError:      # Cache ohne ev_tage (alter Aufbau)
            pass
    finally:
        cache.close()
    if attr_pfad and os.path.exists(attr_pfad):
        try:
            ac = sqlite3.connect(f"file:{attr_pfad}?mode=ro", uri=True)
            try:
                for mid, n in ac.execute("SELECT metadata_id, SUM(zeilen) FROM attr_tage WHERE art = 'letzt' GROUP BY metadata_id"):
                    if mid in mid_eid:
                        stand[mid_eid[mid]][3] = n or 0
            finally:
                ac.close()
        except sqlite3.Error:
            _LOG.exception("Attribut-Stand konnte nicht gelesen werden")
    return stand, erster


def _rueckfuellen(con, stand, erster, zuwachs_pfad, heute):
    """Tage vor heute aus den Zuwachs-Tageswerten zurückrechnen (siehe Modulkopf). Liefert {quelle: erster rückgefüllter Tag}."""
    zc = sqlite3.connect(f"file:{zuwachs_pfad}?mode=ro", uri=True)
    try:
        zinfo = zuwachs._info_lesen(zc)
        abd = {}
        for i, q in enumerate(QUELLEN):
            try:
                von, bis = int(zinfo[f"{q}_von"]), int(zinfo[f"{q}_bis"])
            except (KeyError, ValueError):
                continue
            if von <= bis:
                abd[q] = (i, von, min(bis, heute - 1))
        if not abd:
            return {}
        abd.pop("attr", None)      # Attribute: „neu benutzte Zeilen“ (Zuwachs) und „zugeordnete Zeilen“ (Bestand) lassen sich nicht ineinander umrechnen
        gitter = {q: [d for d in range(v if q == "lts" else v + ANLAUF_TAGE, b + 1) if _raster(d, heute)] for q, (_i, v, b) in abd.items()}
        for eid, s in stand.items():
            zeilen = {z[0]: z[1:] for z in zc.execute("SELECT tag, states, kurz, lts, attr, events FROM tage WHERE entity_id = ?", (eid,))}
            tage = {}
            for q, (i, von, bis) in abd.items():
                anker = s[i]
                z = {d: v[i] for d, v in zeilen.items() if v[i]}
                if not anker and not z:
                    continue
                # Präfixsummen über von..bis
                pre = [0] * (bis - von + 2)
                for d in range(von, bis + 1):
                    pre[d - von + 1] = pre[d - von] + z.get(d, 0)
                summe = lambda a, b: pre[b - von + 1] - pre[a - von] if b >= a else 0     # noqa: E731
                ab = None if q == "lts" else erster[eid][1 if q == "kurz" else 0]      # ältester heute noch vorhandener Tag
                fenster = None
                if ab is not None and ab > von + 1:
                    fenster = max(KURZ_FENSTER, heute - ab)
                for d in gitter[q]:
                    if fenster is not None and d - fenster + 1 >= von:
                        wert = summe(d - fenster + 1, d)
                    else:
                        wert = max(0, anker - summe(d + 1, bis))
                    if wert:
                        tage.setdefault(d, [0, 0, 0, 0, 0])[i] = wert
            if tage:
                con.executemany("INSERT OR REPLACE INTO tage (entity_id, tag, states, kurz, lts, attr, events) VALUES (?,?,?,?,?,?,?)",
                                ((eid, d, *w) for d, w in tage.items()))
    finally:
        zc.close()
    return {q: gitter[q][0] for q in abd if gitter.get(q)}


def _verdichte_und_beschneide(con, heute, monate):
    """Tage außerhalb der Dichte entfernen, dann alles vor der Speicherdauer; Abdeckung der Quellen kürzen."""
    grenze = zuwachs.grenze_tag(heute, monate)
    weg = 0
    if grenze is not None:
        weg += con.execute("DELETE FROM tage WHERE tag < ?", (grenze,)).rowcount
    tage = [t for (t,) in con.execute("SELECT DISTINCT tag FROM tage WHERE tag < ?", (heute - DICHT_TAGE,))]
    for t in tage:
        if not _raster(t, heute):
            weg += con.execute("DELETE FROM tage WHERE tag = ?", (t,)).rowcount
    info = zuwachs._info_lesen(con)
    if grenze is not None:
        for q in QUELLEN:
            if f"{q}_von" in info and int(info[f"{q}_von"]) < grenze:
                zuwachs._info_setzen(con, **{f"{q}_von": grenze})
    return weg


def erfasse(bestand_pfad, cache_pfad, attr_pfad, zuwachs_pfad, heute, monate=zuwachs.ZUWACHS_MONATE):
    """Nach einer Analyse aufrufen: den heutigen Stand festhalten (beim ersten Mal zusätzlich die Rückfüllung)."""
    start = time.time()
    con = sqlite3.connect(bestand_pfad, timeout=60)
    try:
        con.executescript(SCHEMA)
        info = zuwachs._info_lesen(con)
        stand, erster = _stand_lesen(cache_pfad, attr_pfad)
        mit_attr = any(s[3] for s in stand.values())
        neu = {}
        if "echt_von" not in info and zuwachs_pfad and os.path.exists(zuwachs_pfad):
            try:
                neu = _rueckfuellen(con, stand, erster, zuwachs_pfad, heute)
                _LOG.info("Bestand: Rückfüllung %s", {q: zuwachs.tag_datum(t) for q, t in neu.items()})
            except Exception:  # noqa: BLE001 – Zusatz, der gemessene Stand soll trotzdem gespeichert werden
                _LOG.exception("Bestand: Rückfüllung fehlgeschlagen")
                con.rollback()
                neu = {}
        con.execute("DELETE FROM tage WHERE tag = ?", (heute,))
        con.executemany("INSERT INTO tage (entity_id, tag, states, kurz, lts, attr, events) VALUES (?,?,?,?,?,?,?)",
                        ((eid, heute, *s) for eid, s in stand.items() if any(s)))
        werte = {"stand": time.time()}
        if "echt_von" not in info:
            werte["echt_von"] = heute
        for i, q in enumerate(QUELLEN):
            if q == "attr" and not mit_attr and f"{q}_von" not in info:
                continue
            alt = info.get(f"{q}_von")
            kandidaten = [heute] + ([int(alt)] if alt is not None else []) + ([neu[q]] if q in neu else [])
            werte[f"{q}_von"] = min(kandidaten)
            werte[f"{q}_bis"] = heute
        zuwachs._info_setzen(con, **werte)
        weg = _verdichte_und_beschneide(con, heute, monate)
        con.commit()
    finally:
        con.close()
    _LOG.info("Bestand erfasst in %.1f s (%d Entitäten, %d Zeilen entfernt)", time.time() - start, len(stand), weg)


def attribute_nachtragen(bestand_pfad, cache_pfad, attr_pfad, heute):
    """Nach einer Attribut-Analyse: die Spalte attr des heutigen Tages mit dem frischen Stand überschreiben."""
    if not os.path.exists(bestand_pfad):
        return
    stand, _erster = _stand_lesen(cache_pfad, attr_pfad)
    con = sqlite3.connect(bestand_pfad, timeout=60)
    try:
        if not con.execute("SELECT 1 FROM tage WHERE tag = ? LIMIT 1", (heute,)).fetchone():
            return
        con.execute("UPDATE tage SET attr = 0 WHERE tag = ?", (heute,))
        con.executemany(
            "INSERT INTO tage (entity_id, tag, attr) VALUES (?,?,?) ON CONFLICT(entity_id, tag) DO UPDATE SET attr = excluded.attr",
            ((eid, heute, s[3]) for eid, s in stand.items() if s[3]))
        info = zuwachs._info_lesen(con)
        zuwachs._info_setzen(con, attr_bis=heute, attr_von=min(heute, int(info.get("attr_von", heute))), stand=time.time())
        con.commit()
    finally:
        con.close()
