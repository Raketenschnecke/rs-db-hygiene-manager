"""Warnungen vor riskanten Regeln und Läufen (Punkt 54) – liest nur.

Gelöscht wird nie etwas Inkonsistentes, aber Daten können fehlen, die HA anderswo braucht:
  - Langzeit kurz oder „keine“ bei Zählern (total/total_increasing): Energie-Dashboard verliert Historie
  - Kurzzeit sehr kurz: Verlauf leer
  - Quellen von Helfern, die aus dem Verlauf rechnen (history_stats, statistics, filter, trend): falsche Werte
  - Event-Regeln mit breiten Platzhaltern: treffen auch System-Events
derivative, integration und utility_meter rechnen aus laufenden Änderungen und sind nicht betroffen.
"""
import fnmatch
import json
import os
import re
import time

import herkunft
import regeln as rg

KURZ_MIN_TAGE = 7
LANG_MIN_TAGE = 365
HELFER_PLATTFORMEN = ("history_stats", "statistics", "filter", "trend")
SYSTEM_EVENTS = ("homeassistant_", "component_loaded", "core_config_updated", "service_", "entity_registry_updated",
                 "device_registry_updated", "area_registry_updated", "floor_registry_updated", "label_registry_updated",
                 "category_registry_updated", "repairs_issue_registry_updated", "recorder_", "user_", "panels_updated",
                 "themes_updated", "lovelace_updated", "data_entry_flow_progressed", "config_entry_discovered",
                 "logbook_entry", "persistent_notifications_updated")
_CACHE = {"zeit": 0, "kontext": None}


def _json(pfad):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def energie_quellen(config):
    """Alle statistic_ids, die das Energie-Dashboard nutzt (.storage/energy, Felder stat_*)."""
    ids = set()

    def sammle(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k.startswith("stat_") and isinstance(v, str):
                    ids.add(v)
                else:
                    sammle(v)
        elif isinstance(o, list):
            for x in o:
                sammle(x)
    sammle(_json(os.path.join(config, ".storage", "energy")).get("data", {}))
    return ids


_YAML_PLATTFORM = re.compile(r"^(\s*)-?\s*platform:\s*(" + "|".join(HELFER_PLATTFORMEN) + r")\b", re.M)
_YAML_QUELLE = re.compile(r"^\s*(?:entity_id|source):\s*['\"]?([a-z0-9_]+\.[a-z0-9_]+)", re.M)


def helfer_quellen(config):
    """{entity_id: [„history_stats (UI: Name)“ bzw. „statistics (sensors.yaml)“, …]} – UI-Helfer und YAML."""
    out = {}
    for e in _json(os.path.join(config, ".storage", "core.config_entries")).get("data", {}).get("entries", []):
        if e.get("domain") in HELFER_PLATTFORMEN:
            o = e.get("options") or {}
            for q in (o.get("entity_id"), o.get("source")):
                for x in (q if isinstance(q, list) else [q]):
                    if isinstance(x, str) and "." in x:
                        out.setdefault(x, []).append(f"{e['domain']} (UI: {e.get('title') or o.get('name') or '?'})")
    for pfad in herkunft.yaml_dateien(config):
        try:
            with open(pfad, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        treffer = list(_YAML_PLATTFORM.finditer(text))
        for i, m in enumerate(treffer):
            block = text[m.end(): treffer[i + 1].start() if i + 1 < len(treffer) else m.end() + 1500]
            block = re.split(r"\n\s*-\s*platform:", block)[0]
            for q in _YAML_QUELLE.findall(block):
                out.setdefault(q, []).append(f"{m.group(2)} ({os.path.relpath(pfad, config)})")
    return out


def kontext(config="/homeassistant"):
    """Energie-Quellen und Helfer-Quellen, 5 min zwischengespeichert."""
    if not _CACHE["kontext"] or time.time() - _CACHE["zeit"] > 300:
        _CACHE.update(zeit=time.time(), kontext={"energie": energie_quellen(config), "helfer": helfer_quellen(config)})
    return _CACHE["kontext"]


def _tage(wert):
    if wert in (None, "", "global", "unbegrenzt", "wie_kurzzeit"):
        return None
    return 0 if wert == "keine" else rg.in_tagen(wert)


def _liste(xs, n=6):
    xs = sorted(xs)
    return ", ".join(xs[:n]) + (f" … (+{len(xs) - n})" if len(xs) > n else "")


def pruefe_regel(regel, pl, ktx):
    """Warnungen für eine (normalisierte) Regel gegen die Entitäten bzw. Events des Probelaufs."""
    w = []
    if regel["typ"] == "event":
        breit = [m for m in regel["muster"] if any(c in m for c in "*?[")]
        if breit:
            typen = [e["event_type"] for e in pl.get("events", []) if any(fnmatch.fnmatchcase(e["event_type"], m) for m in regel["muster"])]
            system = [t for t in typen if t.startswith(SYSTEM_EVENTS)]
            if len(typen) > 3 or system:
                w.append({"art": "event_breit", "text": f"Das Muster {', '.join(breit)} trifft {len(typen)} Event-Typen"
                          + (f", darunter System-Events ({_liste(system, 4)})" if system else "")
                          + ". Ihr Logbuch-Verlauf wird mit gekürzt.", "entitaeten": typen})
        return w
    allein = rg.Aufloeser({"standard": rg.STANDARD, "regeln": [{**regel, "aktiv": True}]})
    k, lz = _tage(regel.get("kurzzeit")), _tage(regel.get("langzeit"))
    kurz, zaehler, energie, helfer = [], [], [], {}
    for e in pl.get("entitaeten", []):
        x = allein.loese_e(e)
        if not any(x[a]["regel"] for a in rg.WERTE):
            continue
        eid = e["entity_id"]
        if k is not None and k < KURZ_MIN_TAGE and regel["typ"] != "verwaist" and x["kurzzeit"]["regel"]:
            kurz.append(eid)
        if lz is not None and lz < LANG_MIN_TAGE and x["langzeit"]["regel"] and (e.get("state_class") or "").startswith("total"):
            (energie if eid in ktx["energie"] else zaehler).append(eid)
        if eid in ktx["helfer"] and x["kurzzeit"]["regel"] and k is not None:
            helfer[eid] = ktx["helfer"][eid]
    if kurz:
        w.append({"art": "kurzzeit_kurz", "text": f"Kurzzeit unter {KURZ_MIN_TAGE} Tagen bei {len(kurz)} Entitäten ({_liste(kurz)}): "
                  "Verlaufsdiagramme zeigen danach nur noch wenige Tage.", "entitaeten": kurz})
    if energie:
        w.append({"art": "energie", "text": f"Langzeit {rg.anzeige(regel['langzeit'])} bei {len(energie)} Quellen des Energie-Dashboards "
                  f"({_liste(energie)}): Dort verschwindet die Historie vor dieser Grenze endgültig.", "entitaeten": energie})
    if zaehler:
        w.append({"art": "zaehler", "text": f"Langzeit {rg.anzeige(regel['langzeit'])} bei {len(zaehler)} Zählern (state_class total) "
                  f"({_liste(zaehler)}): Die Langzeit-Historie der Zähler geht vor dieser Grenze verloren.", "entitaeten": zaehler})
    # Punkt 111: Treffer, die eine Schutz-Regel ohnehin festhält – dort wirkt diese Regel nicht
    if regel["typ"] != "schutz":
        geschuetzt = {}
        for e in pl.get("entitaeten", []):
            if e.get("schutz") and e.get("schutz") != regel.get("id"):
                x = allein.loese_e(e)
                if any(x[a]["regel"] for a in rg.WERTE):
                    geschuetzt.setdefault(e["schutz"], []).append(e["entity_id"])
        for sid, ids in geschuetzt.items():
            w.append({"art": "schutz", "text": f"{len(ids)} Treffer sind durch die Schutz-Regel {sid} geschützt ({_liste(ids)}): "
                      "Dort wirkt diese Regel nicht – Kurzzeit global und Langzeit unbegrenzt bleiben.", "entitaeten": ids})
    if helfer:
        w.append({"art": "helfer", "text": f"{len(helfer)} Entitäten sind Quelle von Helfern, die aus dem Verlauf rechnen: "
                  + "; ".join(f"{eid} → {', '.join(h)}" for eid, h in list(helfer.items())[:4]) + (" …" if len(helfer) > 4 else "")
                  + ". Die Kurzzeit muss mindestens deren Zeitfenster abdecken (max_age, duration), sonst rechnen sie falsch.",
                  "entitaeten": list(helfer)})
    return w


def risiken_plan(p, pl, ktx):
    """Zusammenfassung „Risiken dieses Laufs“ für den Tab Löschen."""
    r = []
    sc = {e["entity_id"]: (e.get("state_class") or "") for e in pl.get("entitaeten", [])}
    im_lauf = [c for c in p.get("entitaeten", []) if c.get("keep_days")]
    kurz = [c["entity_id"] for c in im_lauf if c.get("kurzzeit_tage", 99) < KURZ_MIN_TAGE]
    if kurz:
        r.append({"text": f"{len(kurz)} Entitäten behalten weniger als {KURZ_MIN_TAGE} Tage Verlauf", "beispiele": kurz[:6], "anzahl": len(kurz)})
    helfer = [c["entity_id"] for c in im_lauf if c["entity_id"] in ktx["helfer"]]
    if helfer:
        r.append({"text": f"{len(helfer)} Entitäten sind Quelle von Helfern mit Verlauf (history_stats, statistics, filter, trend)",
                  "beispiele": helfer[:6], "anzahl": len(helfer)})
    b = [a for a in p.get("auftraege_b", []) if a.get("art_lauf") not in ("nächster Lauf", "übersprungen")]
    lts = [a for a in b if a["art"] == "lts" and sc.get(a["name"], "").startswith("total")]
    en = [a["name"] for a in lts if a["name"] in ktx["energie"]]
    if en:
        r.append({"text": f"{len(en)} Quellen des Energie-Dashboards verlieren Langzeit-Historie", "beispiele": en[:6], "anzahl": len(en), "schwer": True})
    rest = [a["name"] for a in lts if a["name"] not in ktx["energie"]]
    if rest:
        r.append({"text": f"{len(rest)} Zähler (state_class total) verlieren Langzeit-Historie", "beispiele": rest[:6], "anzahl": len(rest)})
    alle = [a["name"] for a in b if a["art"] == "lts" and a.get("alle")]
    if alle:
        r.append({"text": f"{len(alle)} Statistiken werden vollständig gelöscht (Langzeit „keine“)", "beispiele": alle[:6], "anzahl": len(alle)})
    sysev = [a["name"] for a in b if a["art"] == "events" and a["name"].startswith(SYSTEM_EVENTS)]
    if sysev:
        r.append({"text": f"{len(sysev)} System-Event-Typen werden gekürzt", "beispiele": sysev[:6], "anzahl": len(sysev)})
    aus = [c["entity_id"] for c in p.get("entitaeten", []) if c.get("ausnahme")]
    if aus:
        r.append({"text": f"{len(aus)} Entitäten überschreiten einmalig die Obergrenze (Ausnahme)", "beispiele": aus[:6], "anzahl": len(aus), "schwer": True})
    return r


def _tage_text(n, dativ=False):
    """7300 → „7.300 Tage (20 Jahre)“, 400 → „400 Tage“ (wie tageJahre in der GUI, nur ganze Jahre).

    dativ=True (nach „mit“, „nach“; Punkt 88): „7.300 Tagen (20 Jahren)“; Einzahl bleibt „1 Tag“ bzw. „1 Jahr“."""
    tage = "Tag" if n == 1 else ("Tagen" if dativ else "Tage")
    t = f"{n:,} {tage}".replace(",", ".")
    if n >= 365 and n % 365 == 0:
        j = n // 365
        t += f" ({j} {'Jahr' if j == 1 else ('Jahren' if dativ else 'Jahre')})"
    return t


PKD_MIN_EINRICHTUNG = 3650   # Punkt 133: mindestens 10 Jahre


def einrichtung_pruefung(recorder):
    """Punkt 133 (0.5.3): Ist der Recorder so eingerichtet, wie die App es voraussetzt?

    (1) Einträge: auto_purge und auto_repack auf false, purge_keep_days gesetzt; db_url darf fehlen (HA nimmt dann
    home-assistant_v2.db), wenn gesetzt, muss es SQLite auf home-assistant_v2.db sein (!secret wird nicht geprüft).
    (2) purge_keep_days mindestens 3650 Tage. Liefert eine Warnung {art: "einrichtung", …} oder None.
    Die GUI zeigt sie mit Link auf die FAQ „Erstinbetriebnahme“ (`hilfe`), sperrt aber nichts.
    """
    gesetzt = recorder.get("gesetzt") or []
    maengel = []
    if not recorder.get("block"):
        maengel.append("Abschnitt recorder: nicht gefunden (HA-Standard: alles nach 10 Tagen löschen)")
    else:
        for k in ("auto_purge", "auto_repack"):
            if k not in gesetzt:
                maengel.append(f"{k} fehlt (HA-Standard: an)")
            elif recorder.get(k):
                maengel.append(f"{k} ist an statt false")
        if "purge_keep_days" not in gesetzt:
            maengel.append("purge_keep_days fehlt (HA-Standard: 10 Tage)")
        url = recorder.get("db_url")
        if url and not url.startswith("!") and not (url.startswith("sqlite:") and url.endswith("/home-assistant_v2.db")):
            maengel.append(f"db_url zeigt nicht auf die SQLite-Datei home-assistant_v2.db ({url.split('://')[0]})")
    pkd = int(recorder.get("purge_keep_days") or 10)
    if pkd < PKD_MIN_EINRICHTUNG and ("purge_keep_days" in gesetzt):
        maengel.append(f"purge_keep_days {_tage_text(pkd)} statt mindestens {_tage_text(PKD_MIN_EINRICHTUNG)}")
    if not maengel:
        return None
    return {"art": "einrichtung", "titel": "Recorder nicht für die App eingerichtet:", "maengel": maengel,
            "text": " · ".join(maengel) + ".",
            "hilfe": "h-faq-erstinbetrieb", "hilfe_text": "Hilfe: Erstinbetriebnahme"}


def pkd_pruefung(recorder, option_pkd, regel_daten, beispiele=5):
    """Punkt 85: Ist purge_keep_days in der configuration.yaml kleiner als das, was die App erwartet?

    (a) App-Option purge_keep_days größer als der HA-Wert – ab 0.4.8 nur noch ein Hinweis, die Option wird dann ignoriert (Punkt 103), (b) aktive Regeln, deren Kurzzeit bzw. Vorhaltezeit (Events)
    länger ist als der HA-Wert. Die Langzeit ist nicht betroffen: HA löscht Langzeitstatistik nie selbst.
    Liefert eine Liste {art, titel, text}; leer = alles passt. Ab 0.4.4 (Punkt 89) steht der Titel getrennt vom Text,
    die GUI zeigt je Warnung ein eigenes Banner mit fettem Titel.
    """
    ha, auto = int(recorder["purge_keep_days"]), recorder.get("auto_purge", True)
    wann = ("automatisch (auto_purge ist an)" if auto
            else "bei jedem Aufruf von recorder.purge ohne keep_days (auto_purge ist aus)")
    out = []
    w = einrichtung_pruefung(recorder)
    if w:
        out.append(w)
    opt = int(option_pkd or 0)
    if opt > ha:
        # Punkt 103 (0.4.8): Die configuration.yaml hat Vorrang – ein größerer Wert in der App-Option wird ignoriert
        out.append({"art": "option", "ha": ha, "app": opt, "titel": "purge_keep_days:", "text":
                    f"Die App-Option purge_keep_days ({_tage_text(opt)}) ist größer als der Wert in der configuration.yaml "
                    f"({_tage_text(ha)}) und wird ignoriert – die configuration.yaml hat Vorrang, die App rechnet mit {_tage_text(ha, True)}. "
                    "So verschwindet der Hinweis: die App-Option auf 0 stellen (oder auf einen kleineren Wert, wenn die App bewusst "
                    "kürzer rechnen soll)."})
    zu_lang = []
    std = (regel_daten.get("standard") or {}).get("kurzzeit")
    if rg.in_tagen(std) and rg.in_tagen(std) > ha:
        zu_lang.append(("Standard", f"Kurzzeit {rg.anzeige(std)}", rg.in_tagen(std)))
    for r in regel_daten.get("regeln", []):
        t = rg.in_tagen(r.get("kurzzeit"))
        if r.get("aktiv", True) and t and t > ha:
            art = "Vorhaltezeit (Events)" if r["typ"] == "event" else "Kurzzeit"
            zu_lang.append((f"{r['id']}" + (f" {r['name']}" if r.get("name") and r["name"] != r["id"] else ""),
                            f"{art} {rg.anzeige(r['kurzzeit'])}", t))
    if zu_lang:
        zu_lang.sort(key=lambda x: -x[2])
        n, groesste = len(zu_lang), zu_lang[0][2]
        liste = "; ".join(f"{name} ({was})" for name, was, _t in zu_lang[:beispiele]) + (f" und {n - beispiele} weitere" if n > beispiele else "")
        out.append({"art": "regeln", "ha": ha, "anzahl": n, "groesste": groesste, "titel": "Regeln länger als purge_keep_days:", "text":
                    f"{n} aktive {'Regel will' if n == 1 else 'Regeln wollen'} länger aufbewahren, als HA es tut (purge_keep_days "
                    f"{_tage_text(ha)} in der configuration.yaml): {liste}. HA löscht diese Daten trotzdem nach {_tage_text(ha, True)} – {wann}. "
                    f"So verschwindet die Warnung: purge_keep_days in der configuration.yaml auf mindestens {groesste} setzen, "
                    "„ha core check“ ausführen und HA neu starten – oder die genannten Regeln auf höchstens "
                    f"{_tage_text(ha)} kürzen."})
    return out
