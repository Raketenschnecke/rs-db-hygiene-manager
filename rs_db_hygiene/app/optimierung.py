"""Empfehlungen (Punkt 176, Reiter bis 0.5.4b7 „Optimierungen“) und Wirkung einer Regel (Punkt 170) – liest nur.

Alles rechnet auf dem Probelauf (Analyse-Cache + aktuelle Regeln), ohne neue Abfrage der HA-Datenbank:

  wirkung()     Was eine Regel im Regelwerk bewirkt, verglichen mit den heutigen Regeln: Treffer, Überstimmungen,
                zusätzliche Löschmenge sofort (aus den Tageshistogrammen) und Ersparnis im Prognosezeitraum.
  vorschlaege() Regelvorschläge nach der Analyse: Gruppen (Diagnose-Endungen), einzelne Entitäten (Vielschreiber,
                große Attribute, nirgends verwendet, deaktiviert, Uhrzeit), zusammengefasst je Klasse (Punkt 208), je
                Integration (Punkt 205) oder Namensmuster; für nirgends verwendete Entitäten die dynamische Regel
                vom Typ unbenutzt (Punkt 372, ersetzt die statische Sammelregel der Punkte 233/234; wird sie empfohlen oder
                besteht sie, entfallen Einzel-/Bündelvorschläge, deren einziger Grund „nirgends verwendet“ ist);
                verwaiste ohne Regel, Event-Typen ohne Event-Regel.
                Leitplanken: nie Energie- oder Helfer-Quellen, nie geschützte Entitäten oder Zähler, die Langzeit nur
                in der eigenen Empfehlung „Langzeit“ (Punkt 249, nirgends verwendete Entitäten);
                nur wo heute keine eigene Kurzzeit gilt (Standard bzw. HA-Standard) und der Vorschlag kürzer ist.
  verdichte()   Punkt 171: Muster mit Platzhaltern, die genau eine Auswahl von Entity-IDs treffen.
"""
import fnmatch
import json
import logging
import os
import re
import sqlite3
import threading
import time

import herkunft
import probelauf as pl_
import regeln as rg

# Schwellen der Vorschläge
VIEL_PRO_TAG = 2000          # ab hier „Vielschreiber“ (≈ alle 43 s ein neuer Wert)
VIEL_SEHR = 10000            # ab hier Vorschlag 14 statt 30 Tage
ATTR_PROGNOSE_MIN = 100_000_000   # Punkt 254: Empfehlung „Attribute“ ab 100 MB Zuwachs im Prognosezeitraum je Entität (Zuwachs/Tag × wirksame Attribut-Tage)
UNBENUTZT_PRO_TAG = 500      # nirgends verwendet und mindestens so viele Zeilen je Tag
UNBENUTZT_BUENDEL = 100      # Punkt 236: ab hier zählen unbenutzte Entitäten für Bündel (klasse, herkunft, Sammelregel) mit
GRUPPE_MIN = 3               # Mindestzahl Entitäten einer Gruppe
GRUPPE_PRO_TAG = 1000        # Mindestzuwachs einer Gruppe (Zeilen je Tag)
EVENT_PRO_TAG = 1000         # Event-Typen ab so vielen Events je Tag
TAGE = {"gruppe": "7d", "viel": "30d", "viel_sehr": "14d", "attribute": "14d", "unbenutzt": "14d", "deaktiviert": "30d",
        "verwaist": "30d", "event": "14d", "herkunft": "30d", "uhrzeit": "1d",
        "langzeit_unbenutzt": "1j"}   # Punkt 249 (252: 1 Jahr statt 90 Tage): Langzeit nirgends verwendeter Entitäten
# Punkt 205: ab so vielen Einzelvorschlägen einer Integration eine herkunft-Regel statt vieler entity-Regeln (Punkt 234:
# 3 statt 5); nicht für Sammel-Integrationen, unter denen ganz verschiedene Geräte und Helfer stehen (auch keine Sammelregel)
HERKUNFT_MIN = 3
HERKUNFT_NIE = ("mqtt", "esphome", "template", "group", "automation", "script", "scene", "statistics", "utility_meter",
                "integration", "derivative", "filter", "min_max", "threshold", "trend", "history_stats", "zha", "zwave_js",
                "knx", "modbus", "rest", "command_line", "sql", "homeassistant")
# Punkt 208: klasse-Gruppe nur, wenn höchstens dieser Anteil der übrigen freien Entitäten der Klasse irgendwo verwendet wird
# (ohne Verwendungsprüfung: wenn die Vorschläge mindestens die Hälfte der Klasse ausmachen)
KLASSE_VERWENDET = 0.1
# Punkt 206: Automationen und Skripte nennt keine andere Stelle – „nirgends verwendet“ wäre für sie immer wahr
NIE_UNBENUTZT = ("automation", "script")
# Punkt 372: Notiz der früheren statischen Sammelregeln (entity mit fester Liste) – die dynamische Regel ersetzt sie
SAMMEL_NOTIZ = "Empfehlung des RS DB HM: alle unbenutzten Entitäten in einer Regel"
UHRZEIT_PLATTFORMEN = ("time_date",)   # Punkt 208: Uhrzeit-/Datumssensoren, Verlauf ohne Nutzen
# Endungen typischer Diagnose-Entitäten (Funk, Laufzeit, Netz, Systemwerte) – Grundlage der Gruppen
DIAGNOSE_ENDUNGEN = ("rssi", "linkquality", "signal_strength", "wifi_signal", "signal", "uptime", "last_seen", "last_restart",
                     "ip", "ip_address", "ssid", "bssid", "mac", "mac_address", "heap", "free_heap", "free_memory", "loop_time",
                     "cpu_load", "lqi", "snr", "noise", "connected_clients", "boot_time", "last_boot", "restart_reason",
                     # Punkt 237: deutsche Namen
                     "signalstarke", "wlan_signal", "wifi_signalstarke", "paket_id", "packet_id", "letzter_neustart",
                     "ip_adresse", "betriebszeit")
# Punkt 237: Endungen von Diagnose-Entitäten (entity_category diagnostic) außerhalb der Liste – mindestens so viele Wortteile
# oder Zeichen, damit kurze Endungen wie _max oder _id nicht künftige fremde Entitäten treffen
DIAG_MIN_TEILE, DIAG_MIN_ZEICHEN, DIAG_MAX_TEILE = 2, 8, 3
_LOG = logging.getLogger("optimierung")
# Punkt 372 (Erkennung der Verwendung). Alle Regexe ohne Rücksicht auf Groß-/Kleinschreibung (HA akzeptiert z. B. entity_id: sensor.Temp_WZ);
# Funde werden in Kleinbuchstaben übernommen. _ID erfasst quotierte und freie Entity-IDs (states('sensor.x'), states["sensor.x"],
# state_attr('sensor.x', …), is_state(…), expand(…), entity_id: sensor.x), aber bei der Punktschreibweise states.sensor.x.state nur
# „states.sensor“ und „x.state“ – dafür die beiden Regexe unten (states.<domain>.<objekt> und states.<domain>['<objekt>'])
_ID = re.compile(r"\b([a-z_][a-z0-9_]*\.[a-z0-9_]+)\b", re.IGNORECASE)
_STATES_PUNKT = re.compile(r"\bstates\.([a-z_][a-z0-9_]*)\.([a-z0-9_]+)\b", re.IGNORECASE)
_STATES_KLAMMER = re.compile(r"""\bstates\.([a-z_][a-z0-9_]*)\[\s*['"]([a-z0-9_]+)['"]\s*\]""", re.IGNORECASE)
# Registry-IDs von Entitäten und Geräten (32 Hex-Zeichen) – Geräte-Automationen, switch_as_x und Helfer speichern darüber [Fachwissen, nicht geprüft]
_HEX32 = re.compile(r"\b[0-9a-f]{32}\b", re.IGNORECASE)
# Bereichs-/Label-/Etagen-Ziele: Wert hinter dem Schlüssel als Einzelwert, YAML-Block- oder Flow-Liste bzw. JSON-Liste (Kommentar hinter dem
# Doppelpunkt und Leer-/Kommentarzeilen vor oder zwischen den Listenelementen erlaubt); „area“ ist der Schlüssel der Dashboard-Karte
# Bereich [Fachwissen, nicht geprüft]
_ZIEL_SCHLUESSEL = re.compile(r"""["']?\b(area_id|area|label_id|floor_id)["']?[ \t]*:[ \t]*(?:\#[^\n]*)?"""
                              r"""(\[[^\]]*\]|"[^"\n]*"|'[^'\n]*'|(?:(?:\r?\n[ \t]*(?:\#[^\n]*)?)*\r?\n[ \t]*-[ \t]*[^\n]+)+|[^\s,#\]\[}{]+)""")
_ZIEL_SCHLUESSEL_NAMEN = ("area_id", "area", "label_id", "floor_id")
# Template-Funktionen mit Bereichs-/Etagen-/Label-/Integrationsnamen als Zeichenkette (area_entities('x'), 'x' | area_entities); area_id('x')
# usw. stehen oft verschachtelt (area_entities(area_id('x'))) [Fachwissen, nicht geprüft: Funktionsumfang der Templates]
_FUNKTIONEN = ("area_entities|area_devices|area_id|floor_areas|floor_entities|floor_id|label_entities|label_devices|label_areas|label_id"
               "|integration_entities")
_ZIEL_FUNKTION = re.compile(r"\b(" + _FUNKTIONEN + r")\s*\(\s*(['\"])(.*?)\2")
_ZIEL_FILTER = re.compile(r"""(['"])([^'"\n]+)\1\s*\|\s*(""" + _FUNKTIONEN + r")\b")
_WORT = re.compile(r"[A-Za-z0-9_]+")
# Anführungszeichen-Inhalte einer Zeile (Mehrwort-Namen wie „Wohn Zimmer“) und YAML-Anker/-Tags vor einem Skalar
_ZITAT = re.compile(r"""\"((?:[^\"\\\n]|\\.)*)\"|'([^'\n]*)'""")
_KOMMENTAR = re.compile(r"\s#.*$")
_ANKER = re.compile(r"^(?:[&!][^\s]*\s+)+")
# Referenzdateien in .storage (Präfix). Punkt 372 zusätzlich: person (device_trackers) und homeassistant.exposed_entities (für Sprachassistenten
# freigegebene Entitäten) [Fachwissen, nicht geprüft: Aufbau dieser Dateien]. Bewusst nicht dabei: core.restore_state und trace.* (nennen auch
# längst ungenutzte Entitäten), die Registry-Dateien (nennen jede Entität) und cloud (kann Webhook-Kennungen enthalten, Nutzen unbelegt).
# Der Präfixvergleich trifft auch Verwandte wie lovelace_resources, lovelace.<dashboard> oder Sicherungs-/Zwischendateien mit demselben Anfang:
# zusätzliche Treffer machen eine Entität höchstens „verwendet“ (sichere Seite), eine unlesbare davon zählt als Lesefehler
REFERENZ_DATEIEN = ("lovelace", "lovelace_dashboards", "energy", "core.config_entries", "automations", "scripts", "scenes",
                    "person", "homeassistant.exposed_entities")
# Punkt 372: Quellen, in denen jeder Skalarwert, der genau einer Bereichs-/Label-/Etagen-ID oder einem solchen Namen entspricht, als Ziel gilt
# (Blueprint-Eingaben und andere frei benannte Schlüssel). Dazu alle YAML-Dateien; nie die Registry-Dateien oder core.config_entries
SKALAR_QUELLEN = ("lovelace", "automations", "scripts", "scenes")
# .storage-Dateien, die als Dashboard-Quelle zählen: das Standard-Dashboard und weitere Dashboards (lovelace.<url>)
DASHBOARD_DATEIEN = ("lovelace", "lovelace.")
MAX_LOG = 10        # höchstens so viele Lesefehler je Durchlauf einzeln loggen
IGNORIERT_DATEI = "optimierungen_ignoriert.json"


# ---------------------------------------------------------------- Histogramme (für die Löschmenge sofort)

_HIST = {"schl": None}


def histogramme(cache_pfad, attr_pfad=None):
    """Tageshistogramme States, 5-Min, Langzeit und heute_tag/pkd – je Cache-Stand einmal gelesen.
    Punkt 254: mit attr_pfad zusätzlich die Attributzeilen je Tag der letzten Nutzung (für die Löschmenge einer Attribut-Regel)."""
    schl = (os.path.getmtime(cache_pfad) if os.path.exists(cache_pfad) else None,
            os.path.getmtime(attr_pfad) if attr_pfad and os.path.exists(attr_pfad) else None)
    if _HIST["schl"] != schl:
        con = sqlite3.connect(f"file:{cache_pfad}?mode=ro", uri=True)
        info = dict(con.execute("SELECT schluessel, wert FROM info"))
        _HIST.update(schl=schl, heute=int(info["heute_tag"]), pkd=int(info["purge_keep_days"]),
                     st=pl_._histogramm(con, "SELECT metadata_id, tag, n FROM st_tage"),
                     kurz=pl_._histogramm(con, "SELECT lts_id, tag, n FROM kurz_tage"),
                     lts=pl_._histogramm(con, "SELECT lts_id, tag, n FROM lts_tage"),
                     ids=dict(con.execute("SELECT entity_id, metadata_id FROM entitaet")),
                     lids=dict(con.execute("SELECT entity_id, lts_id FROM entitaet")))
        attr = {}
        if attr_pfad and os.path.exists(attr_pfad):
            try:
                ac = sqlite3.connect(f"file:{attr_pfad}?mode=ro", uri=True)
                for mid, tag, b, z in ac.execute("SELECT metadata_id, tag, bytes, zeilen FROM attr_tage WHERE art = 'letzt'"):
                    attr.setdefault(mid, []).append((tag, b, z))
                ac.close()
            except sqlite3.Error:
                attr = {}
        _HIST["attr"] = attr
        try:   # ab Cache-Version 3: Events je Typ und Tag
            _HIST["ev"] = pl_._histogramm(con, "SELECT event_type, tag, n FROM ev_tage")
        except sqlite3.OperationalError:
            _HIST["ev"] = {}
        con.close()
    return _HIST


def _vorfilter(regel, ents):
    """Nur Entitäten, die das Muster überhaupt treffen kann – spart die Auflösung für alle anderen."""
    t, m = regel["typ"], regel["muster"]
    if t == "entity":
        s = set(m)
        return [e for e in ents if e["entity_id"] in s]
    if t in ("glob", "schutz"):
        return [e for e in ents if any(fnmatch.fnmatchcase(e["entity_id"], x) for x in m)]
    if t == "klasse":
        s = set(m)
        return [e for e in ents if f'{e["domain"]}|{e.get("device_class") or ""}' in s]
    if t == "verwaist":
        return [e for e in ents if e["status"] != "aktiv"]
    if t == "vielschreiber":      # Punkt 193: nur wer die Schwelle erreicht
        s = int(m[0])
        return [e for e in ents if (e["rows_24h"] or 0) >= s]
    if t == "herkunft":           # Punkt 196
        return [e for e in ents if e.get("plattform") and any(fnmatch.fnmatchcase(e["plattform"], x) for x in m)]
    if t == "unbenutzt":          # Punkt 372: dynamisch – Treffer des Kerns (Karenz erfüllt), leeres Muster = alle, sonst glob als Einschränkung
        return [e for e in ents if e["entity_id"] in rg.UNBENUTZT["treffer"]
                and (not m or any(fnmatch.fnmatchcase(e["entity_id"], x) for x in m))]
    return ents


# ---------------------------------------------------------------- Wirkung einer Regel (Punkt 170)

def _regelwerk_mit(regel, bestand):
    liste = [regel if x["id"] == regel["id"] else x for x in bestand["regeln"]]
    if all(x["id"] != regel["id"] for x in bestand["regeln"]):
        liste.append(regel)
    return {"standard": bestand["standard"], "regeln": liste}


def wirkung(regel, pl, bestand, hist, ziel=None, beispiele=12, mit_ents=False):
    """Treffer und Wirkung einer (normalisierten, aktiv gedachten) Regel gegenüber den heutigen Regeln.

    Treffer: die Regel allein (mit Zähler-Schutz). Wirkung nur, wo sie im ganzen Regelwerk gewinnt; verglichen wird
    die wirksame Kurzzeit/Langzeit heute (Probelauf) mit der neuen. Ersparnis = Zuwachs je Tag × Differenz der Tage,
    beide höchstens bis zum Prognosezeitraum (wie die Prognose der Kacheln).
    """
    regel = {**regel, "aktiv": True}
    allein = rg.Aufloeser({"standard": rg.STANDARD, "regeln": [regel]})
    gesamt = rg.Aufloeser(_regelwerk_mit(regel, bestand))
    pkd, heute = hist["pkd"], hist["heute"]
    ziel = int(ziel or (pl.get("stand") or {}).get("ziel_tage") or pkd)
    treffer, geschuetzt, von, getroffen = [], [], {}, []
    w = {"ueberstimmt": 0, "gewinnt": 0, "zuwachs_tag": 0, "bestand": 0, "loesch_states": 0, "loesch_kurz": 0,
         "loesch_lts": 0, "loesch_attr": 0, "loesch_bytes": 0, "ersparnis_bytes": 0, "ersparnis_kurz_bytes": 0, "ersparnis_lang_bytes": 0,
         "ersparnis_attr_bytes": 0, "verlaengert": 0}
    for e in _vorfilter(regel, pl.get("entitaeten", [])):
        x = allein.loese_e(e)
        if x["geschuetzt"]:
            geschuetzt.append(e["entity_id"])
            continue
        if not any(x[a]["regel"] for a in rg.WERTE):
            continue
        treffer.append({"entity_id": e["entity_id"], "rows_24h": e["rows_24h"]})
        getroffen.append(e)
        w["zuwachs_tag"] += e["rows_24h"] or 0
        w["bestand"] += e["rows"] or 0
        g = gesamt.loese_e(e)
        sieger = {g[a]["regel"] for a in rg.WERTE if regel[a] and g[a]["regel"] != regel["id"]}
        if sieger:
            w["ueberstimmt"] += 1
            for s in sieger:
                von[s or "Standard"] = von.get(s or "Standard", 0) + 1
        if not any(g[a]["regel"] == regel["id"] for a in rg.WERTE):
            continue
        w["gewinnt"] += 1
        neu, _h = rg.wirksame_tage(g, pkd)
        alt = {a: (e["werte"] or {}).get(a, {}).get("tage") for a in rg.WERTE}
        mid, lid = hist["ids"].get(e["entity_id"]), hist["lids"].get(e["entity_id"])
        if g["kurzzeit"]["regel"] == regel["id"] and alt["kurzzeit"] is not None:
            k = max(neu["kurzzeit"], pl_.MIN_KEEP_DAYS)
            ls = max(0, pl_._aelter(hist["st"], mid, heute - k) - (e["loeschbar"]["states"] or 0))
            lk = max(0, (pl_._aelter(hist["kurz"], lid, heute - k) if lid else 0) - (e["loeschbar"]["kurz"] or 0))
            w["loesch_states"] += ls
            w["loesch_kurz"] += lk
            w["loesch_bytes"] += ls * pl_.B_STATE + lk * pl_.B_KURZ
            if e["status"] == "aktiv":
                d = min(alt["kurzzeit"], ziel) - min(neu["kurzzeit"], ziel)
                pro_tag = (e["rows_24h"] or 0) * pl_.B_STATE + (pl_.KURZ_PRO_TAG * pl_.B_KURZ if lid else 0)
                w["ersparnis_bytes"] += d * pro_tag
                w["ersparnis_kurz_bytes"] += d * pro_tag   # Punkt 253: getrennt ausgewiesen
                w["verlaengert"] += d < 0
        # Punkt 254: Attribute haben einen eigenen Wert (höchstens die Kurzzeit) – ihre Ersparnis folgt den wirksamen Tagen und
        # gehört der Regel, die Kurzzeit oder Attribute setzt
        if (g["kurzzeit"]["regel"] == regel["id"] or g["attribute"]["regel"] == regel["id"]) and e["status"] == "aktiv" \
                and alt["attribute"] is not None and neu["attribute"] is not None:
            da = min(alt["attribute"], ziel) - min(neu["attribute"], ziel)
            ersp_attr = da * (e.get("attr_zuwachs_tag") or 0)
            w["ersparnis_bytes"] += ersp_attr
            w["ersparnis_attr_bytes"] += ersp_attr   # Punkt 254: getrennt von Kurzzeit und Langzeit ausgewiesen
            w["verlaengert"] += da < 0 and not (g["kurzzeit"]["regel"] == regel["id"] and alt["kurzzeit"] is not None
                                                  and min(alt["kurzzeit"], ziel) - min(neu["kurzzeit"], ziel) < 0)
        if hist.get("attr") and g["attribute"]["regel"] == regel["id"] and neu["attribute"] is not None:
            # Löschmenge sofort der Attribute (nur wo diese Regel den Attribut-Wert setzt): Attributzeilen, die zuletzt vor der neuen Grenze
            # benutzt wurden, abzüglich dessen, was heute schon fällig ist (Attribut-Analyse nötig)
            al = max(neu["attribute"], pl_.MIN_KEEP_DAYS)
            lza = max(0, pl_._aelter_zeilen(hist["attr"], mid, heute - al) - (e["loeschbar"].get("attr_zeilen") or 0))
            lba = max(0, pl_._aelter_bytes(hist["attr"], mid, heute - al) * pl_.B_ATTR_FAKTOR - (e["loeschbar"].get("attr_bytes") or 0))
            w["loesch_attr"] += lza
            w["loesch_bytes"] += lba
        if g["langzeit"]["regel"] == regel["id"] and lid and neu["langzeit"] is not None:
            ll = max(0, pl_._aelter(hist["lts"], lid, heute - neu["langzeit"]) - (e["loeschbar"]["lts"] or 0))
            w["loesch_lts"] += ll
            w["loesch_bytes"] += ll * pl_.B_LTS
            if e["status"] == "aktiv":
                # Ersparnis im Prognosezeitraum wie der Probelauf: Langzeit „unbegrenzt“ = Bestand + Zuwachs über `ziel` Tage
                lts_jetzt = e["lts_rows"] or 0

                def lts_prog(tage):
                    return (lts_jetzt + pl_.LTS_PRO_TAG * ziel) if tage is None else pl_.LTS_PRO_TAG * tage
                d = lts_prog(alt["langzeit"]) - lts_prog(neu["langzeit"])
                w["ersparnis_bytes"] += d * pl_.B_LTS
                w["ersparnis_lang_bytes"] += d * pl_.B_LTS
                w["verlaengert"] += d < 0
    treffer.sort(key=lambda t: -(t["rows_24h"] or 0))
    return {"anzahl": len(treffer), "treffer": treffer[:beispiele], "anzahl_geschuetzt": len(geschuetzt),
            "geschuetzt": geschuetzt[:beispiele], "anzahl_ueberstimmt": w.pop("ueberstimmt"),
            "ueberstimmt_von": dict(sorted(von.items(), key=lambda kv: -kv[1])), "ziel_tage": ziel, **w,
            **({"_ents": getroffen} if mit_ents else {})}


def event_wirkung(regel, pl, bestand, hist, ziel=None):
    """Wie wirkung(), für Event-Regeln: Treffer sind Event-Typen."""
    pkd = (pl.get("stand") or {}).get("purge_keep_days")
    ziel = int(ziel or (pl.get("stand") or {}).get("ziel_tage") or pkd)
    gesamt = _regelwerk_mit({**regel, "aktiv": True}, bestand)
    treffer, von, w = [], {}, {"ueberstimmt": 0, "gewinnt": 0, "zuwachs_tag": 0, "bestand": 0, "loesch_events": 0,
                                "loesch_bytes": 0, "ersparnis_bytes": 0}
    for ev in pl.get("events", []):
        if not rg.event_regel({"regeln": [{**regel, "aktiv": True}]}, ev["event_type"], pkd)["regel"]:
            continue
        treffer.append({"entity_id": ev["event_type"], "rows_24h": ev["zeilen_24h"]})
        w["zuwachs_tag"] += ev["pro_tag"]
        w["bestand"] += ev["zeilen"]
        neu = rg.event_regel(gesamt, ev["event_type"], pkd)
        if neu["regel"] != regel["id"]:
            w["ueberstimmt"] += 1
            von[neu["regel"] or "Standard"] = von.get(neu["regel"] or "Standard", 0) + 1
            continue
        w["gewinnt"] += 1
        # Löschmenge: Anteil der Events älter als die neue Grenze, abzüglich dessen, was heute schon fällig ist
        anteil = pl_._aelter(hist["ev"], ev["event_type"], hist["heute"] - max(neu["tage"], pl_.MIN_KEEP_DAYS))
        w["loesch_events"] += max(0, anteil - ev["loeschbar"])
        w["ersparnis_bytes"] += (min(ev["tage"], ziel) - min(neu["tage"], ziel)) * ev["pro_tag"] * pl_.B_EVENT
    w["loesch_bytes"] = w["loesch_events"] * pl_.B_EVENT
    treffer.sort(key=lambda t: -(t["rows_24h"] or 0))
    return {"anzahl": len(treffer), "treffer": treffer[:12], "anzahl_geschuetzt": 0, "geschuetzt": [],
            "anzahl_ueberstimmt": w.pop("ueberstimmt"), "ueberstimmt_von": dict(sorted(von.items(), key=lambda kv: -kv[1])),
            "einheit": "Event-Typen", "ziel_tage": ziel, **w}


# ---------------------------------------------------------------- Kontext: Registry und Verwendung

def _json(pfad):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def registry_info(config):
    """{entity_id: {platform, entity_category, disabled}} aus core.entity_registry."""
    out = {}
    for e in _json(os.path.join(config, ".storage", "core.entity_registry")).get("data", {}).get("entities", []):
        out[e["entity_id"]] = {"platform": e.get("platform") or "", "kategorie": e.get("entity_category") or "",
                               "deaktiviert": bool(e.get("disabled_by"))}
    return out


_VERWENDET = {"zeit": 0, "ttl": 300, "ergebnis": None, "config": None}
_VERWENDET_SPERRE = threading.Lock()     # Punkt 372: bei Cache-Ablauf rechnet nur ein Thread, die anderen warten auf dessen Ergebnis


def _lies_registry(pfad):
    """(data, Zustand) einer Registry-Datei aus .storage; Zustand: „ok“, „fehlt“ (Datei gibt es nicht) oder „fehler“ (nicht lesbar/ungültig)."""
    try:
        with open(pfad, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None, "fehlt"
    except (OSError, ValueError, RecursionError):
        return None, "fehler"
    d = d.get("data") if isinstance(d, dict) else None
    return (d, "ok") if isinstance(d, dict) else (None, "fehler")


def _json_texte(obj):
    """Punkt 372: alle Zeichenketten eines geladenen JSON-Dokuments (iterativ): (Werte, Schlüssel, Ziele). Die Werte sind die entschlüsselten
    Texte (aus \\" wird ", aus \\n ein Zeilenumbruch) – nur so erkennen die Regexe Templates in .storage-Dateien. Ziele = [(schluessel,
    [Zeichenketten])] für Einträge mit Ziel-Schlüssel (area_id, area, label_id, floor_id), Wert als Zeichenkette oder Liste von Zeichenketten."""
    werte, schluessel, ziele, stapel = [], [], [], [obj]
    while stapel:
        x = stapel.pop()
        if isinstance(x, str):
            werte.append(x)
        elif isinstance(x, dict):
            for k, v in x.items():
                if isinstance(k, str):
                    schluessel.append(k)
                    if k.lower() in _ZIEL_SCHLUESSEL_NAMEN:
                        ziele.append((k.lower(), [v] if isinstance(v, str) else [s for s in v if isinstance(s, str)] if isinstance(v, list) else []))
                stapel.append(v)
        elif isinstance(x, list):
            stapel.extend(x)
    return werte, schluessel, ziele


def _yaml_skalare(text):
    """Punkt 372: mögliche Skalarwerte einer YAML-Datei ohne YAML-Parser (grob, auf der sicheren Seite): Anführungszeichen-Inhalte, der Rest
    jeder Zeile hinter „- “ bzw. „Schlüssel: “ ohne Kommentar und YAML-Anker/-Tags, Elemente einer Flow-Liste [a, b]. Restlücke: Werte über
    mehrere Zeilen (Block-/Faltungs-Skalare) und Namen mit Sonderzeichen in einer Flow-Liste ohne Anführungszeichen."""
    for zeile in text.splitlines():
        s = zeile.strip()
        if not s or s.startswith("#"):
            continue
        for m in _ZITAT.finditer(zeile):
            yield m.group(1) if m.group(1) is not None else m.group(2)
        s = _KOMMENTAR.sub("", s)
        while s.startswith("- ") or s == "-":
            s = s[1:].lstrip()
        teile = [s]
        if ": " in s:
            teile.append(s.split(": ", 1)[1])
        for t in teile:
            t = _ANKER.sub("", t.strip())
            if t.startswith("[") and t.endswith("]"):
                yield from t[1:-1].split(",")
            else:
                yield t


def _relativ(config, pfad):
    try:
        return os.path.relpath(pfad, config).replace(os.sep, "/")
    except ValueError:
        return "?"


def _namen(x):
    """Name und Aliase eines Registry-Eintrags (Bereich, Etage, Label) in Kleinbuchstaben."""
    s = {str(x.get("name") or "").strip().lower()}
    if isinstance(x.get("aliases"), list):
        s.update(str(a).strip().lower() for a in x["aliases"] if isinstance(a, str))
    s.discard("")
    return s


def _wert_tokens(wert):
    """Ziel-Tokens eines Werts hinter area_id/label_id/floor_id: jedes Wort, die ganze Zeichenkette und – bei Listen – jedes Element
    (Mehrwort-Namen wie „Wohn Zimmer“ bleiben so als Ganzes erhalten)."""
    t = {w.lower() for w in _WORT.findall(wert)}
    t.add(wert.strip(" \t\r\n[]\"'").lower())
    for teil in re.split(r"[,\n]", wert):
        t.add(teil.strip().lstrip("-").strip().strip("[]\"' \t").lower())
    t.discard("")
    return t


def _berechne_verwendung(config):
    st = os.path.join(config, ".storage")
    ids, hexe = set(), set()
    bereiche, labels, etagen, plattformen = set(), set(), set(), set()
    zaehler = {"fehler": 0, "log": 0}
    n = {"storage": 0, "dateien": 0, "dashboards": 0, "yaml": 0}

    def fehler(pfad):
        zaehler["fehler"] += 1
        if zaehler["log"] < MAX_LOG:      # Ursache auffindbar machen, ohne das Log zu fluten
            zaehler["log"] += 1
            _LOG.warning("Verwendung: Datei nicht lesbar: %s", _relativ(config, pfad))

    # --- Registry zuerst: Sie liefert die bekannten Bereichs-/Label-/Etagen-IDs und -Namen für die Erkennung frei benannter Ziele
    reg, z_reg = _lies_registry(os.path.join(st, "core.entity_registry"))
    eintraege = [e for e in (reg or {}).get("entities", []) if isinstance(e, dict) and e.get("entity_id")]
    registry = z_reg == "ok" and bool(eintraege)
    dev = fl = lb = fo = None
    if registry:
        dev, z_dev = _lies_registry(os.path.join(st, "core.device_registry"))
        fl, z_fl = _lies_registry(os.path.join(st, "core.area_registry"))
        lb, z_lb = _lies_registry(os.path.join(st, "core.label_registry"))
        fo, z_fo = _lies_registry(os.path.join(st, "core.floor_registry"))
        if z_dev == "fehler" or (z_dev == "fehlt" and any(e.get("device_id") for e in eintraege)):
            zaehler["fehler"] += 1       # ohne Geräteregister sind device_id und Gerätebereich nicht auflösbar
        # Bereichs-/Label-/Etagen-Register dürfen fehlen (es gibt dann keine), nicht defekt sein
        zaehler["fehler"] += (z_fl == "fehler") + (z_lb == "fehler") + (z_fo == "fehler")
    geraete = {d["id"]: d for d in (dev or {}).get("devices", []) if isinstance(d, dict) and d.get("id")}
    flaechen = [a for a in (fl or {}).get("areas", []) if isinstance(a, dict)]
    etagen_reg = [f for f in (fo or {}).get("floors", []) if isinstance(f, dict)]
    labels_reg = [x for x in (lb or {}).get("labels", []) if isinstance(x, dict)]
    if registry and z_fo == "fehlt" and any(a.get("floor_id") for a in flaechen):
        zaehler["fehler"] += 1           # Bereiche verweisen auf Etagen, das Etagenregister fehlt: Etagen sind nicht auflösbar
    bereich_namen = {a.get("id"): _namen(a) for a in flaechen}
    bereich_etage = {a.get("id"): str(a.get("floor_id") or "") for a in flaechen}
    bereich_labels = {a.get("id"): [x for x in (a.get("labels") or []) if isinstance(x, str)] for a in flaechen}
    etage_namen = {f.get("floor_id"): _namen(f) for f in etagen_reg}
    label_namen = {x.get("label_id"): _namen(x) for x in labels_reg}
    bekannt = {}         # Token (ID oder Name, klein) → Mengen, in die es als Ziel gehört
    for kennung, namen, menge in ([(a.get("id"), bereich_namen[a.get("id")], bereiche) for a in flaechen]
                                  + [(f.get("floor_id"), etage_namen[f.get("floor_id")], etagen) for f in etagen_reg]
                                  + [(x.get("label_id"), label_namen[x.get("label_id")], labels) for x in labels_reg]):
        for tok in {str(kennung or "").strip().lower(), *namen} - {""}:
            bekannt.setdefault(tok, []).append(menge)

    def ziel_menge(art):
        return labels if art == "label_id" else etagen if art == "floor_id" else bereiche

    def funktionsziel(fn, arg):
        ziel = labels if fn.startswith("label") else etagen if fn.startswith("floor") else plattformen if fn.startswith("integration") else bereiche
        ziel.add(arg.strip().lower())
        ziel.update(w.lower() for w in _WORT.findall(arg))

    def skalare(kandidaten):
        """Jeder Skalarwert, der genau einer bekannten Bereichs-/Label-/Etagen-ID oder einem solchen Namen entspricht, gilt als Ziel."""
        if not bekannt:
            return
        for c in kandidaten:
            if len(c) > 200:
                continue
            tok = c.strip().strip("\"'").strip().lower()
            for menge in bekannt.get(tok, ()):
                menge.add(tok)

    def auswerten(text):
        ids.update(m.lower() for m in _ID.findall(text))
        ids.update(f"{d}.{o}".lower() for d, o in _STATES_PUNKT.findall(text))      # {{ states.sensor.temp.state }} → sensor.temp
        ids.update(f"{d}.{o}".lower() for d, o in _STATES_KLAMMER.findall(text))    # states.sensor['temp'] → sensor.temp
        hexe.update(h.lower() for h in _HEX32.findall(text))
        for art, wert in _ZIEL_SCHLUESSEL.findall(text):
            ziel_menge(art.lower()).update(_wert_tokens(wert))
        for fn, _q, arg in _ZIEL_FUNKTION.findall(text):
            funktionsziel(fn, arg)
        for _q, arg, fn in _ZIEL_FILTER.findall(text):
            funktionsziel(fn, arg)

    def lies(pfad, mit_skalaren):
        try:
            with open(pfad, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            fehler(pfad)
            return False
        auswerten(text)
        if mit_skalaren:
            skalare(_yaml_skalare(text))
        return True

    def lies_storage(pfad, name):
        """.storage-Datei ist JSON: Rohtext (Schlüssel wie "area_id": […]) und die entschlüsselten Zeichenketten (Werte und Schlüssel)
        auswerten; Templates stehen dort mit maskierten Anführungszeichen und Zeilenumbrüchen. Ungültiges JSON zählt als Lesefehler."""
        try:
            with open(pfad, encoding="utf-8") as f:
                roh = f.read()
            daten = json.loads(roh)
        except (OSError, ValueError, RecursionError):
            fehler(pfad)
            return False
        auswerten(roh)
        werte, schluessel, ziele = _json_texte(daten)
        auswerten("\n".join(werte))
        auswerten("\n".join(schluessel))
        for art, liste in ziele:
            for s in liste:
                ziel_menge(art).update(_wert_tokens(s))
        if name.startswith(SKALAR_QUELLEN):
            skalare(werte)
        return True

    try:
        namen = sorted(os.listdir(st))
    except OSError:
        namen = []
        fehler(st)
    for name in namen:
        pfad = os.path.join(st, name)
        if name.startswith(REFERENZ_DATEIEN) and os.path.isfile(pfad) and lies_storage(pfad, name):
            n["storage"] += 1
            if name == "lovelace" or name.startswith("lovelace."):
                n["dashboards"] += 1
    dateien, f_walk = herkunft.verwendungs_dateien(config)
    zaehler["fehler"] += f_walk
    for pfad in dateien:
        ist_yaml = pfad.endswith((".yaml", ".yml"))
        if lies(pfad, ist_yaml):
            n["dateien"] += 1
            n["yaml"] += ist_yaml
    for s in (bereiche, labels, etagen, plattformen):
        s.discard("")

    # --- Auflösung über die Registry
    aufgeloest = 0
    if registry:
        geraet_verwendet = {d for d in geraete if d in hexe}

        def etage_gemeint(fid):
            return bool(fid) and (str(fid).lower() in etagen or bool(etage_namen.get(fid, set()) & etagen))

        def label_gemeint(lid):
            return bool(lid) and (str(lid).lower() in labels or bool(label_namen.get(lid, set()) & labels))

        def bereich_gemeint(aid):
            """Bereich genannt (ID oder Name), seine Etage genannt oder eines seiner Labels genannt."""
            return bool(aid) and (str(aid).lower() in bereiche or bool(bereich_namen.get(aid, set()) & bereiche)
                                  or etage_gemeint(bereich_etage.get(aid))
                                  or any(label_gemeint(x) for x in bereich_labels.get(aid, ())))

        for e in eintraege:
            eid = e["entity_id"].lower()
            if eid in ids:
                continue
            gd = geraete.get(e.get("device_id")) or {}
            if ((e.get("id") and str(e["id"]).lower() in hexe) or e.get("device_id") in geraet_verwendet
                    or bereich_gemeint(e.get("area_id")) or bereich_gemeint(gd.get("area_id"))
                    or any(label_gemeint(x) for x in (e.get("labels") or []) + (gd.get("labels") or []))
                    or (e.get("platform") or "").lower() in plattformen):
                ids.add(eid)
                aufgeloest += 1
    erg = {"ids": ids, "storage": n["storage"], "dateien": n["dateien"], "dashboards": n["dashboards"], "yaml": n["yaml"],
           "fehler": zaehler["fehler"], "registry": registry, "aufgeloest": aufgeloest}
    return erg


def verwendung(config):
    """Punkt 372: Verwendung der Entitäten samt Kennzahlen: {"ids": set, "storage": n, "dateien": n, "dashboards": n, "yaml": n, "fehler": n,
    "registry": bool, "aufgeloest": n}. ids sind alle Entity-IDs (klein geschrieben), die irgendwo in der Konfiguration genannt werden:
    Dashboards, Automationen, Skripte, Szenen, Templates und Helfer (UI und YAML), Energie-Dashboard, ESPHome- und Blueprint-YAML,
    Python-Skripte (python_scripts, pyscript), Jinja-Makros, Personen und Freigaben für Sprachassistenten (soweit in
    homeassistant.exposed_entities gespeichert). .storage-Dateien sind JSON: ausgewertet werden der Rohtext und die entschlüsselten
    Zeichenketten (Werte und Schlüssel), weil Templates dort mit maskierten Anführungszeichen stehen. Grob, aber auf der sicheren Seite:
    jedes Vorkommen zählt, ohne Rücksicht auf Groß-/Kleinschreibung. Dazu die Auflösung über die Registry: Registry-ID einer Entität oder
    device_id eines Geräts im Text → Entität bzw. alle Entitäten des Geräts; area_id/label_id/floor_id (und area_entities('…'),
    floor_entities('…'), label_entities('…') usw.) → alle Entitäten des Bereichs (eigener oder der des Geräts), des Bereichs auf der
    Etage bzw. mit dem Label (Label an Entität, Gerät oder Bereich); integration_entities('…') → alle der Plattform. Frei benannte
    Schlüssel (Blueprint-Eingaben): jeder Skalarwert in Dashboards, Automationen, Skripten, Szenen und YAML-Dateien, der genau einer
    Bereichs-/Label-/Etagen-ID oder einem solchen Namen entspricht, gilt als Ziel (bewusst breit). storage = gelesene .storage-
    Referenzdateien, dashboards = davon Dashboard-Quellen (lovelace, lovelace.<url>), dateien = gelesene YAML-/py-/Jinja-Dateien,
    yaml = davon YAML-Dateien, fehler = Lesefehler (ein fehlender optionaler Ordner zählt nicht), registry = Entity-Registry gelesen und
    nicht leer, aufgeloest = über die Registry ergänzte Entitäten. Ob das Ergebnis für die Regel unbenutzt verlässlich ist, sagt
    verwendung_grund(). 5 min zwischengespeichert (bei Lesefehlern nur 60 s); bei Cache-Ablauf rechnet ein Thread, die übrigen warten.
    Die Registry-Dateien selbst werden nicht als Text ausgewertet (sie nennen jede Entität)."""
    def frisch():
        e = _VERWENDET["ergebnis"]
        if e is not None and _VERWENDET["config"] == config and time.time() - _VERWENDET["zeit"] < _VERWENDET["ttl"]:
            return e
        return None
    erg = frisch()
    if erg is not None:
        return erg
    with _VERWENDET_SPERRE:
        erg = frisch()
        if erg is None:
            erg = _berechne_verwendung(config)
            _VERWENDET.update(zeit=time.time(), ttl=60 if (erg["fehler"] or not erg["registry"]) else 300, ergebnis=erg, config=config)
        return erg


def verwendung_grund(v):
    """Punkt 372: None, wenn die Verwendung verlässlich bestimmt ist, sonst der Grund (Text für die GUI). Nicht bestimmbar bei
    Lesefehlern, ohne gelesene .storage-Referenzdatei, ohne gelesene Dashboard-Quelle (.storage/lovelace*) und YAML-Datei oder bei nicht
    lesbarer Entity-Registry."""
    if not v["registry"]:
        return "Entity-Registry nicht lesbar"
    if v["fehler"]:
        return f"{v['fehler']} Lesefehler in der Konfiguration – die Verwendung wäre unvollständig"
    if not v["storage"]:
        return "keine Referenzdatei (Dashboards, Helfer, Energie) im Speicherordner von Home Assistant lesbar"
    if not (v.get("dashboards") or v.get("yaml")):
        return "weder eine Dashboard-Quelle (.storage/lovelace*) noch eine YAML-Datei der Konfiguration lesbar"
    if not v["ids"]:
        return "in der Konfiguration wurde keine Entität gefunden"
    return None


def verwendung_sicher(config):
    """Punkt 372: (ids, info). ids = Menge der verwendeten Entity-IDs oder None, wenn die Verwendung nicht bestimmbar ist; info =
    {grund, storage, dateien, dashboards, yaml, fehler, registry, aufgeloest, woerter} (grund None = bestimmbar; woerter = Größe der Menge
    ids, sie zählt jedes Wort mit Punkt und ist keine Entitätenzahl – die zählt halte_unbenutzt)."""
    try:
        v = verwendung(config)
    except Exception as x:  # noqa: BLE001 – ohne verlässliche Verwendung trifft die Regel nichts
        _LOG.exception("Verwendung der Entitäten nicht bestimmbar")
        return None, {"grund": f"Auswertung fehlgeschlagen ({type(x).__name__})", "storage": 0, "dateien": 0, "dashboards": 0, "yaml": 0,
                      "fehler": 0, "registry": False, "aufgeloest": 0, "woerter": 0}
    grund = verwendung_grund(v)
    info = {"grund": grund, "storage": v["storage"], "dateien": v["dateien"], "dashboards": v.get("dashboards", 0), "yaml": v.get("yaml", 0),
            "fehler": v["fehler"], "registry": v["registry"], "aufgeloest": v["aufgeloest"], "woerter": len(v["ids"])}
    return (None if grund else v["ids"]), info


def verwendete_ids(config):
    """Alle Entity-IDs, die irgendwo in der Konfiguration genannt werden (Menge, wie bisher; Details in verwendung()). Ohne
    Prüfung der Verlässlichkeit – für die Regel unbenutzt und die Empfehlungen gilt verwendung_sicher()."""
    return verwendung(config)["ids"]


# ---------------------------------------------------------------- Vorschläge (Punkt 176)

def _endung(eid):
    name = eid.split(".", 1)[1]
    for e in sorted(DIAGNOSE_ENDUNGEN, key=len, reverse=True):
        if name == e or name.endswith("_" + e):
            return e
    return None


def _frei(e, ktx):
    """Darf für diese Entität eine kürzere Kurzzeit vorgeschlagen werden?"""
    if e["status"] != "aktiv" or e.get("schutz") or (e.get("state_class") or "").startswith("total"):
        return False
    if e["entity_id"] in ktx["energie"] or e["entity_id"] in ktx["helfer"]:
        return False
    kz = (e["werte"] or {}).get("kurzzeit") or {}
    return kz.get("regel") in (None, rg.HA_STANDARD_ID)


def _tage_von(wert):
    return rg.in_tagen(wert)


def _de(x):
    """Zahl mit einer Nachkommastelle und deutschem Komma (nur für Zahlen, nie für Texte mit Entity-IDs)."""
    return f"{x:.1f}".replace(".", ",")


def _anzahl(n, ein, mehr):
    return f"{n:,} {ein if n == 1 else mehr}".replace(",", ".")


# Punkt 232 (offener Teil, 0.5.6b2): Erweitern statt neu – bestehende Regel gleichen Typs, in die ein Vorschlag passt
ERWEITERN_TYPEN = ("entity", "glob", "klasse", "herkunft", "event")


def _erweiterbar(vorschlag, bestand, pl, hist, ziel):
    """Bestehende Regel gleichen Typs, in die der Vorschlag passt: Kurzzeit gleich lang oder kürzer (die Ersparnis
    bleibt mindestens), keine Schutz-, E:- oder Ausnahme-Regel. Bevorzugt: ohne eigene Langzeit (Empfehlungen fassen
    die Langzeit sonst nie an), dann die längste Kurzzeit (gleich vor kürzer), dann aktiv, dann die Reihenfolge. Wirkung mit allen Mustern gerechnet (nur der Zuwachs
    durch die neuen Muster – für die bisherigen Treffer ändert sich nichts)."""
    typ, tv = vorschlag["typ"], rg.in_tagen(vorschlag["kurzzeit"])
    if typ not in ERWEITERN_TYPEN or tv is None:
        return None
    kand = []
    for i, r in enumerate(bestand["regeln"]):
        if r["typ"] != typ or r["id"].startswith("E:") or r["name"].startswith("Ausnahme: "):
            continue
        tr = rg.in_tagen(r["kurzzeit"])
        if tr is None or tr > tv or all(m in r["muster"] for m in vorschlag["muster"]):
            continue
        kand.append((r["langzeit"] is not None, -tr, not r["aktiv"], i, r))
    if not kand:
        return None
    *_s, r = min(kand, key=lambda k: k[:4])
    neu = {**r, "aktiv": True, "muster": r["muster"] + [m for m in vorschlag["muster"] if m not in r["muster"]]}
    w = event_wirkung(neu, pl, bestand, hist, ziel=ziel) if typ == "event" else wirkung(neu, pl, bestand, hist, ziel=ziel)
    if not w["gewinnt"]:
        return None
    return {"id": r["id"], "name": r["name"], "aktiv": r["aktiv"], "kurzzeit": r["kurzzeit"], "langzeit": r["langzeit"],
            "gleich": r["kurzzeit"] == vorschlag["kurzzeit"] and r["langzeit"] is None,
            "muster_neu": [m for m in vorschlag["muster"] if m not in r["muster"]],
            "loesch_bytes": w["loesch_bytes"], "ersparnis_bytes": w["ersparnis_bytes"]}


def vorschlaege(pl, bestand, hist, ktx, config, ignoriert=(), ziel=None):
    ents = [e for e in pl.get("entitaeten", [])]
    reg = registry_info(config)
    # Punkt 372: zwei Größen. verwendet = sichere Menge, None bei nicht bestimmbarer Verwendung (Lesefehler, keine Referenzdatei, Registry
    # nicht lesbar): Grundlage für alles, was auf NICHT-Verwendung beruht (dynamische Regel, Einzelvorschläge „nirgends verwendet“,
    # Diagnose-Gruppen Punkt 237, Langzeit Punkt 249) – bei None entfallen diese Vorschläge. verwendet_teil = möglicherweise
    # unvollständige Menge, nur für die bremsende Prüfung bei klasse/integration („von den übrigen werden X verwendet“): Teilwissen
    # bremst weiterhin, fehlendes Wissen nie ein Vorschlag mehr.
    verwendet = verwendung_sicher(config)[0] or None
    try:
        verwendet_teil = verwendet if verwendet is not None else (verwendung(config)["ids"] or None)
    except Exception:  # noqa: BLE001 – ohne Teilwissen gilt nur die Anteilsprüfung der Bündel
        verwendet_teil = None
    regeln_muster = {(r["typ"], tuple(sorted(r["muster"]))): r for r in bestand["regeln"]}
    passive = [{**r, "_aufl": rg.Aufloeser({"standard": rg.STANDARD, "regeln": [{**r, "aktiv": True}]})}
               for r in bestand["regeln"] if not r["aktiv"] and r["typ"] not in ("event", "ha_standard") and r.get("kurzzeit")]
    out, vergeben = [], set()

    def kurz_wirksam(e):
        return ((e["werte"] or {}).get("kurzzeit") or {}).get("tage")

    def neu(vid, art, titel, typ, muster, kurzzeit, gruende, name, notiz="", immer=False, langzeit=None, attribut=None, schaetzung=None):
        """schaetzung (Punkt 372): Entity-IDs, über die die Wirkung einer dynamischen Regel (unbenutzt) geschätzt wird – ihre Treffer
        sind während der Karenz leer, die Rechnung nimmt stattdessen eine entity-Regel mit diesen IDs und derselben Kurzzeit
        (Rang spielt keine Rolle: die Kandidaten haben heute keine eigene Kurzzeit). Der Vorschlag selbst bleibt die dynamische Regel."""
        regel = {"id": "VORSCHLAG", "name": name, "typ": typ, "muster": muster, "aktiv": False, "notiz": notiz,
                 "kurzzeit": kurzzeit, "attribute": attribut, "langzeit": langzeit}
        regel["name"] = rg.name_mit_suffix(name, regel)   # Punkt 241: „– 14T/∞“ statt „– 14 Tage“
        norm = rg.normalisiere({"regeln": [regel], "events": {}})["regeln"][0]
        rechenregel = {**norm, "typ": "entity", "muster": sorted(schaetzung)} if schaetzung is not None else norm
        w = event_wirkung(norm, pl, bestand, hist, ziel=ziel) if typ == "event" else wirkung(rechenregel, pl, bestand, hist, ziel=ziel, mit_ents=True)
        if not immer and (not w["gewinnt"] or w["ersparnis_bytes"] <= 0 and not w["loesch_bytes"]):   # immer: Punkt 239
            return None
        # Punkte 205/208: herkunft/klasse haben Rang vor vielschreiber – nie vorschlagen, wenn sie irgendwo verlängern
        if typ in ("herkunft", "klasse") and (w.get("verlaengert") or w["ersparnis_bytes"] <= 0):
            return None
        if w.get("verlaengert"):   # Punkte 205/208: herkunft/klasse haben Rang vor vielschreiber und können verlängern
            gruende = gruende + [f"⚠ verlängert die Kurzzeit von {_anzahl(w['verlaengert'], 'Entität', 'Entitäten')}, "
                                 "für die heute eine kürzere Regel gilt (Rang) – in der Maske prüfen"]
        vorhanden = regeln_muster.get((typ, tuple(sorted(muster))))
        erweitern = None if vorhanden else _erweiterbar(norm, bestand, pl, hist, ziel)
        # passive Regeln, die schon (einen Teil) der Treffer abdecken – der Vorschlag ist dann vielleicht nur „einschalten“
        passiv = []
        if typ != "event":
            for p in passive:
                n_ = sum(1 for e in w["_ents"] if any(p["_aufl"].loese_e(e)[a]["regel"] for a in ("kurzzeit",)))
                if n_:
                    passiv.append({"id": p["id"], "anzahl": n_, "kurzzeit": p["kurzzeit"]})
        v = {"id": vid, "art": art, "titel": titel, "gruende": gruende, "ignoriert": vid in ignoriert,
             "regel": regel, "vorhanden": ({"id": vorhanden["id"], "aktiv": vorhanden["aktiv"]} if vorhanden else None),
             "erweitern": erweitern,
             "passiv": passiv,
             **{k: w[k] for k in ("anzahl", "anzahl_geschuetzt", "anzahl_ueberstimmt", "gewinnt", "zuwachs_tag", "bestand",
                                  "loesch_bytes", "ersparnis_bytes")},
             **{k: w[k] for k in ("ersparnis_kurz_bytes", "ersparnis_lang_bytes", "ersparnis_attr_bytes") if k in w},   # Punkt 253: Events ohne Langzeit haben sie nicht
             "loesch_zeilen": (w.get("loesch_states", 0) + w.get("loesch_kurz", 0) + w.get("loesch_lts", 0) + w.get("loesch_events", 0)
                               + w.get("loesch_attr", 0)),
             "beispiele": [t["entity_id"] for t in w["treffer"][:6]]}
        out.append(v)
        return v

    # 0. Punkt 372: dynamische Regel „nirgends verwendet“ (Typ unbenutzt, leeres Muster) statt statischer Sammelregeln. Nur mit bekannter
    # Verwendung, ohne bestehende unbenutzt-Regel (aktiv oder passiv) und wenn mindestens eine freie unbenutzte Entität mit Daten länger
    # als der Vorschlag behalten würde. Die Treffer der echten Regel sind während der Karenz leer – die Wirkung wird deshalb über die
    # heute unbenutzten freien Entitäten geschätzt (neu(..., schaetzung=ids)); der Vorschlag selbst bleibt die dynamische Regel.
    # Der Block steht vor den Einzelvorschlägen: ob die dynamische Regel empfohlen wird oder schon besteht, entscheidet dort, ob
    # Vorschläge entfallen, die nur auf „nirgends verwendet“ beruhen (durch_unbenutzt_gedeckt). Die Schätzung umfasst weiter alle
    # unbenutzten freien Entitäten, unabhängig von dieser Unterdrückung.
    bestehende_ub = [r for r in bestand["regeln"] if r["typ"] == "unbenutzt"]
    dyn_empfohlen = False
    if verwendet is not None and not bestehende_ub:
        ku = _tage_von(TAGE["unbenutzt"])
        ub = sorted(e["entity_id"] for e in ents
                    if _frei(e, ktx) and e["entity_id"] not in verwendet and e["domain"] not in NIE_UNBENUTZT
                    and (e["rows"] or 0) > 0 and (kurz_wirksam(e) or 0) > ku)
        if ub:
            sammel = [r for r in bestand["regeln"] if r["typ"] == "entity" and (r.get("notiz") or "").startswith(SAMMEL_NOTIZ)]
            gr = [f"{_anzahl(len(ub), 'Entität', 'Entitäten')} nirgends verwendet (nicht in Dashboards, Automationen, Skripten, Templates, "
                  "Helfern, Energie), ohne eigene Kurzzeit und mit Daten – Grundlage der geschätzten Wirkung",
                  f"dynamische Regel: sie erfasst jede Entität erst nach {rg.KARENZ_TAGE} Tagen durchgehender Nichtverwendung (Karenz); "
                  "wird eine Entität wieder verwendet, verlässt sie die Regel mit der nächsten Berechnung (spätestens der nächsten Analyse) und die Karenz beginnt von vorn. Die Treffer werden bei "
                  "jeder Analyse neu bestimmt, auch künftige Entitäten kommen dazu – die Wirkung hier ist deshalb eine Schätzung",
                  "setzt nur Kurzzeit und Attribute, keine Langzeit; bereits gelöschte Daten kommen nicht zurück",
                  "ersetzt die Einzel- und Bündelvorschläge, die nur auf „nirgends verwendet“ beruhen"]
            if sammel:
                namen = ", ".join(f"{r['id']} „{r['name']}“" for r in sammel[:4]) + (f" … (+{len(sammel) - 4})" if len(sammel) > 4 else "")
                gr.append(f"kann nach Übernahme entfernt werden: {_anzahl(len(sammel), 'statische Sammelregel', 'statische Sammelregeln')} "
                          f"für nirgends verwendete Entitäten ({namen})")
            v_dyn = neu("unbenutzt:dynamisch", "unbenutzt", "Nirgends verwendete Entitäten (dynamisch)", "unbenutzt", [], TAGE["unbenutzt"], gr,
                        "Nirgends verwendet", "Empfehlung des RS DB HM: nirgends verwendete Entitäten dynamisch", schaetzung=ub)
            dyn_empfohlen = bool(v_dyn) and not v_dyn["ignoriert"]     # ignoriert: der Nutzer verzichtet darauf, die Einzelvorschläge kehren zurück

    def durch_unbenutzt_gedeckt(eid):
        """Auflage B (Punkt 372): Trifft die dynamische Regel diese Entität (empfohlen oder bereits vorhanden, aktiv oder passiv; mit
        Entity-Muster nur dessen Treffer), entfallen Vorschläge, deren einziger Grund „nirgends verwendet“ ist."""
        if verwendet is None:
            return False
        if dyn_empfohlen:
            return True
        return any(not r["muster"] or any(fnmatch.fnmatchcase(eid, m) for m in r["muster"]) for r in bestehende_ub)

    # 1. Gruppen: Diagnose-Endungen je Domain → eine glob-Regel
    gruppen = {}
    for e in ents:
        if not _frei(e, ktx):
            continue
        end = _endung(e["entity_id"])
        if end:
            gruppen.setdefault((e["domain"], end), []).append(e)
    tg = _tage_von(TAGE["gruppe"])
    for (dom, end), l in sorted(gruppen.items(), key=lambda kv: -sum(x["rows_24h"] or 0 for x in kv[1])):
        l = [e for e in l if (kurz_wirksam(e) or 0) > tg]
        if len(l) < GRUPPE_MIN or sum(e["rows_24h"] or 0 for e in l) < GRUPPE_PRO_TAG:
            continue
        muster = f"{dom}.*_{end}"
        v = neu(f"gruppe:{muster}", "gruppe", f"Gruppe {muster}", "glob", [muster], TAGE["gruppe"],
                [f"{len(l)} Diagnose-Entitäten mit Endung _{end} ohne eigene Kurzzeit"],
                f"Diagnose {muster} – 7 Tage", "Empfehlung des RS DB HM: Diagnosewerte braucht man selten länger als eine Woche")
        if v:
            vergeben.update(e["entity_id"] for e in l)

    # Punkt 237: Diagnose-Entitäten (Kategorie diagnostic) mit Endungen außerhalb der Liste → glob-Regel, nur für unbenutzte
    # und nur, wenn das Muster heute ausschließlich solche Entitäten ändert (Gegenprobe über alle aktiven Entitäten)
    if verwendet is not None:
        regel_von = {r["id"]: r for r in bestand["regeln"]}

        def diag_frei(e):
            return (_frei(e, ktx) and e["entity_id"] not in verwendet and (kurz_wirksam(e) or 0) > tg
                    and (reg.get(e["entity_id"]) or {}).get("kategorie") == "diagnostic")

        def unberuehrt(e):
            """Ändert eine neue glob-Regel (am Ende, 7 Tage, ohne Langzeit) für diese Entität nichts?"""
            if e.get("schutz") or (kurz_wirksam(e) or 0) == tg:
                return True
            r = regel_von.get(((e["werte"] or {}).get("kurzzeit") or {}).get("regel")) or {}
            return r.get("typ") in ("schutz", "entity", "glob") or str(r.get("id", "")).startswith("E:")

        def enden(eid):
            teile = eid.split(".", 1)[1].split("_")
            for k in range(1, min(DIAG_MAX_TEILE, len(teile) - 1) + 1):
                end = "_".join(teile[-k:])
                if k >= DIAG_MIN_TEILE or len(end) >= DIAG_MIN_ZEICHEN:
                    yield end

        alle_end = {}   # Muster → alle aktiven Entitäten ohne Zähler, die es träfe
        dg = {}
        for e in ents:
            if e["status"] != "aktiv" or (e.get("state_class") or "").startswith("total"):
                continue
            kandidat = e["entity_id"] not in vergeben and not _endung(e["entity_id"]) and diag_frei(e)
            for end in enden(e["entity_id"]):
                m = f'{e["domain"]}.*_{end}'
                alle_end.setdefault(m, []).append(e)
                if kandidat:
                    dg.setdefault(m, []).append(e)
        for m, l in sorted(dg.items(), key=lambda kv: (-sum(x["rows_24h"] or 0 for x in kv[1]), -len(kv[0]))):
            l = [e for e in l if e["entity_id"] not in vergeben]
            if len(l) < GRUPPE_MIN or sum(e["rows_24h"] or 0 for e in l) < GRUPPE_PRO_TAG:
                continue
            if not all(diag_frei(e) or unberuehrt(e) for e in alle_end[m]):
                continue
            end = m.split(".*_", 1)[1]
            v = neu(f"gruppe:{m}", "gruppe", f"Gruppe {m}", "glob", [m], TAGE["gruppe"],
                    [f"{len(l)} Diagnose-Entitäten (Kategorie diagnostic) mit Endung _{end}, nirgends verwendet, ohne eigene Kurzzeit",
                     "das Muster trifft heute nur solche Entitäten – künftige mit derselben Endung kämen hinzu"],
                    f"Diagnose {m} – 7 Tage", "Empfehlung des RS DB HM: Diagnosewerte braucht man selten länger als eine Woche")
            if v:
                vergeben.update(e["entity_id"] for e in l)

    # 2. einzelne Entitäten: Vielschreiber, große Attribute, nirgends verwendet, deaktiviert
    # Punkt 236: unbenutzte mit 100–499 Zeilen je Tag (schwach) zählen nur für Bündel (klasse, herkunft, Sammelregel)
    einzeln, schwach = [], []
    for e in sorted(ents, key=lambda x: -(x["rows_24h"] or 0)):
        eid = e["entity_id"]
        if eid in vergeben or not _frei(e, ktx):
            continue
        ri = reg.get(eid) or {}
        n, gruende, tage = e["rows_24h"] or 0, [], []
        unbenutzt = verwendet is not None and eid not in verwendet and e["domain"] not in NIE_UNBENUTZT
        unb_grund = False      # Punkt 372: „nirgends verwendet“ ist (einer) der Gründe dieses Einzelvorschlags
        gedeckt = unbenutzt and durch_unbenutzt_gedeckt(eid)
        if ri.get("deaktiviert") and (e["rows"] or 0) > 0:
            gruende.append("in HA deaktiviert – schreibt nichts mehr, Altdaten bleiben sonst bis purge_keep_days")
            tage.append(TAGE["deaktiviert"])
        if n >= VIEL_PRO_TAG:
            gruende.append(f"Vielschreiber: {n:,} Zeilen je Tag (Ø 7 volle Tage, ≈ alle {max(1, round(86400 / n))} s)".replace(",", "."))
            tage.append(TAGE["viel_sehr" if n >= VIEL_SEHR else "viel"])
        if unbenutzt and n >= UNBENUTZT_PRO_TAG:
            gruende.append("nirgends verwendet (nicht in Dashboards, Automationen, Skripten, Templates, Helfern, Energie)")
            tage.append(TAGE["unbenutzt"])
            unb_grund = True
        if (e.get("plattform") or ri.get("platform")) in UHRZEIT_PLATTFORMEN and n:   # Punkt 208
            gruende.append("Uhrzeit/Datum: der Verlauf hat keinen Nutzen")
            tage.append(TAGE["uhrzeit"])
        if not gruende:
            # Punkt 372: die dynamische Regel deckt die Entität – kein schwacher Kandidat für Bündel
            if unbenutzt and not gedeckt and n >= UNBENUTZT_BUENDEL and (kurz_wirksam(e) or 0) > _tage_von(TAGE["unbenutzt"]):
                schwach.append((eid, TAGE["unbenutzt"], ["nirgends verwendet, unter 500 Zeilen je Tag (nur im Bündel)"]))
            continue
        if unb_grund and gedeckt and len(gruende) == 1:
            continue       # Punkt 372: einziger Grund „nirgends verwendet“ und von der dynamischen Regel gedeckt → kein Einzelvorschlag
        kz = min(tage, key=_tage_von)
        if (kurz_wirksam(e) or 0) <= _tage_von(kz):
            continue
        if ri.get("platform") == "esphome" and n >= VIEL_PRO_TAG:
            gruende.append("ESPHome: besser direkt am Gerät filtern (delta, throttle_average) – dann entstehen die Zeilen gar nicht erst")
        einzeln.append((eid, kz, gruende))

    def arten(drin):
        """Gründe der Einzelvorschläge einer Gruppe gezählt, z. B. „12× nirgends verwendet“."""
        n_ = {}
        for _e, _k, g in drin:
            for t in g:
                kurz = t.split(":")[0].split(" (")[0].split(" –")[0]
                n_[kurz] = n_.get(kurz, 0) + 1
        return [f"{z}× {a}" for a, z in sorted(n_.items(), key=lambda kv: -kv[1])]

    eidx = {e["entity_id"]: e for e in ents}
    stark = {x[0] for x in einzeln}

    def frei_alle(pruef, kz):
        """Alle freien Entitäten, die eine Muster-Regel mit Kurzzeit kz kürzen würde – nicht nur die Einzelvorschläge."""
        return [e for e in ents if pruef(e) and _frei(e, ktx) and (kurz_wirksam(e) or 0) > _tage_von(kz)]

    def buendel(l, wo):
        """Erste Begründung eines Bündels: Einzelvorschläge und schwache Kandidaten (Punkt 236)."""
        st = sum(1 for x in l if x[0] in stark)
        sw = f"{len(l) - st} {'weitere ' if st else ''}unbenutzte mit 100–499 Zeilen je Tag"
        t = f"{wo}: " + (_anzahl(st, 'Einzelvorschlag', 'Einzelvorschläge') + (f" und {sw}" if len(l) > st else "") if st else sw)
        return t + " – eine Regel statt vieler"

    # Punkt 372: die statische Sammelregel (Punkte 233/234, entity mit fester Liste) entfällt – unbenutzte Entitäten, für die keine
    # klasse-/herkunft-Regel passt, deckt die dynamische Regel (Block „0.“ oben) ab

    # Punkt 208: Einzelvorschläge derselben Klasse (domain|device_class) → eine klasse-Regel, wenn die übrigen freien
    # Entitäten der Klasse (fast) nirgends verwendet werden – sonst träfe die Regel Werte, die jemand nutzt (z. B. power)
    je_kl = {}
    for x in einzeln + schwach:
        e = eidx[x[0]]
        if e.get("device_class"):
            je_kl.setdefault(f'{e["domain"]}|{e["device_class"]}', []).append(x)
    for k, l in sorted(je_kl.items(), key=lambda kv: -len(kv[1])):
        if len(l) < GRUPPE_MIN:
            continue
        kz = max((x[1] for x in l), key=_tage_von)
        dom, dc = k.split("|", 1)
        frei = frei_alle(lambda e: e["domain"] == dom and e.get("device_class") == dc, kz)
        kand = {x[0] for x in l}
        rest = [e for e in frei if e["entity_id"] not in kand]
        if verwendet is None and len(l) < 0.5 * len(frei):
            continue       # ohne sichere Verwendung nur, wenn die Einzelvorschläge mindestens die Hälfte der Klasse ausmachen
        # bremsende Prüfung mit der möglicherweise unvollständigen Menge (Teilwissen bremst weiterhin)
        benutzt = None if verwendet_teil is None else [e["entity_id"] for e in rest if e["entity_id"] in verwendet_teil]
        v = None
        if benutzt is None or len(benutzt) <= KLASSE_VERWENDET * len(rest):
            gr = [buendel(l, f"Klasse {k}"),
                  f"trifft alle {_anzahl(len(frei), 'Entität', 'Entitäten')} dieser Klasse ohne eigene Kurzzeit (Zähler ausgenommen), auch künftige"]
            if rest and benutzt is not None:
                gr.append(f"von den übrigen {len(rest)} werden {len(benutzt)} irgendwo verwendet"
                          + ("; Verwendung nur teilweise bestimmbar" if verwendet is None else ""))
            elif verwendet is None:
                gr.append("Verwendung nicht bestimmbar – geprüft nur über den Anteil der Einzelvorschläge an der Klasse")
            vid = f"klasse:{k}"
            v = neu(vid, "klasse", f"Klasse {k}", "klasse", [k], kz, gr + arten(l), f"Klasse {k} – {rg.anzeige(kz)}",
                    "Empfehlung des RS DB HM")
            if v and vid not in ignoriert:
                vergeben.update(x[0] for x in l)

    # Punkt 205: viele Einzelvorschläge einer Integration → eine herkunft-Regel (trifft alle Entitäten der Integration);
    # Uhrzeit-Integrationen schon ab einem Vorschlag. Punkt 234: Kurzzeit wie bei klasse die längste der Einzelvorschläge,
    # Verwendungsprüfung wie bei klasse, sonst Sammelregel
    je_pl = {}
    for x in einzeln + schwach:
        p = eidx[x[0]].get("plattform") or (reg.get(x[0]) or {}).get("platform") or ""
        if x[0] not in vergeben and p and p not in HERKUNFT_NIE:
            je_pl.setdefault(p, []).append(x)
    for p, l in sorted(je_pl.items(), key=lambda kv: -len(kv[1])):
        uhr = p in UHRZEIT_PLATTFORMEN
        if len(l) < (1 if uhr else HERKUNFT_MIN):
            continue
        kz = max((x[1] for x in l), key=_tage_von)
        frei = frei_alle(lambda e: e.get("plattform") == p, kz)
        kand = {x[0] for x in l}
        rest = [e for e in frei if e["entity_id"] not in kand]
        # bremsende Prüfung mit der möglicherweise unvollständigen Menge (Teilwissen bremst weiterhin)
        benutzt = None if verwendet_teil is None or uhr else [e["entity_id"] for e in rest if e["entity_id"] in verwendet_teil]
        v = None
        if benutzt is None or len(benutzt) <= KLASSE_VERWENDET * len(rest):
            quellen = sum(1 for e in ents if e.get("plattform") == p and e["status"] == "aktiv"
                          and not (e.get("state_class") or "").startswith("total")
                          and (e["entity_id"] in ktx["energie"] or e["entity_id"] in ktx["helfer"]))
            gr = [buendel(l, f"Integration {p}"),
                  f"trifft alle {_anzahl(len(frei), 'Entität', 'Entitäten')} von {p} ohne eigene Kurzzeit (Zähler ausgenommen), auch künftige"]
            if rest and benutzt is not None:
                gr.append(f"von den übrigen {len(rest)} werden {len(benutzt)} irgendwo verwendet"
                          + ("; Verwendung nur teilweise bestimmbar" if verwendet is None else ""))
            gr += arten(l)
            if quellen:
                gr.append(f"⚠ trifft auch {_anzahl(quellen, 'Quelle', 'Quellen')} von Energie-Dashboard oder Helfern – in der Maske prüfen")
            vid = f"herkunft:{p}"
            v = neu(vid, "herkunft", f"Integration {p}", "herkunft", [p], kz, gr, f"Integration {p} – {rg.anzeige(kz)}",
                    "Empfehlung des RS DB HM: eine Regel je Integration statt vieler einzelner")
            if v and vid not in ignoriert:
                vergeben.update(x[0] for x in l)
    einzeln = [x for x in einzeln if x[0] not in vergeben]   # schwache ohne Bündel entfallen (Punkt 236)

    # gleichartige Einzelvorschläge (gleiche Kurzzeit) zu Mustern verdichten, die nur Kandidaten treffen
    for kz in sorted({k for _e, k, _g in einzeln}, key=_tage_von):
        l = [x for x in einzeln if x[1] == kz]
        if len(l) >= GRUPPE_MIN:
            for m in verdichte([x[0] for x in l], pl, nur_muster=True)["muster"]:
                if not any(c in m for c in "*?["):
                    continue
                drin = [x for x in l if fnmatch.fnmatchcase(x[0], m)]
                if len(drin) < GRUPPE_MIN:
                    continue
                v = neu(f"gruppe:{m}", "gruppe", f"Gruppe {m}", "glob", [m], kz,
                        [f"{len(drin)} Entitäten, alle ohne eigene Kurzzeit"] + arten(drin),
                        f"{m} – {rg.anzeige(kz)}", "Empfehlung des RS DB HM")
                if v:
                    vergeben.update(x[0] for x in drin)
    for eid, kz, gruende in einzeln:
        if eid not in vergeben:
            neu(f"entitaet:{eid}", "entitaet", eid, "entity", [eid], kz, gruende, f"{eid} – {rg.anzeige(kz)}",
                "Empfehlung des RS DB HM")

    # 3. verwaiste Entitäten: Punkt 239 – immer vorschlagen, solange keine aktive verwaist-Regel existiert (ohne Mengenschwelle
    # und Ersparnisprüfung, auch ohne Verwaiste: vorbeugend für künftige)
    if not any(r["typ"] == "verwaist" and r["aktiv"] for r in bestand["regeln"]):
        alt = [e for e in ents if rg.ist_verwaist(e["status"]) and not e.get("schutz")]
        menge = sum((e["rows"] or 0) + (e["kurz_rows"] or 0) for e in alt)
        if alt:
            jetzt = time.time()
            tage = [(jetzt - e["last_ts"]) / 86400 for e in alt if e.get("last_ts")]
            alter = (f", letzter Wert vor {round(min(tage))}–{round(max(tage))} Tagen" if tage and round(min(tage)) != round(max(tage))
                     else f", letzter Wert vor {round(tage[0])} Tagen" if tage else "")
            gr = [f"{_anzahl(len(alt), 'verwaiste Entität', 'verwaiste Entitäten')} ohne Regel, {_anzahl(menge, 'Zeile', 'Zeilen')} "
                  f"(States + 5-Min){alter}; sie bleiben sonst bis purge_keep_days"]
        else:
            gr = ["vorbeugend: derzeit keine verwaisten Entitäten – die Regel greift, sobald eine Entität verschwindet"]
        gr.append("Langzeit bleibt unverändert (unbegrenzt) – lässt sich in der Maske ändern")
        v = neu("verwaist:alle", "verwaist", "verwaiste Entitäten", "verwaist", ["alle"], TAGE["verwaist"], gr,
                "Verwaiste – 30 Tage", "Empfehlung des RS DB HM: 30 Tage Frist, falls eine Entität nur umbenannt wurde", immer=True)
        if v and alt:
            v["gruende"].insert(1, f"heute fällig: {_anzahl(v['loesch_zeilen'], 'Zeile', 'Zeilen')} – der Rest nach Ablauf der 30 Tage")

    # 4. Event-Typen mit viel Volumen ohne Event-Regel
    te = _tage_von(TAGE["event"])
    for ev in pl.get("events", []):
        if ev["pro_tag"] < EVENT_PRO_TAG or (ev["regel"] not in (None, rg.HA_STANDARD_ID)) or ev["tage"] <= te:
            continue
        typ = ev["event_type"]
        neu(f"event:{typ}", "event", f"Events {typ}", "event", [typ], TAGE["event"],
            [f"{ev['pro_tag']:,} Events je Tag ohne eigene Event-Regel".replace(",", "."),
             "Events braucht nur das Logbuch („Aktivität“)"], f"Events {typ} – 14 Tage", "Empfehlung des RS DB HM")

    # 5. Punkt 249: Langzeit (Stundenstatistik) nirgends verwendeter Entitäten – Sammelregel nur mit Langzeit; der Rest (auch künftige
    # Entitäten) wird über die Langzeit der Regel HA:standard aufgefangen („Lumpensammler“)
    if verwendet is not None:
        lz = TAGE["langzeit_unbenutzt"]
        lz_tage = _tage_von(lz)
        ids = []
        for e in ents:
            eid = e["entity_id"]
            if (e["status"] != "aktiv" or e.get("schutz") or (e.get("state_class") or "").startswith("total") or not e.get("lts_rows")
                    or eid in verwendet or e["domain"] in NIE_UNBENUTZT or eid in ktx["energie"] or eid in ktx["helfer"]):
                continue
            wl = (e["werte"] or {}).get("langzeit") or {}
            if wl.get("regel") not in (None, rg.HA_STANDARD_ID):
                continue
            if wl.get("tage") is None or wl["tage"] > lz_tage:
                ids.append(eid)
        if len(ids) >= GRUPPE_MIN:
            gewaehlt = set(ids)
            zeilen = sum(e["lts_rows"] for e in ents if e["entity_id"] in gewaehlt)
            gr = [f"{_anzahl(len(ids), 'Entität', 'Entitäten')} nirgends verwendet (nicht in Dashboards, Automationen, Skripten, Templates, Helfern, Energie), "
                  f"Langzeit heute unbegrenzt bzw. länger als {rg.anzeige(lz)} ({_anzahl(zeilen, 'Stundenzeile', 'Stundenzeilen')})",
                  "setzt nur die Langzeit – die Kurzzeit bleibt unverändert; Zähler, Energie-/Helfer-Quellen und geschützte Entitäten ausgenommen",
                  "künftige Entitäten erfasst die Regel nicht – dafür Langzeit der Regel HA:standard einstellen (Lumpensammler)"]
            neu("langzeit:unbenutzt", "langzeit", "Langzeit nirgends verwendeter Entitäten", "entity", sorted(ids), None, gr,
                f"Langzeit nirgends verwendet – {rg.anzeige(lz)}", "Empfehlung des RS DB HM: Stundenstatistik ohne Nutzen begrenzen",
                langzeit=lz)

    # 6. Punkt 254: Attribute mit großem Zuwachs – Sammelregel (entity, gilt auch für Zähler) nur mit Attribut-Wert; Kurzzeit und Langzeit
    # bleiben unverändert, die States werden nicht gelöscht. Nur Entitäten, deren Attribut-Wert heute vom Attribut-Standard kommt
    # (nicht von einer Regel – auch nicht von Schutz-Regeln mit „Attribute mitschützen“) und länger als der Vorschlag ist.
    at = TAGE["attribute"]
    at_tage = _tage_von(at)
    ziel_t = int(ziel or (pl.get("stand") or {}).get("ziel_tage") or hist["pkd"])
    gross = []
    for e in ents:
        wa = (e["werte"] or {}).get("attribute") or {}
        if e["status"] != "aktiv" or not e["rows_24h"] or wa.get("regel") is not None or wa.get("tage") is None or wa["tage"] <= at_tage:
            continue
        if (e.get("attr_zuwachs_tag") or 0) * min(wa["tage"], ziel_t) >= ATTR_PROGNOSE_MIN:
            gross.append(e)
    if gross:
        gross.sort(key=lambda e: -(e["attr_zuwachs_tag"] or 0))
        ids = sorted(e["entity_id"] for e in gross)
        summe_tag = sum(e["attr_zuwachs_tag"] for e in gross)
        top = ", ".join(f"{e['entity_id']} ({_de(e['attr_zuwachs_tag'] / 1e6)} MB/Tag)" for e in gross[:3])
        gr = [f"{_anzahl(len(ids), 'Entität', 'Entitäten')} mit großem Attribut-Zuwachs: zusammen {_de(summe_tag / 1e6)} MB je Tag, jede Entität allein "
              f"mindestens {ATTR_PROGNOSE_MIN / 1e6:.0f} MB im Prognosezeitraum",
              f"größte: {top}",
              "setzt nur den Attribut-Wert – Kurzzeit und Langzeit bleiben unverändert, die States werden nicht gelöscht; die Attribute werden nach der "
              "Frist von den States getrennt (braucht die Integration RS DB Hygiene ab 0.5.6b9)",
              "ausgenommen: Entitäten mit eigenem Attribut-Wert in einer Regel und Schutz-Regeln mit „Attribute mitschützen“"]
        neu("attribute:gross", "attribute", "Attribute mit großem Zuwachs", "entity", ids, None, gr,
            f"Attribute groß – A {rg.anzeige(at)}", "Empfehlung des RS DB HM: Attribute mit großem Zuwachs früher von den States trennen",
            attribut=at)

    out.sort(key=lambda v: (-(v["ersparnis_bytes"] or 0), -(v["zuwachs_tag"] or 0)))   # Punkt 207: bei Gleichstand Zuwachs
    return out


# ---------------------------------------------------------------- Wer schreibt am meisten? (Punkt 194)

PARETO_STUFEN = (10, 25, 50, 100, 250)


def pareto(pl, hist, ktx, ziel=None, tage=14):
    """Kandidaten wie bei den Vorschlägen (_frei: aktiv, ohne Schutz, keine Zähler, keine Energie-/Helfer-Quellen, ohne
    eigene Kurzzeit), deren wirksame Kurzzeit heute länger als `tage` ist – nach Zeilen je Tag sortiert. Je Stufe (Top N):
    Anteil am Zuwachs aller nicht verwaisten Entitäten, Schwelle (Zeilen/Tag der N-ten) und Ersparnis im
    Prognosezeitraum, wenn diese N auf `tage` gekürzt würden (Formel wie wirkung())."""
    pkd = hist["pkd"]
    ziel = int(ziel or (pl.get("stand") or {}).get("ziel_tage") or pkd)
    ents = pl.get("entitaeten", [])
    gesamt = sum(e["rows_24h"] or 0 for e in ents if not rg.ist_verwaist(e["status"]))
    kand = []
    for e in ents:
        n = e["rows_24h"] or 0
        alt = ((e["werte"] or {}).get("kurzzeit") or {}).get("tage")
        if not n or alt is None or alt <= tage or not _frei(e, ktx):
            continue
        lid = hist["lids"].get(e["entity_id"])
        pro_tag = n * pl_.B_STATE + (pl_.KURZ_PRO_TAG * pl_.B_KURZ if lid else 0) + (e.get("attr_zuwachs_tag") or 0)
        kand.append({"entity_id": e["entity_id"], "rows_24h": n, "kurzzeit_tage": alt,
                     "ersparnis_bytes": (min(alt, ziel) - min(tage, ziel)) * pro_tag})
    kand.sort(key=lambda x: -x["rows_24h"])
    stufen, summe_n, summe_b = [], 0, 0
    grenzen = [s for s in PARETO_STUFEN if s < len(kand)] + [len(kand)] if kand else []
    for i, k in enumerate(kand, 1):
        summe_n += k["rows_24h"]
        summe_b += k["ersparnis_bytes"]
        if i in grenzen:
            stufen.append({"top": i, "alle": i == len(kand), "zeilen_tag": summe_n,
                           "anteil": round(100 * summe_n / gesamt, 1) if gesamt else 0,
                           "schwelle": k["rows_24h"], "ersparnis_bytes": summe_b})
    return {"tage": tage, "ziel_tage": ziel, "gesamt_zeilen_tag": gesamt, "anzahl": len(kand), "stufen": stufen,
            "kandidaten": kand[:max(PARETO_STUFEN)]}


def lies_ignoriert(daten_ordner):
    d = _json(os.path.join(daten_ordner, IGNORIERT_DATEI))
    return set(d) if isinstance(d, list) else set()


def setze_ignoriert(daten_ordner, vid, an):
    s = lies_ignoriert(daten_ordner)
    (s.add if an else s.discard)(vid)
    pfad = os.path.join(daten_ordner, IGNORIERT_DATEI)
    with open(pfad + ".tmp", "w", encoding="utf-8") as f:
        json.dump(sorted(s), f, ensure_ascii=False, indent=1)
    os.replace(pfad + ".tmp", pfad)
    return s


# ---------------------------------------------------------------- Muster verdichten (Punkt 171)

def _kandidaten(eid):
    """Präfix- und Endungsmuster einer Entity-ID an den Grenzen „_“ (z. B. sensor.a_b_c → sensor.a_*, sensor.a_b_*,
    sensor.*_c, sensor.*_b_c)."""
    dom, name = eid.split(".", 1)
    teile = name.split("_")
    out = []
    for i in range(1, len(teile)):
        out.append(f"{dom}.{'_'.join(teile[:i])}_*")
        out.append(f"{dom}.*_{'_'.join(teile[i:])}")
    return out


def verdichte(ids, pl, nur_muster=False):
    """Muster mit Platzhaltern für eine Auswahl. glob wirkt nur auf aktive Entitäten ohne Zähler – andere bleiben außen
    vor und werden genannt. Liefert {muster, genau, zusaetzlich, fehlend, ausserhalb}; nur_muster: ohne Gegenprobe."""
    ents = pl.get("_ent_index")
    if ents is None:
        ents = pl["_ent_index"] = {e["entity_id"]: e for e in pl.get("entitaeten", [])}
    glob_ok = lambda e: e and not rg.ist_verwaist(e["status"]) and not (e.get("state_class") or "").startswith("total")  # noqa: E731
    wahl = {i for i in ids if glob_ok(ents.get(i))}
    ausserhalb = sorted(set(ids) - wahl)
    if len(wahl) < 2:
        return {"muster": sorted(wahl), "genau": True, "zusaetzlich": [], "fehlend": [], "ausserhalb": ausserhalb}
    alle = {}
    for eid, e in ents.items():
        if glob_ok(e):
            for k in _kandidaten(eid):
                alle[k] = alle.get(k, 0) + 1
    deckt = {}
    for eid in wahl:
        for k in _kandidaten(eid):
            deckt.setdefault(k, set()).add(eid)
    offen, muster = set(wahl), []
    while offen:
        # nur Muster ohne fremde Treffer; das mit der größten Abdeckung, bei Gleichstand das längere (genauere –
        # z. B. switch.adaptive_lighting_* statt switch.adaptive_*, damit spätere fremde Entitäten nicht hineinrutschen)
        best = max(((k, len(s & offen)) for k, s in deckt.items() if alle[k] == len(s) and len(s & offen) >= 2),
                   key=lambda t: (t[1], len(t[0])), default=None)
        if not best:
            break
        muster.append(best[0])
        offen -= deckt[best[0]]
    muster = sorted(muster) + sorted(offen)
    if nur_muster:
        return {"muster": muster}
    # Gegenprobe über alle Entitäten
    treffer = {eid for eid, e in ents.items() if glob_ok(e) and any(fnmatch.fnmatchcase(eid, m) for m in muster)}
    return {"muster": muster, "genau": treffer == wahl, "zusaetzlich": sorted(treffer - wahl)[:50],
            "fehlend": sorted(wahl - treffer)[:50], "ausserhalb": ausserhalb, "vorher": len(ids), "nachher": len(muster)}
