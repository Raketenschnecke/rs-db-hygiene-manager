"""Punkt 302 (0.6.2b1): freigegebene Recorder-Schemas der Integration aus kompatibilitaet.json.

Gleiche Datei wie rs_db_hygiene/app/kompatibilitaet.json. Eine Datei `rs_db_hygiene_kompatibilitaet.json` im HA-Konfigordner
(eigene Freigabe ohne Release) hat Vorrang. Ist die gültige Datei nicht lesbar, ist kein Schema freigegeben (Löschen gesperrt).
"""
from __future__ import annotations

import json
import os

DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kompatibilitaet.json")
EIGENE = "rs_db_hygiene_kompatibilitaet.json"


def lade(eigene_pfad: str | None) -> dict:
    """{"von", "bis", "quelle", "fehler", "ha_version"} – läuft im Executor (Dateizugriff). Bei Fehler von/bis None."""
    pfad, quelle = (eigene_pfad, "eigene") if eigene_pfad and os.path.exists(eigene_pfad) else (DATEI, "paket")
    try:
        with open(pfad, encoding="utf-8") as f:
            d = json.load(f)
        b = d["integration"]
        von, bis = b["von"], b["bis"]
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (von, bis)) or von > bis:
            raise ValueError("„integration“ braucht ganze Zahlen von ≤ bis")
        hv = d.get("ha_version") or {}
        return {"von": von, "bis": bis, "quelle": quelle, "fehler": None,
                "ha_version": {int(k): str(v) for k, v in hv.items()} if isinstance(hv, dict) else {}}
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {"von": None, "bis": None, "quelle": quelle, "fehler": f"{os.path.basename(pfad)}: {e}", "ha_version": {}}


def freigegeben(k: dict, schema: int | None) -> bool:
    return k.get("von") is not None and schema is not None and k["von"] <= schema <= k["bis"]


def kopf(k: dict) -> str | None:
    """Wert für den Kopf X-DBH-Integration-Schema („53-53“); ohne gültige Freigabe None."""
    return f"{k['von']}-{k['bis']}" if k.get("von") is not None else None
