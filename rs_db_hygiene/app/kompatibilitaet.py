"""Punkt 302: Freigabe des Recorder-Schemas – Löschen nur, wenn App (und Integration) den Aufbau der Datenbank kennen.

Die Freigaben stehen in `kompatibilitaet.json` neben diesem Modul (gleiche Datei in custom_components/rs_db_hygiene/):
  app / integration   Bereich freigegebener Schemas {"von": n, "bis": m}
  ha_version          Schema → HA-Version, auf der es geprüft ist (nur für die Meldungen)
  mindest_ha          kleinste unterstützte HA-Version (wie hacs.json)
Eine Datei `/homeassistant/rs_db_hygiene_kompatibilitaet.json` (eigene Freigabe ohne Release) hat Vorrang; die App zeigt dann
dauerhaft einen Hinweis. Ist sie ungültig, bleibt das Löschen gesperrt.

Alles hier liest nur: die Schema-Version kommt nur lesend aus `schema_changes` der HA-Datenbank. Analysen sind nicht betroffen.
"""
import json
import os
import sqlite3

DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kompatibilitaet.json")
EIGENE = "rs_db_hygiene_kompatibilitaet.json"
KOPF_SCHEMA = "X-DBH-Integration-Schema"     # Bereich der Integration, z. B. „53-53“


def _bereich(b, name):
    if not isinstance(b, dict) or not all(isinstance(b.get(k), int) and not isinstance(b.get(k), bool) for k in ("von", "bis")):
        raise ValueError(f"„{name}“ braucht ganze Zahlen „von“ und „bis“")
    if b["von"] > b["bis"]:
        raise ValueError(f"„{name}“: „von“ ist größer als „bis“")
    return {"von": b["von"], "bis": b["bis"]}


def pruefe_daten(d):
    """Gültige Freigabe als Dict (app, integration, ha_version, mindest_ha) oder ValueError mit Text."""
    if not isinstance(d, dict):
        raise ValueError("Die Datei enthält kein JSON-Objekt")
    hv = d.get("ha_version") or {}
    if not isinstance(hv, dict) or not all(str(k).isdigit() and isinstance(v, str) for k, v in hv.items()):
        raise ValueError("„ha_version“ muss Schema-Nummern auf HA-Versionen abbilden")
    mh = d.get("mindest_ha")
    if mh is not None and not isinstance(mh, str):
        raise ValueError("„mindest_ha“ muss eine Versionsangabe sein")
    return {"app": _bereich(d.get("app"), "app"), "integration": _bereich(d.get("integration"), "integration"),
            "ha_version": {int(k): v for k, v in hv.items()}, "mindest_ha": mh}


def lade(ha_config):
    """{"daten", "quelle" ("paket" | "eigene"), "fehler"} – bei Fehler daten None (Löschen gesperrt)."""
    eigene = os.path.join(ha_config, EIGENE) if ha_config else None
    pfad, quelle = (eigene, "eigene") if eigene and os.path.exists(eigene) else (DATEI, "paket")
    try:
        with open(pfad, encoding="utf-8") as f:
            return {"daten": pruefe_daten(json.load(f)), "quelle": quelle, "fehler": None}
    except (OSError, ValueError) as e:     # json.JSONDecodeError ist ein ValueError
        return {"daten": None, "quelle": quelle, "fehler": f"{os.path.basename(pfad)}: {e}"}


def schema_der_db(db_pfad):
    """Aktuelle Schema-Version der HA-Datenbank (letzter Eintrag in schema_changes) oder None."""
    if not db_pfad or not os.path.exists(db_pfad):
        return None
    con = sqlite3.connect(f"file:{db_pfad}?mode=ro", uri=True, timeout=10)
    try:
        z = con.execute("SELECT schema_version FROM schema_changes ORDER BY change_id DESC LIMIT 1").fetchone()
        return int(z[0]) if z and z[0] is not None else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def bereich_aus_kopf(wert):
    """„53-54“ → {"von": 53, "bis": 54}; sonst None (ältere Integration schickt den Kopf nicht)."""
    try:
        von, bis = (int(x) for x in str(wert).split("-", 1))
        return {"von": von, "bis": bis} if von <= bis else None
    except (TypeError, ValueError):
        return None


def _drin(schema, b):
    return b is not None and b["von"] <= schema <= b["bis"]


def bewerte(schema, kompat, integration=None, ha_version=None):
    """None, wenn Löschen erlaubt ist; sonst {"fall", "text", "details"}. Fälle laut Punkt 302:
    1 Integration veraltet · 2 RS DB Hygiene Manager zu alt für diese HA-Version · 3 HA zu alt · 4 App veraltet ·
    0 Freigabe-Datei ungültig oder Schema nicht lesbar. integration: Bereich der Integration aus ihrem Kopf (None = unbekannt)."""
    if kompat.get("daten") is None:
        return {"fall": 0, "text": "Die Freigabe-Datei ist ungültig – das Löschen ist pausiert, bis sie korrigiert ist.",
                "details": kompat.get("fehler") or ""}
    d = kompat["daten"]
    app = d["app"]
    if schema is None:
        return {"fall": 0, "text": "Der Aufbau der Datenbank lässt sich nicht lesen – das Löschen ist pausiert.",
                "details": "Tabelle schema_changes nicht lesbar oder leer."}
    details = (f"Datenbank-Schema {schema}; freigegeben App {app['von']}–{app['bis']}"
               + (f", Integration {integration['von']}–{integration['bis']}" if integration else ", Integration unbekannt"))
    ha = f"Home Assistant {ha_version}" if ha_version else "Home Assistant"
    if _drin(schema, app):
        if integration is not None and not _drin(schema, integration):
            return {"fall": 1, "details": details,
                    "text": "Die Integration RS DB Hygiene Manager ist veraltet. Bitte in HACS aktualisieren und HA neu starten – "
                            "bis dahin ist das Löschen pausiert."}
        return None
    if _drin(schema, integration):
        return {"fall": 4, "details": details,
                "text": "Die App RS DB Hygiene Manager ist veraltet. Bitte im App-Store aktualisieren – bis dahin ist das Löschen pausiert."}
    if schema > app["bis"]:
        return {"fall": 2, "details": details,
                "text": f"{ha} hat den Aufbau der Datenbank geändert. Diese Version des RS DB Hygiene Managers ist dafür noch nicht "
                        "freigegeben. Das Löschen ist pausiert, bis ein Update erscheint – deine Daten bleiben unverändert."}
    mindest = d.get("mindest_ha") or d["ha_version"].get(app["von"])
    return {"fall": 3, "details": details,
            "text": "Diese Version des RS DB Hygiene Managers braucht "
                    + (f"mindestens Home Assistant {mindest}" if mindest else "ein neueres Home Assistant")
                    + ". Bitte HA aktualisieren – bis dahin ist das Löschen pausiert."}
