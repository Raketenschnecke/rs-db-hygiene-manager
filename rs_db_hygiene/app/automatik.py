"""Schnittstelle für die Integration RS DB Hygiene (Punkt 62) – Steuerung und Status aus HA heraus.

- Zugang: nur mit Schlüssel (Kopf X-DBH-Schluessel); der Schlüssel liegt zufällig erzeugt unter /data/schluessel.txt und
  wird der Integration per Supervisor-Discovery übergeben (Dienst rs_db_hygiene, mit Host und Port).
- Läufe aus HA heraus (Knopf, Dienst, Automation) brauchen zusätzlich die App-Option automatik_freigegeben;
  Hauptschalter, Obergrenze, Sperren und Analyse-Alter gelten unverändert.
"""
import hmac
import json
import logging
import os
import secrets
import time
import urllib.request

_LOG = logging.getLogger("automatik")
KOPF = "X-DBH-Schluessel"
PORT = 8099
# ab 0.4.1 (Punkt 66): Ort des Schalters nennen; ein Neustart der App ist nicht nötig (Optionen werden je Abfrage gelesen)
AUTOMATIK_AUS = ("Automatik aus: „Automatik freigegeben“ einschalten (Einstellungen → Apps → RS DB Hygiene Manager → Konfiguration); "
                 "wirkt nach dem Speichern sofort, ohne Neustart der App.")


def schluessel(daten_ordner):
    pfad = os.path.join(daten_ordner, "schluessel.txt")
    try:
        with open(pfad, encoding="utf-8") as f:
            s = f.read().strip()
            if len(s) >= 32:
                return s
    except OSError:
        pass
    s = secrets.token_urlsafe(32)
    with open(pfad, "w", encoding="utf-8") as f:
        f.write(s)
    os.chmod(pfad, 0o600)
    return s


def zugang_ok(request, key):
    return hmac.compare_digest(request.headers.get(KOPF, ""), key)


def _supervisor(pfad, daten=None, token=None):
    req = urllib.request.Request(f"http://supervisor{pfad}", method="POST" if daten is not None else "GET",
                                 data=json.dumps(daten).encode() if daten is not None else None,
                                 headers={"Authorization": f"Bearer {token or os.environ.get('SUPERVISOR_TOKEN')}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def melde_discovery(key, versuche=10, pause=15):
    """App bei HA anmelden: Supervisor-Discovery mit Host, Port und Schlüssel (die Integration übernimmt die Daten)."""
    for i in range(versuche):
        try:
            host = _supervisor("/addons/self/info")["data"]["hostname"]
            antwort = _supervisor("/discovery", {"service": "rs_db_hygiene", "config": {"host": host, "port": PORT, "schluessel": key}})
            _LOG.info("Discovery an HA gemeldet (Host %s, Port %s): %s", host, PORT, (antwort.get("data") or {}).get("uuid"))
            return True
        except Exception as e:  # noqa: BLE001 – HA evtl. noch nicht bereit, erneut versuchen
            _LOG.warning("Discovery nicht gemeldet (Versuch %d): %s", i + 1, e)
            time.sleep(pause)
    return False


def status(p, job, letzter_fehler, analyse_info, db_bytes, prognose_bytes, optionen, laeufe, attr_info=None, prognose_tage=None):
    """Kompakter Zustand für die Entitäten der Integration."""
    automatik = bool(optionen.get("automatik_freigegeben"))
    # ab 0.4.3 (Punkt 83): ohne Sperre wegen alter Analyse – ein Lauf aus HA analysiert in dem Fall vorher selbst
    sperren = list(p.get("sperren_auto", p.get("sperren")) or []) if p else ["Noch keine Analyse vorhanden."]
    if not automatik:
        sperren.insert(0, AUTOMATIK_AUS)
    nichts = bool(p) and not p["gruppen"] and not (p.get("summe") or {}).get("auftraege_b")
    if job:
        art = job.get("art")
        if art == "beide":      # Punkt 90: Knopf der Web-GUI – Zustand nach dem laufenden Schritt
            art = job.get("schritt") or "analyse"
        zustand = {"loeschen": "loeschlauf", "analyse": "analyse", "attribute": "attribut_analyse"}.get(art, "analyse")
    elif letzter_fehler:
        zustand = "fehler"
    elif nichts and all("nichts zu löschen" in s for s in sperren):
        zustand = "nichts_zu_tun"
    elif sperren:
        zustand = "gesperrt"
    else:
        zustand = "bereit"
    f = (job or {}).get("fortschritt") or {}
    ziel = max(f.get("geplant") or 0, f.get("gezaehlt") or 0)
    su = (p or {}).get("summe") or {}
    zb = su.get("zeilen_b_lauf") or su.get("zeilen_b") or {}     # Punkt 75: nur, was dieser Lauf löscht
    ll = laeufe[0] if laeufe else None
    return {
        "zustand": zustand, "sperren": sperren, "fehler": letzter_fehler, "laeuft": bool(job), "job": (job or {}).get("art"),
        "fortschritt": round(100 * f.get("geloescht", 0) / ziel, 1) if job and ziel else (0 if job else None),
        "plan": {"zeilen": su.get("zeilen", 0), "states": su.get("zeilen_states", 0), "kurz": zb.get("kurz", 0), "lts": zb.get("lts", 0),
                 "events": zb.get("events", 0), "attributzeilen": (su.get("attr_zeilen", 0) or 0) + (su.get("attr_getrennt", 0) or 0), "entitaeten": su.get("entitaeten", 0),
                 "auftraege": su.get("aufrufe", 0) + su.get("auftraege_b", 0)} if p else None,
        "letzter_lauf": {"start": ll.get("start"), "ende": ll.get("ende"), "status": ll.get("status"), "geloescht": ll.get("geloescht"),
                         "attributzeilen": ll.get("attr_geloescht"), "dauer_s": round(ll["ende"] - ll["start"]) if ll.get("ende") else None,
                         "umfang": ll.get("nur_regel") or "alle aktiven Regeln", "ausgeloest": ll.get("ausgeloest"),
                         "fehler": ll.get("fehler")} if ll else None,
        "analyse_zeit": float(analyse_info["analyse_zeit"]) if analyse_info.get("analyse_zeit") else None,
        "attribut_analyse_zeit": float(attr_info["analyse_zeit"]) if (attr_info or {}).get("analyse_zeit") else None,
        "db_bytes": db_bytes, "prognose_bytes": prognose_bytes,
        "prognose_tage": prognose_tage,       # Punkt 81: Zeitraum der Prognose (= purge_keep_days der Analyse)
        "analyse_veraltet": (p or {}).get("analyse_veraltet") or None,
        "automatik_freigegeben": automatik, "loeschen_freigegeben": bool(optionen.get("loeschen_freigegeben")),
        "stufe_b": (p or {}).get("stufe_b"),
    }
