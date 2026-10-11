"""Herkunft jeder Entität: Gerät, Integration, UI-Helfer, YAML-Datei … – nur lesend, nur Standardbibliothek.

HA speichert keine Quelldatei. Abgeleitet wird aus
  .storage/core.entity_registry   device_id, config_entry_id, platform, unique_id, deleted_entities
  .storage/core.device_registry   Name, Hersteller, Modell, Bereich
  .storage/core.area_registry     Bereichsnamen
  .storage/core.label_registry    Label-Namen (labels(), ab 0.4.10; Labels an Entität und Gerät)
  .storage/core.config_entries    Titel und Domain einer Integration
  .storage/<domain>               UI-Helfer (input_*, timer, counter, schedule …)
  YAML-Dateien der Konfiguration  Definition der unique_id bzw. des Schlüssels (feste Muster, keine bloße Erwähnung)

Test per SSH:  python3 herkunft.py /homeassistant states.json
"""
import json
import os
import re
import time

ARTEN = {
    "geraet": "Gerät", "integration": "Integration (UI)", "ui_helfer": "UI-Helfer", "yaml": "YAML",
    "ohne_uid": "ohne unique_id", "nicht_gefunden": "nicht gefunden", "verwaist": "verwaist",
}
HELFER_DOMAINS = ("input_boolean", "input_number", "input_select", "input_text", "input_datetime", "input_button",
                  "timer", "counter", "schedule", "zone", "person", "tag")
# Plattformen, deren YAML-Entitäten über einen Schlüssel statt unique_id definiert werden
SCHLUESSEL_PLATTFORMEN = HELFER_DOMAINS + ("script", "utility_meter", "group", "shell_command", "rest_command")
AUSGESCHLOSSEN = ("esphome", "custom_components", ".storage", "blueprints", "themes", "deps", "tts", "backups", ".git")

_UID = re.compile(r"""^\s*(?:-\s*)?unique_id:\s*["']?([^"'\s#]+)""")
_ID = re.compile(r"""^\s*(?:-\s*)?id:\s*["']?([^"'\s#]+)""")
_SCHLUESSEL = re.compile(r"^( *)([A-Za-z0-9_]+):\s*(?:#.*)?$")


def _json(pfad):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f).get("data", {})
    except (OSError, ValueError):
        return {}


def yaml_dateien(config):
    out = []
    for wurzel, ordner, dateien in os.walk(config):
        rel = os.path.relpath(wurzel, config)
        teile = [] if rel == "." else rel.split(os.sep)
        if any(t in AUSGESCHLOSSEN or t.startswith(".") for t in teile) or len(teile) > 3:
            ordner[:] = []
            continue
        out += [os.path.join(wurzel, d) for d in dateien if d.endswith((".yaml", ".yml"))]
    return sorted(out)


def yaml_index(config):
    """Liefert (unique_id → [(datei, zeile)], id → […], schlüssel → [(datei, zeile, block)])."""
    uids, ids, schluessel = {}, {}, {}
    for pfad in yaml_dateien(config):
        rel = os.path.relpath(pfad, config).replace(os.sep, "/")
        block = None
        try:
            with open(pfad, encoding="utf-8", errors="replace") as f:
                for nr, zeile in enumerate(f, 1):
                    m = _UID.match(zeile)
                    if m:
                        uids.setdefault(m.group(1), []).append((rel, nr))
                        continue
                    m = _ID.match(zeile)
                    if m:
                        ids.setdefault(m.group(1), []).append((rel, nr))
                        continue
                    m = _SCHLUESSEL.match(zeile.rstrip("\n"))
                    if m:
                        einzug, key = len(m.group(1)), m.group(2)
                        if einzug == 0:
                            block = key
                            schluessel.setdefault(key, []).append((rel, nr, None))
                        elif einzug <= 4:
                            schluessel.setdefault(key, []).append((rel, nr, block))
        except OSError:
            continue
    return uids, ids, schluessel


def _datei_text(treffer, vermutlich=False):
    datei, zeile = treffer[0], treffer[1]
    return ("vermutlich " if vermutlich else "") + f"{datei}:{zeile}"


def _schluessel_treffer(liste, plattform):
    """Wählt die Definition eines Schlüssels: Block oder Dateiname passt zur Plattform."""
    sicher = [t for t in liste if t[2] == plattform or (t[2] is None and plattform in os.path.basename(t[0]))]
    if sicher:
        return sicher[0], False
    if len(liste) == 1:
        return liste[0], True
    return None, False


def ermittle(config, zustaende):
    """Liefert {entity_id: {art, text, info, device_id}} für Registry-Einträge, gelöschte und registrierungslose Zustände."""
    st = os.path.join(config, ".storage")
    reg = _json(os.path.join(st, "core.entity_registry"))
    geraete = {d["id"]: d for d in _json(os.path.join(st, "core.device_registry")).get("devices", [])}
    bereiche = {a["id"]: a.get("name") for a in _json(os.path.join(st, "core.area_registry")).get("areas", [])}
    eintraege = {e["entry_id"]: e for e in _json(os.path.join(st, "core.config_entries")).get("entries", [])}
    helfer = {}
    for dom in HELFER_DOMAINS:
        helfer[dom] = {str(i.get("id")) for i in _json(os.path.join(st, dom)).get("items", [])}
    uids, ids, schluessel = yaml_index(config)

    def integration(ce_id):
        e = eintraege.get(ce_id)
        return (e.get("domain"), e.get("title")) if e else (None, None)

    out = {}
    for e in reg.get("entities", []):
        eid, plat, uid = e["entity_id"], e.get("platform") or "", str(e.get("unique_id") or "")
        dev = geraete.get(e.get("device_id"))
        if dev:
            name = dev.get("name_by_user") or dev.get("name") or "(ohne Name)"
            info = " · ".join(x for x in (dev.get("manufacturer"), dev.get("model"),
                                          bereiche.get(e.get("area_id") or dev.get("area_id"))) if x)
            out[eid] = {"art": "geraet", "text": name, "info": info or plat, "device_id": dev["id"]}
            continue
        deaktiviert = " · deaktiviert" if e.get("disabled_by") else ""
        # YAML-unique_id zuerst: auch YAML-MQTT-Entitäten hängen am Config-Entry der MQTT-Integration
        kandidaten = [uid, uid.removesuffix("_single_tariff")]
        yaml_uid = next((uids[k] for k in kandidaten if k in uids), None)
        if yaml_uid:
            out[eid] = {"art": "yaml", "text": _datei_text(yaml_uid[0], len(yaml_uid) > 1),
                        "info": f"Plattform {plat}{deaktiviert}", "device_id": None}
            continue
        if e.get("config_entry_id"):
            dom, titel = integration(e["config_entry_id"])
            text = f"UI: {dom or plat} „{titel}“" if titel else f"UI: {dom or plat}"
            out[eid] = {"art": "integration", "text": text, "info": f"Plattform {plat}{deaktiviert}", "device_id": None}
            continue
        if plat in helfer and uid in helfer[plat]:
            out[eid] = {"art": "ui_helfer", "text": f"UI-Helfer ({plat})", "info": f".storage/{plat}{deaktiviert}", "device_id": None}
            continue
        treffer, vermutlich = None, False
        if plat in ("automation", "script") and uid in ids:
            treffer, vermutlich = ids[uid][0], len(ids[uid]) > 1
        elif plat in SCHLUESSEL_PLATTFORMEN and uid in schluessel:
            treffer, vermutlich = _schluessel_treffer(schluessel[uid], plat)
        if treffer:
            out[eid] = {"art": "yaml", "text": _datei_text(treffer, vermutlich), "info": f"Plattform {plat}{deaktiviert}", "device_id": None}
        else:
            out[eid] = {"art": "nicht_gefunden", "text": f"nicht gefunden ({plat})",
                        "info": "Registry-Eintrag ohne gefundene Definition – oft ein Rest entfernter Konfiguration" + deaktiviert,
                        "device_id": None}

    for e in reg.get("deleted_entities", []):
        eid = e["entity_id"]
        if eid in out:
            continue
        wann = e.get("orphaned_timestamp")
        dom, titel = integration(e.get("config_entry_id")) if e.get("config_entry_id") else (None, None)
        war = e.get("platform") or "?"
        text = (f"gelöscht {time.strftime('%d.%m.%Y', time.localtime(wann))}, war: {war}" if wann
                else f"gelöscht, war: {war}")
        out[eid] = {"art": "verwaist", "text": text, "info": f"Integration „{titel}“ ({dom})" if titel else "",
                    "device_id": None}

    for z in zustaende:
        eid = z["entity_id"]
        if eid not in out:
            out[eid] = {"art": "ohne_uid", "text": f"ohne unique_id ({eid.split('.', 1)[0]})",
                        "info": "Kein Registry-Eintrag – Herkunft nicht sicher bestimmbar", "device_id": None}
    return out


LABEL_DATEIEN = ("core.label_registry", "core.entity_registry", "core.device_registry")


def labels(config):
    """Punkt 115 (0.4.10): HA-Labels je Entität – eigene und die ihres Geräts.

    Liefert {entity_id: [{"name": …, "an": "entitaet" | "geraet" | "beide"}]}, nach Namen sortiert; nur Entitäten mit Label.
    Unbekannte Label-IDs (Label inzwischen gelöscht) werden übergangen."""
    st = os.path.join(config, ".storage")
    namen = {x["label_id"]: x.get("name") or x["label_id"] for x in _json(os.path.join(st, "core.label_registry")).get("labels", [])}
    am_geraet = {d["id"]: d.get("labels") or [] for d in _json(os.path.join(st, "core.device_registry")).get("devices", [])}
    out = {}
    for e in _json(os.path.join(st, "core.entity_registry")).get("entities", []):
        an = {}
        for lid in am_geraet.get(e.get("device_id"), []):
            if lid in namen:
                an[lid] = "geraet"
        for lid in e.get("labels") or []:
            if lid in namen:
                an[lid] = "beide" if an.get(lid) == "geraet" else "entitaet"
        if an:
            out[e["entity_id"]] = sorted(({"name": namen[lid], "an": wo} for lid, wo in an.items()), key=lambda x: x["name"].lower())
    return out


if __name__ == "__main__":
    import sys
    from collections import Counter
    with open(sys.argv[2], encoding="utf-8") as f:
        zs = json.load(f)
    t = time.time()
    h = ermittle(sys.argv[1], zs)
    print(f"{len(h)} Einträge in {time.time() - t:.2f} s")
    print(Counter(v["art"] for v in h.values()))
    for art in ARTEN:
        beispiele = [(k, v["text"]) for k, v in h.items() if v["art"] == art][:3]
        for k, txt in beispiele:
            print(f"  {art:15} {k:55} {txt}")
