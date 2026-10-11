"""Abbildung des App-Status auf die Entitäten (ohne Abhängigkeit von Home Assistant, damit testbar)."""
from datetime import UTC, datetime

SCHALTER_ORT = "Einstellungen → Apps → RS DB Hygiene Manager → Konfiguration"
ZUSTAENDE = ["bereit", "nichts_zu_tun", "gesperrt", "analyse", "attribut_analyse", "loeschlauf", "fehler", "nicht_erreichbar"]


def _ts(sek):
    return datetime.fromtimestamp(sek, UTC) if sek else None


def werte(d):
    """Werte und Attribute je Entitäts-Schlüssel aus /api/auto/status der App."""
    plan = d.get("plan") or {}
    ll = d.get("letzter_lauf") or {}
    return {
        # ab 0.4.1 (Punkt 66) verständliche Namen der Schalter; „sperren“ bleibt (wird in Automationen benutzt)
        "status": (d.get("zustand"), {"sperren": d.get("sperren") or [], "fehler": d.get("fehler"), "job": d.get("job"),
                                      "hauptschalter_loeschen_freigegeben": d.get("loeschen_freigegeben"),
                                      "automatik_laeufe_aus_ha_freigegeben": d.get("automatik_freigegeben"),
                                      "schalter_ort": SCHALTER_ORT,
                                      "statistik_und_events_loeschbar": d.get("stufe_b"),
                                      # ab 0.4.4 (Punkt 108): gesetzt, solange die App kurz nicht antwortet und der letzte Stand gilt
                                      "veraltet_seit": _ts(d.get("veraltet_seit")), "veraltet_grund": d.get("veraltet_grund")}),
        "laeuft": (bool(d.get("laeuft")), {"job": d.get("job")}),
        "fortschritt": (d.get("fortschritt"), {}),
        "geplante_zeilen": (plan.get("zeilen"), {k: plan.get(k) for k in ("states", "kurz", "lts", "events", "attributzeilen", "entitaeten", "auftraege")}),
        "letzter_lauf": (_ts(ll.get("ende") or ll.get("start")), {k: ll.get(k) for k in ("status", "geloescht", "attributzeilen", "dauer_s", "umfang", "ausgeloest", "fehler")}),
        "letzter_lauf_geloescht": (ll.get("geloescht"), {"attributzeilen": ll.get("attributzeilen")}),
        "db_groesse": (round(d["db_bytes"] / 1e9, 2) if d.get("db_bytes") else None, {}),
        # ab 0.4.3 (Punkt 81): Zeitraum der Prognose als Attribut; er folgt purge_keep_days (seit Punkt 79 bis 20 Jahre)
        "prognose": (round(d["prognose_bytes"] / 1e9, 1) if d.get("prognose_bytes") else None,
                     {"tage": d.get("prognose_tage"), "jahre": round(d["prognose_tage"] / 365, 1) if d.get("prognose_tage") else None}),
        "analyse_stand": (_ts(d.get("analyse_zeit")), {}),
        "letzte_attribut_analyse": (_ts(d.get("attribut_analyse_zeit")), {}),
    }


def lauf_beendet(alt, neu):
    """Daten für das Event rs_db_hygiene_lauf_beendet, wenn seit der letzten Abfrage ein Lauf zu Ende ging, sonst None."""
    a, n = (alt or {}).get("letzter_lauf") or {}, (neu or {}).get("letzter_lauf") or {}
    if not n.get("ende") or (a.get("start") == n.get("start") and a.get("ende")):
        return None
    if alt is None:          # erste Abfrage nach dem Start: alten Lauf nicht neu melden
        return None
    return {"status": n.get("status"), "geloescht": n.get("geloescht"), "attributzeilen": n.get("attributzeilen"),
            "dauer_s": n.get("dauer_s"), "umfang": n.get("umfang"), "ausgeloest": n.get("ausgeloest"), "fehler": n.get("fehler"),
            "start": n.get("start"), "ende": n.get("ende")}
