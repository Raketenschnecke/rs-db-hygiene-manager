"""Tote Entitäten in Regeln (Punkt 281) – liest nur; die Regeldatei ändert main.py über die Sicherungen der Regelverwaltung.

Wenn Entitäten gelöscht oder umbenannt werden, bleiben ihre genauen IDs in den Regeln stehen. Geprüft werden alle Muster,
die eine einzelne Entity-ID nennen: Typ entity (auch E:-Regeln) sowie glob und schutz ohne Platzhalter (z. B. weather.glinde).
Platzhalter-Muster, klasse, verwaist, herkunft und event sind keine festen IDs und bleiben außen vor.

Tot ist eine ID, die weder in der Entity-Registry steht noch einen aktuellen Zustand in HA hat – dasselbe Maß wie „aktiv“
in der Analyse. Eine in der Registry nur deaktivierte Entität zählt als lebendig. Der Befund kommt bevorzugt live aus
Registry und HA-Zuständen; ist beides nicht verlässlich lesbar (z. B. HA startet gerade), gilt ersatzweise die letzte Analyse:
tot ist dann, was dort fehlt oder verwaist ist.

  geloescht   in der Registry als gelöscht vermerkt (Entität entfernt oder umbenannt)
  unbekannt   nirgends mehr bekannt (z. B. Tippfehler oder seit Langem weg)
"""
import json
import os
import re
import time

import analyse
import regeln as rg

_PLATZHALTER = re.compile(r"[*?\[]")
_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_CACHE = {"zeit": 0, "live": None}
ZWISCHENSPEICHER_S = 30
GRUND_TEXT = {"geloescht": "in der Registry gelöscht (entfernt oder umbenannt)", "unbekannt": "nirgends mehr bekannt", "verwaist": "verwaist (laut Analyse)"}


def feste_ids(regel):
    """Muster der Regel, die genau eine Entity-ID nennen."""
    if regel["typ"] == "entity":
        return [m for m in regel["muster"] if not _PLATZHALTER.search(m)]    # Platzhalter bei entity meldet schon der Typ-Hinweis
    if regel["typ"] in ("glob", "schutz"):
        return [m for m in regel["muster"] if not _PLATZHALTER.search(m) and _ENTITY_ID.match(m)]
    return []


def _zustaende():
    """Aktuelle Zustände aus HA. Nur für Testcontainer ohne Supervisor: Liegt im Datenordner eine states_test.json
    (Momentaufnahme von /api/states), gilt sie statt der HA-API. Im Add-on gibt es diese Datei nicht."""
    datei = os.path.join(os.environ.get("DBH_DATA", "/data"), "states_test.json")
    if os.path.exists(datei):
        with open(datei, encoding="utf-8") as f:
            return json.load(f)
    return analyse.hole_zustaende()


def lebende(registry_pfad, frisch=False):
    """(lebende IDs, in der Registry gelöschte IDs); 30 s zwischengespeichert. Wirft ValueError, wenn HA nicht verlässlich antwortet.

    Leere Registry oder leere Zustandsliste (HA startet gerade) würden alle Muster als tot erscheinen lassen – dann lieber keine Aussage."""
    if not frisch and _CACHE["live"] is not None and time.time() - _CACHE["zeit"] < ZWISCHENSPEICHER_S:
        return _CACHE["live"]
    aktiv, geloescht = analyse.lies_registry(registry_pfad)
    zustaende = {z["entity_id"] for z in _zustaende()}
    if not aktiv or not zustaende:
        raise ValueError("Registry oder Zustandsliste von HA ist leer (HA startet gerade?) – keine verlässliche Aussage möglich")
    _CACHE.update(zeit=time.time(), live=(aktiv | zustaende, geloescht))
    return _CACHE["live"]


def live_oder_none(registry_pfad, frisch=False):
    try:
        return lebende(registry_pfad, frisch)
    except Exception:  # noqa: BLE001 – Zusatzprüfung: ohne HA-Antwort gilt die Analyse
        return None


def bezug(ents, live=None):
    """Grundlage der Prüfung: live (lebend, gelöscht) oder None, dazu die IDs der Analyse und ihre verwaisten."""
    return {"live": live, "ids": {e["entity_id"] for e in ents},
            "verwaist": {e["entity_id"] for e in ents if rg.ist_verwaist(e["status"])}}


def grund(m, bz):
    """None = lebt; sonst geloescht | unbekannt | verwaist."""
    if bz["live"] is not None:
        lebend, geloescht = bz["live"]
        return None if m in lebend else "geloescht" if m in geloescht else "unbekannt"
    if m in bz["verwaist"]:
        return "verwaist"
    return None if m in bz["ids"] else "unbekannt"


def tote_muster(regel, bz):
    """[{muster, grund}] der toten festen IDs einer Regel."""
    return [{"muster": m, "grund": g} for m in feste_ids(regel) if (g := grund(m, bz))]


def finde(regel_daten, bz):
    """{Regel-ID: [{muster, grund}, …]} über alle Regeln (auch passive), nur mit Treffern, in Reihenfolge der Regelliste."""
    out = {}
    for r in regel_daten["regeln"]:
        tot = tote_muster(r, bz)
        if tot:
            out[r["id"]] = tot
    return out


def entferne(regel_daten, tot, auswahl=None):
    """Regeldaten ohne die gewählten toten Einträge plus Bericht.

    auswahl: Liste [Regel-ID, Muster] oder None (= alle toten). Entfernt wird nur, was in `tot` steht – gleich neu berechnet,
    nicht aus der Anzeige übernommen. Bleibt einer Regel kein Muster, entfällt die ganze Regel."""
    gewaehlt = None if auswahl is None else {(a[0], a[1]) for a in auswahl}
    neu, bericht = [], {"muster": 0, "regeln_geaendert": [], "regeln_entfernt": []}
    for r in regel_daten["regeln"]:
        weg = {t["muster"] for t in tot.get(r["id"], []) if gewaehlt is None or (r["id"], t["muster"]) in gewaehlt}
        if not weg:
            neu.append(r)
            continue
        rest = [m for m in r["muster"] if m not in weg]
        bericht["muster"] += len(r["muster"]) - len(rest)
        if rest:
            neu.append({**r, "muster": rest})
            bericht["regeln_geaendert"].append(r["id"])
        else:
            bericht["regeln_entfernt"].append(r["id"])
    return {**regel_daten, "regeln": neu}, bericht
