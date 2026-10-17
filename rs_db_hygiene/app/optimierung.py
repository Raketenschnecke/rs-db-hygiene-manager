"""Empfehlungen (Punkt 176, Reiter bis 0.5.4b7 „Optimierungen“) und Wirkung einer Regel (Punkt 170) – liest nur.

Alles rechnet auf dem Probelauf (Analyse-Cache + aktuelle Regeln), ohne neue Abfrage der HA-Datenbank:

  wirkung()     Was eine Regel im Regelwerk bewirkt, verglichen mit den heutigen Regeln: Treffer, Überstimmungen,
                zusätzliche Löschmenge sofort (aus den Tageshistogrammen) und Ersparnis im Prognosezeitraum.
  vorschlaege() Regelvorschläge nach der Analyse: Gruppen (Diagnose-Endungen), einzelne Entitäten (Vielschreiber,
                große Attribute, nirgends verwendet, deaktiviert, Uhrzeit), zusammengefasst je Klasse (Punkt 208), je
                Integration (Punkt 205) oder Namensmuster; wo klasse/herkunft nicht sicher sind, eine Sammelregel mit den
                unbenutzten Entitäten (Punkte 233/234); verwaiste ohne Regel, Event-Typen ohne Event-Regel.
                Leitplanken: nie Energie- oder Helfer-Quellen, nie geschützte Entitäten oder Zähler, nie die Langzeit;
                nur wo heute keine eigene Kurzzeit gilt (Standard bzw. HA-Standard) und der Vorschlag kürzer ist.
  verdichte()   Punkt 171: Muster mit Platzhaltern, die genau eine Auswahl von Entity-IDs treffen.
"""
import fnmatch
import json
import os
import re
import sqlite3
import time

import herkunft
import probelauf as pl_
import regeln as rg

# Schwellen der Vorschläge
VIEL_PRO_TAG = 2000          # ab hier „Vielschreiber“ (≈ alle 43 s ein neuer Wert)
VIEL_SEHR = 10000            # ab hier Vorschlag 14 statt 30 Tage
ATTR_PRO_TAG = 2_000_000     # Attribute ab 2 MB je Tag
UNBENUTZT_PRO_TAG = 500      # nirgends verwendet und mindestens so viele Zeilen je Tag
UNBENUTZT_BUENDEL = 100      # Punkt 236: ab hier zählen unbenutzte Entitäten für Bündel (klasse, herkunft, Sammelregel) mit
GRUPPE_MIN = 3               # Mindestzahl Entitäten einer Gruppe
GRUPPE_PRO_TAG = 1000        # Mindestzuwachs einer Gruppe (Zeilen je Tag)
EVENT_PRO_TAG = 1000         # Event-Typen ab so vielen Events je Tag
TAGE = {"gruppe": "7d", "viel": "30d", "viel_sehr": "14d", "attribute": "30d", "unbenutzt": "14d", "deaktiviert": "30d",
        "verwaist": "30d", "event": "14d", "herkunft": "30d", "uhrzeit": "1d"}
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
_ID = re.compile(r"\b([a-z_][a-z0-9_]*\.[a-z0-9_]+)\b")
REFERENZ_DATEIEN = ("lovelace", "lovelace_dashboards", "energy", "core.config_entries", "automations", "scripts", "scenes")
IGNORIERT_DATEI = "optimierungen_ignoriert.json"


# ---------------------------------------------------------------- Histogramme (für die Löschmenge sofort)

_HIST = {"schl": None}


def histogramme(cache_pfad):
    """Tageshistogramme States, 5-Min, Langzeit und heute_tag/pkd – je Cache-Stand einmal gelesen."""
    schl = os.path.getmtime(cache_pfad) if os.path.exists(cache_pfad) else None
    if _HIST["schl"] != schl:
        con = sqlite3.connect(f"file:{cache_pfad}?mode=ro", uri=True)
        info = dict(con.execute("SELECT schluessel, wert FROM info"))
        _HIST.update(schl=schl, heute=int(info["heute_tag"]), pkd=int(info["purge_keep_days"]),
                     st=pl_._histogramm(con, "SELECT metadata_id, tag, n FROM st_tage"),
                     kurz=pl_._histogramm(con, "SELECT lts_id, tag, n FROM kurz_tage"),
                     lts=pl_._histogramm(con, "SELECT lts_id, tag, n FROM lts_tage"),
                     ids=dict(con.execute("SELECT entity_id, metadata_id FROM entitaet")),
                     lids=dict(con.execute("SELECT entity_id, lts_id FROM entitaet")))
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
         "loesch_lts": 0, "loesch_bytes": 0, "ersparnis_bytes": 0, "verlaengert": 0}
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
                pro_tag = (e["rows_24h"] or 0) * pl_.B_STATE + (pl_.KURZ_PRO_TAG * pl_.B_KURZ if lid else 0) + (e.get("attr_zuwachs_tag") or 0)
                w["ersparnis_bytes"] += d * pro_tag
                w["verlaengert"] += d < 0
        if g["langzeit"]["regel"] == regel["id"] and lid and neu["langzeit"] is not None:
            ll = max(0, pl_._aelter(hist["lts"], lid, heute - neu["langzeit"]) - (e["loeschbar"]["lts"] or 0))
            w["loesch_lts"] += ll
            w["loesch_bytes"] += ll * pl_.B_LTS
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


_VERWENDET = {"zeit": 0, "ids": None}


def verwendete_ids(config):
    """Alle Entity-IDs, die irgendwo in der Konfiguration genannt werden: Dashboards, Automationen, Skripte, Szenen,
    Templates und Helfer (UI und YAML), Energie-Dashboard. Grob, aber auf der sicheren Seite: jedes Vorkommen zählt.
    5 min zwischengespeichert."""
    if _VERWENDET["ids"] is not None and time.time() - _VERWENDET["zeit"] < 300:
        return _VERWENDET["ids"]
    texte = []
    st = os.path.join(config, ".storage")
    try:
        for name in os.listdir(st):
            if name.startswith(REFERENZ_DATEIEN):
                try:
                    with open(os.path.join(st, name), encoding="utf-8", errors="ignore") as f:
                        texte.append(f.read())
                except OSError:
                    pass
    except OSError:
        pass
    for pfad in herkunft.yaml_dateien(config):
        try:
            with open(pfad, encoding="utf-8", errors="ignore") as f:
                texte.append(f.read())
        except OSError:
            pass
    ids = set()
    for t in texte:
        ids.update(_ID.findall(t))
    _VERWENDET.update(zeit=time.time(), ids=ids)
    return ids


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
    try:
        verwendet = verwendete_ids(config)
    except Exception:  # noqa: BLE001 – ohne Verwendungsprüfung entfallen nur diese Vorschläge
        verwendet = None
    regeln_muster = {(r["typ"], tuple(sorted(r["muster"]))): r for r in bestand["regeln"]}
    passive = [{**r, "_aufl": rg.Aufloeser({"standard": rg.STANDARD, "regeln": [{**r, "aktiv": True}]})}
               for r in bestand["regeln"] if not r["aktiv"] and r["typ"] not in ("event", "ha_standard") and r.get("kurzzeit")]
    out, vergeben = [], set()

    def kurz_wirksam(e):
        return ((e["werte"] or {}).get("kurzzeit") or {}).get("tage")

    ablehnung = {"grund": None}   # warum neu() zuletzt nichts vorgeschlagen hat (Begründung der Sammelregel, Punkt 233)

    def neu(vid, art, titel, typ, muster, kurzzeit, gruende, name, notiz="", immer=False):
        regel = {"id": "VORSCHLAG", "name": name, "typ": typ, "muster": muster, "aktiv": False, "notiz": notiz,
                 "kurzzeit": kurzzeit, "attribute": None, "langzeit": None}
        regel["name"] = rg.name_mit_suffix(name, regel)   # Punkt 241: „– 14T/∞“ statt „– 14 Tage“
        norm = rg.normalisiere({"regeln": [regel], "events": {}})["regeln"][0]
        w = event_wirkung(norm, pl, bestand, hist, ziel=ziel) if typ == "event" else wirkung(norm, pl, bestand, hist, ziel=ziel, mit_ents=True)
        ablehnung["grund"] = None
        if not immer and (not w["gewinnt"] or w["ersparnis_bytes"] <= 0 and not w["loesch_bytes"]):   # immer: Punkt 239
            ablehnung["grund"] = "verlaengert" if w.get("verlaengert") else "spart_nichts"
            return None
        # Punkte 205/208: herkunft/klasse haben Rang vor vielschreiber – nie vorschlagen, wenn sie irgendwo verlängern
        if typ in ("herkunft", "klasse") and (w.get("verlaengert") or w["ersparnis_bytes"] <= 0):
            ablehnung["grund"] = "verlaengert" if w.get("verlaengert") else "spart_nichts"
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
             "loesch_zeilen": w.get("loesch_states", 0) + w.get("loesch_kurz", 0) + w.get("loesch_lts", 0) + w.get("loesch_events", 0),
             "beispiele": [t["entity_id"] for t in w["treffer"][:6]]}
        out.append(v)
        return v

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
        if ri.get("deaktiviert") and (e["rows"] or 0) > 0:
            gruende.append("in HA deaktiviert – schreibt nichts mehr, Altdaten bleiben sonst bis purge_keep_days")
            tage.append(TAGE["deaktiviert"])
        if n >= VIEL_PRO_TAG:
            gruende.append(f"Vielschreiber: {n:,} Zeilen je Tag (Ø 7 volle Tage, ≈ alle {max(1, round(86400 / n))} s)".replace(",", "."))
            tage.append(TAGE["viel_sehr" if n >= VIEL_SEHR else "viel"])
        if (e.get("attr_zuwachs_tag") or 0) >= ATTR_PRO_TAG:
            gruende.append(f"große Attribute: {e['attr_zuwachs_tag'] / 1e6:.1f} MB je Tag".replace(".", ","))
            tage.append(TAGE["attribute"])
        if unbenutzt and n >= UNBENUTZT_PRO_TAG:
            gruende.append("nirgends verwendet (nicht in Dashboards, Automationen, Skripten, Templates, Helfern, Energie)")
            tage.append(TAGE["unbenutzt"])
        if (e.get("plattform") or ri.get("platform")) in UHRZEIT_PLATTFORMEN and n:   # Punkt 208
            gruende.append("Uhrzeit/Datum: der Verlauf hat keinen Nutzen")
            tage.append(TAGE["uhrzeit"])
        if not gruende:
            if unbenutzt and n >= UNBENUTZT_BUENDEL and (kurz_wirksam(e) or 0) > _tage_von(TAGE["unbenutzt"]):
                schwach.append((eid, TAGE["unbenutzt"], ["nirgends verwendet, unter 500 Zeilen je Tag (nur im Bündel)"]))
            continue
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

    def sammelregel(schl, wo, frei, rest, benutzt, l, kz, typ):
        """Punkte 233/234: die Muster-Regel (klasse/herkunft) ist nicht möglich – zu viele der übrigen werden verwendet oder
        sie verlängert irgendwo → Sammelregel (entity) mit allen unbenutzten freien Entitäten, die Daten haben. Trifft keine
        verwendete und verlängert nie (nur Entitäten ohne eigene Kurzzeit); künftige Entitäten erfasst sie nicht."""
        anzeige = "Integrations-" if typ == "herkunft" else f"{typ}-"   # Punkt 246
        ids = sorted(e["entity_id"] for e in frei if e["entity_id"] not in verwendet and (e["rows"] or 0) > 0)
        if len(ids) < GRUPPE_MIN:
            return
        if len(benutzt) > KLASSE_VERWENDET * len(rest):
            grund = (f"{anzeige}Regel nicht möglich: von den übrigen {len(rest)} werden {len(benutzt)} verwendet"
                     + (f" (z. B. {', '.join(benutzt[:3])})" if benutzt else ""))
        elif ablehnung["grund"] == "verlaengert":
            grund = f"{anzeige}Regel nicht möglich: sie verlängert die Kurzzeit von Entitäten mit kürzerer Regel (Rang)"
        else:
            grund = f"{anzeige}Regel nicht möglich: sie spart nichts"
        gr = [buendel(l, wo), grund,
              f"enthält alle {_anzahl(len(ids), 'unbenutzte Entität', 'unbenutzten Entitäten')} ohne eigene Kurzzeit "
              "(Zähler ausgenommen) – künftige nicht"]
        vid = f"sammel:{schl}"
        v = neu(vid, "sammel", f"{wo}, nirgends verwendet", "entity", ids, kz, gr + arten(l),
                f"{wo}, nirgends verwendet – {rg.anzeige(kz)}", "Empfehlung des RS DB HM: alle unbenutzten Entitäten in einer Regel")
        if v and vid not in ignoriert:
            vergeben.update(ids)

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
        if verwendet is None:
            if len(l) < 0.5 * len(frei):
                continue
            benutzt = None
        else:
            benutzt = [e["entity_id"] for e in rest if e["entity_id"] in verwendet]
        v = None
        if benutzt is None or len(benutzt) <= KLASSE_VERWENDET * len(rest):
            gr = [buendel(l, f"Klasse {k}"),
                  f"trifft alle {_anzahl(len(frei), 'Entität', 'Entitäten')} dieser Klasse ohne eigene Kurzzeit (Zähler ausgenommen), auch künftige"]
            if rest and benutzt is not None:
                gr.append(f"von den übrigen {len(rest)} werden {len(benutzt)} irgendwo verwendet")
            vid = f"klasse:{k}"
            v = neu(vid, "klasse", f"Klasse {k}", "klasse", [k], kz, gr + arten(l), f"Klasse {k} – {rg.anzeige(kz)}",
                    "Empfehlung des RS DB HM")
            if v and vid not in ignoriert:
                vergeben.update(x[0] for x in l)
        if not v and benutzt is not None:
            sammelregel(f"klasse:{k}", f"Klasse {k}", frei, rest, benutzt, l, kz, "klasse")

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
        benutzt = None if verwendet is None or uhr else [e["entity_id"] for e in rest if e["entity_id"] in verwendet]
        v = None
        if benutzt is None or len(benutzt) <= KLASSE_VERWENDET * len(rest):
            quellen = sum(1 for e in ents if e.get("plattform") == p and e["status"] == "aktiv"
                          and not (e.get("state_class") or "").startswith("total")
                          and (e["entity_id"] in ktx["energie"] or e["entity_id"] in ktx["helfer"]))
            gr = [buendel(l, f"Integration {p}"),
                  f"trifft alle {_anzahl(len(frei), 'Entität', 'Entitäten')} von {p} ohne eigene Kurzzeit (Zähler ausgenommen), auch künftige"]
            if rest and benutzt is not None:
                gr.append(f"von den übrigen {len(rest)} werden {len(benutzt)} irgendwo verwendet")
            gr += arten(l)
            if quellen:
                gr.append(f"⚠ trifft auch {_anzahl(quellen, 'Quelle', 'Quellen')} von Energie-Dashboard oder Helfern – in der Maske prüfen")
            vid = f"herkunft:{p}"
            v = neu(vid, "herkunft", f"Integration {p}", "herkunft", [p], kz, gr, f"Integration {p} – {rg.anzeige(kz)}",
                    "Empfehlung des RS DB HM: eine Regel je Integration statt vieler einzelner")
            if v and vid not in ignoriert:
                vergeben.update(x[0] for x in l)
        if not v and benutzt is not None:
            sammelregel(f"herkunft:{p}", f"Integration {p}", frei, rest, benutzt, l, kz, "herkunft")
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
