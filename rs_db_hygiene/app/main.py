"""RS DB Hygiene Manager – Web-Oberfläche (Ingress) und Nachtlauf.

Ab 0.2.0 Löschlauf Stufe A: Kurzzeit-States per recorder.purge_entities, nur manuell und nur mit Hauptschalter
(loeschen.py). Die App selbst schreibt nie in die HA-Datenbank; sie ist nur lesend eingebunden.
"""
import asyncio
import csv
import io
import json
import logging
import os
import time

from aiohttp import web

import analyse
import automatik
import herkunft
import loeschen
import probelauf
import regeln as rg
import spaltenfilter
import warnungen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_LOG = logging.getLogger("main")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DBH_DATA", "/data")
OPTIONEN_PFAD = os.path.join(DATA, "options.json")
CACHE = os.path.join(DATA, "cache.db")
ATTR = os.path.join(DATA, "attribute.db")
REGELN = os.path.join(DATA, "regeln.json")
LAEUFE = os.path.join(DATA, "laeufe")
LAEUFE_SUMME = os.path.join(DATA, "laeufe_summe.json")
VORLAGE = os.path.join(APP_DIR, "regeln_vorlage.json")
SICHERUNGEN = os.path.join(DATA, "regeln_sicherungen")   # Punkt 123
HA_CONFIG = "/homeassistant"
INGRESS_IP = "172.30.32.2"


def optionen():
    try:
        with open(OPTIONEN_PFAD, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {}


class Zustand:
    def __init__(self):
        self.job = None            # {"art", "start"}
        self.letzter_fehler = None
        self.letzter_job = None
        self._pl_cache = {}
        self.lauf = None           # laufender Löschlauf (loeschen.Lauf)

    def mtime(self, pfad):
        return os.path.getmtime(pfad) if os.path.exists(pfad) else 0

    def probelauf(self, ziel, vorgemerkt):
        if not os.path.exists(CACHE):
            return None
        schluessel = (ziel, vorgemerkt, self.mtime(CACHE), self.mtime(ATTR), self.mtime(REGELN))
        if schluessel not in self._pl_cache:
            self._pl_cache = {schluessel: probelauf.rechne(CACHE, ATTR, rg.lade(REGELN, VORLAGE), ziel, vorgemerkt)}
        return self._pl_cache[schluessel]


Z = Zustand()


# ---------------------------------------------------------------- Jobs

def _analyse_sync():
    opt = optionen()
    db = opt.get("db_pfad", f"{HA_CONFIG}/home-assistant_v2.db")
    pkd = _pkd_wirksam()
    reg, gel = analyse.lies_registry(f"{HA_CONFIG}/.storage/core.entity_registry")
    zustaende = analyse.hole_zustaende()
    try:
        hku = herkunft.ermittle(HA_CONFIG, zustaende)
    except Exception:  # noqa: BLE001 – Herkunft ist Zusatzinformation, die Analyse soll trotzdem laufen
        _LOG.exception("Herkunft konnte nicht ermittelt werden")
        hku = {}
    return analyse.analyse(db, CACHE, zustaende, reg, gel, pkd, hku)


def _cache_version():
    if not os.path.exists(CACHE):
        return 0
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{CACHE}?mode=ro", uri=True)
        wert = dict(con.execute("SELECT schluessel, wert FROM info")).get("cache_version", "1")
        con.close()
        return int(wert)
    except (sqlite3.Error, ValueError):
        return 0


def _attribut_sync():
    db = optionen().get("db_pfad", f"{HA_CONFIG}/home-assistant_v2.db")
    return analyse.attribut_analyse(db, ATTR)


def _beide_sync():
    """Punkt 90 (0.4.5): Knopf „Analyse starten“ der Web-GUI – erst die Analyse, direkt danach die Attribut-Analyse (ein Job)."""
    _LOG.info("Analyse und Attribut-Analyse: Schritt 1/2 Analyse")
    erg = {"analyse": _analyse_sync()}
    if Z.job is not None:
        Z.job.update(schritt="attribute", schritt_start=time.time())
    _LOG.info("Analyse und Attribut-Analyse: Schritt 2/2 Attribut-Analyse")
    erg["attribute"] = _attribut_sync()
    return erg


HERZSCHLAG_S = 30


async def _herzschlag(art):
    """Punkt 109 (0.4.9): alle 30 s eine Zeile im App-Log, solange eine Analyse läuft – zeigt, dass die App lebt."""
    start = time.time()
    while True:
        await asyncio.sleep(HERZSCHLAG_S)
        f = analyse.FORTSCHRITT
        teil = ""
        if art == "beide":
            teil = " – Schritt 2/2 Attribut-Analyse" if (Z.job or {}).get("schritt") == "attribute" else " – Schritt 1/2 Analyse"
        if f.get("art") == "attribute":
            n, erw = f.get("zeilen") or 0, f.get("erwartet")
            mehr = f"{f.get('schritt', '')}: {loeschen._z(n)} Zuordnungen" + (f" von ca. {loeschen._z(erw)} ({min(99, 100 * n // erw)} %)" if erw else "")
        elif f.get("schritt"):
            mehr = f"Schritt {f.get('nr')}/{f.get('von')} {f.get('schritt')}"
        else:
            mehr = ""
        _LOG.info("%s läuft seit %s%s%s", JOBNAMEN.get(art, art), loeschen._dauer(time.time() - start), teil, f" · {mehr}" if mehr else "")


async def starte_job(art, fn=None, zusatz=None, danach=None):
    if Z.job:
        return False
    Z.job = {"art": art, "start": time.time(), **(zusatz or {})}

    async def lauf():
        herz = asyncio.create_task(_herzschlag(art)) if art in ("analyse", "attribute", "beide") else None   # Punkt 109
        try:
            ergebnis = await asyncio.to_thread(fn or {"analyse": _analyse_sync, "attribute": _attribut_sync, "beide": _beide_sync}[art])
            Z.letzter_job = {"art": art, "ende": time.time(), "ok": True, "ergebnis": ergebnis}
            Z.letzter_fehler = None
        except Exception as e:  # noqa: BLE001 – Fehler in der GUI anzeigen
            _LOG.exception("Job %s fehlgeschlagen", art)
            Z.letzter_fehler = f"{JOBNAMEN.get(art, art)}: {e}"
            Z.letzter_job = {"art": art, "ende": time.time(), "ok": False}
        finally:
            if herz:
                herz.cancel()
            Z.job = None
            if danach:
                await danach()

    asyncio.create_task(lauf())
    return True


JOBNAMEN = {"analyse": "Analyse", "attribute": "Attribut-Analyse", "beide": "Analyse und Attribut-Analyse", "loeschen": "Löschlauf"}


async def nachtlauf():
    """Täglich zur eingestellten Uhrzeit die (leichte) Analyse; beim ersten Start oder altem Cache-Aufbau sofort."""
    if _cache_version() < analyse.CACHE_VERSION:
        _LOG.info("Cache fehlt oder hat alten Aufbau – starte Analyse")
        await starte_job("analyse")
    zuletzt = None
    while True:
        await asyncio.sleep(30)
        uhr = optionen().get("analyse_uhrzeit", "02:15")
        jetzt = time.localtime()
        if time.strftime("%H:%M", jetzt) == uhr and zuletzt != jetzt.tm_yday:
            zuletzt = jetzt.tm_yday
            await starte_job("analyse")


# ---------------------------------------------------------------- HTTP

# Nur für Tests außerhalb des Add-ons: zusätzlich erlaubte Client-IPs (kommagetrennt)
ERLAUBT = {INGRESS_IP, "127.0.0.1"} | {ip.strip() for ip in os.environ.get("DBH_ERLAUBT", "").split(",") if ip.strip()}


SCHLUESSEL = {"wert": None}     # Zugang der Integration (Punkt 62), beim Start aus /data gelesen bzw. erzeugt


@web.middleware
async def nur_ingress(request, handler):
    if request.path.startswith("/api/auto/"):
        # Integration RS DB Hygiene aus HA Core: nur mit Schlüssel, unabhängig von der Adresse
        if not SCHLUESSEL["wert"] or not automatik.zugang_ok(request, SCHLUESSEL["wert"]):
            return web.Response(status=403, text="Schlüssel fehlt oder falsch")
    elif request.remote not in ERLAUBT:
        return web.Response(status=403, text="Nur über Home-Assistant-Ingress erreichbar")
    try:
        return await handler(request)
    except rg.RegelFehler as e:
        return web.json_response({"fehler": str(e)}, status=400)


def _parameter(request):
    q = request.query
    ziel = q.get("ziel")
    # passive (früher „vorgemerkte“) Regeln mitrechnen; der alte Parametername wird weiter angenommen
    return (int(ziel) if ziel and ziel.isdigit() else None), "1" in (q.get("passiv"), q.get("vorgemerkt"))


async def index(_request):
    return web.FileResponse(os.path.join(APP_DIR, "static", "index.html"))


async def status(_request):
    info, attr = {}, None
    if os.path.exists(CACHE):
        import sqlite3
        con = sqlite3.connect(f"file:{CACHE}?mode=ro", uri=True)
        info = dict(con.execute("SELECT schluessel, wert FROM info"))
        con.close()
    if os.path.exists(ATTR):
        import sqlite3
        con = sqlite3.connect(f"file:{ATTR}?mode=ro", uri=True)
        attr = dict(con.execute("SELECT schluessel, wert FROM info"))
        con.close()
    return web.json_response({"job": Z.job, "letzter_job": Z.letzter_job, "fehler": Z.letzter_fehler,
                              "analyse": info, "attribute": attr, "optionen": optionen(),
                              "regeln_geaendert": Z.mtime(REGELN),
                              "pruefung": await asyncio.to_thread(pkd_pruefung)})


_PKD = {"schl": None, "erg": None}


def pkd_pruefung():
    """Punkt 85: purge_keep_days der configuration.yaml gegen App-Option und aktive Regeln; neu nur bei geänderten Dateien."""
    cfg = f"{HA_CONFIG}/configuration.yaml"
    schl = (Z.mtime(cfg), Z.mtime(REGELN), Z.mtime(OPTIONEN_PFAD))
    if _PKD["schl"] != schl:
        rec = analyse.lies_recorder(cfg)
        try:
            regel_daten = rg.lade(REGELN, VORLAGE)
        except Exception:  # noqa: BLE001 – kaputte Regeldatei meldet der Tab Regeln; die Prüfung nutzt dann nur die Option
            regel_daten = {"standard": {}, "regeln": []}
        _PKD.update(schl=schl, erg={"ha_purge_keep_days": rec["purge_keep_days"], "auto_purge": rec["auto_purge"],
                                    "option_purge_keep_days": int(optionen().get("purge_keep_days") or 0),
                                    "warnungen": warnungen.pkd_pruefung(rec, optionen().get("purge_keep_days"), regel_daten)})
    return _PKD["erg"]


async def job_starten(request):
    art = request.match_info["art"]
    if art not in ("analyse", "attribute", "beide"):
        raise web.HTTPNotFound()
    laufend = Z.job
    ok = await starte_job(art, zusatz={"schritt": "analyse"} if art == "beide" else None)
    if not ok:
        return web.json_response({"gestartet": False, "job": laufend,
                                  "fehler": f"Es läuft bereits: {laufend['art'] if laufend else 'ein Job'}"}, status=409)
    return web.json_response({"gestartet": True, "job": Z.job}, status=202)


async def probelauf_summe(request):
    ziel, vm = _parameter(request)
    r = await asyncio.to_thread(Z.probelauf, ziel, vm)
    if not r:
        return web.json_response({"fehler": "Noch keine Analyse vorhanden"}, status=404)
    return web.json_response({"stand": r["stand"], "summe": r["summe"], "regeln": r["regeln"], "events": r.get("events", [])})


SORTIERBAR = {"entity_id", "status", "rows", "rows_24h", "first_ts", "last_ts", "lts_rows", "kurz_rows",
              "loeschbar", "prognose_bytes", "herkunft", "labels",
              "loesch_summe", "loesch_states", "loesch_kurz", "loesch_lts", "loesch_attr_zeilen",
              "attr_bytes", "attr_zuwachs_tag", "attr_avg", "attr_loesch", "attr_anteil"}
ATTR_SUMMEN = ("attr_bytes", "attr_zuwachs_tag", "attr_loesch")


def summen(liste):
    """Summe bzw. Anzahl je Spalte (Punkt 12)."""
    s = {"entitaeten": len(liste), "geraete": len({e["device_id"] for e in liste if e.get("device_id")}),
         "aktiv": 0, "verwaist": 0, "rows": 0, "rows_24h": 0, "lts_rows": 0, "loeschbar": 0,
         "loesch_summe": 0, "loesch_states": 0, "loesch_kurz": 0, "loesch_lts": 0, "loesch_attr_zeilen": None,
         "first_ts": None, "last_ts": None,
         # Punkt 115: Entitäten mit Label und Zahl verschiedener Labels
         "labels": sum(1 for e in liste if e.get("labels")),
         "label_namen": len({x["name"] for e in liste for x in e.get("labels_info") or []})}
    for art in rg.WERTE:
        s[art] = {"regel": 0, "std": 0}
    for k in ATTR_SUMMEN:
        s[k] = None          # None = keine Attribut-Analyse vorhanden
    s["attr_zeilen"], gewicht = None, 0
    for e in liste:
        for k in ATTR_SUMMEN:
            if e.get(k) is not None:
                s[k] = (s[k] or 0) + e[k]
        if e.get("attr_zeilen") is not None:
            s["attr_zeilen"] = (s["attr_zeilen"] or 0) + e["attr_zeilen"]
            gewicht += (e["rows"] or 0) * probelauf.B_STATE
        s["aktiv" if e["status"] == "aktiv" else "verwaist"] += 1
        for k in ("rows", "rows_24h", "lts_rows"):
            s[k] += e[k] or 0
        s["loeschbar"] += e["loeschbar"]["states"] or 0
        for k in ("loesch_summe", "loesch_states", "loesch_kurz", "loesch_lts"):
            s[k] += e.get(k) or 0
        if e.get("loesch_attr_zeilen") is not None:
            s["loesch_attr_zeilen"] = (s["loesch_attr_zeilen"] or 0) + e["loesch_attr_zeilen"]
        if e["first_ts"] and (s["first_ts"] is None or e["first_ts"] < s["first_ts"]):
            s["first_ts"] = e["first_ts"]
        if e["last_ts"] and (s["last_ts"] is None or e["last_ts"] > s["last_ts"]):
            s["last_ts"] = e["last_ts"]
        for art in rg.WERTE:
            w = (e["werte"] or {}).get(art)
            if w:
                s[art]["regel" if w["regel"] else "std"] += 1
    # Ø Attributgröße und Anteil über die Auswahl (gewichtet, nicht Mittel der Prozente)
    nutz = (s["attr_bytes"] or 0) / probelauf.B_ATTR_FAKTOR
    s["attr_avg"] = round(nutz / s["attr_zeilen"]) if s["attr_zeilen"] else None
    s["attr_anteil"] = round(100 * s["attr_bytes"] / (s["attr_bytes"] + gewicht), 1) if s["attr_bytes"] else None
    return s


_LABELS = {"schl": None, "daten": {}}


def _mit_labels(r):
    """Punkt 115 (0.4.10): HA-Labels (Entität und Gerät) live aus der Registry an die Entitäten hängen – ohne neue Analyse.
    Neu gelesen wird nur, wenn sich eine der drei Registry-Dateien geändert hat; danach je Probelauf-Stand einmal."""
    schl = tuple(Z.mtime(os.path.join(HA_CONFIG, ".storage", d)) for d in herkunft.LABEL_DATEIEN)
    if _LABELS["schl"] != schl:
        try:
            _LABELS["daten"] = herkunft.labels(HA_CONFIG)
        except Exception:  # noqa: BLE001 – Labels sind Zusatzinformation
            _LOG.exception("Labels nicht lesbar")
            _LABELS["daten"] = {}
        _LABELS["schl"] = schl
    if r.get("_labels_schl") != schl:
        for e in r["entitaeten"]:
            li = _LABELS["daten"].get(e["entity_id"]) or []
            e["labels_info"] = li
            e["labels"] = ", ".join(x["name"] for x in li)
        r["_labels_schl"] = schl
    return r


def _gefiltert(r, q, fehler_ziel=None):
    _mit_labels(r)
    such = (q.get("q") or "").lower()
    domain, status_f, regel_f = q.get("domain"), q.get("status"), q.get("regel")
    hinweis = q.get("hinweis") == "1"
    spalten_ok, fehler, _aktiv = spaltenfilter.baue(q)
    if fehler_ziel is not None:
        fehler_ziel.update(fehler)
    out = []
    for e in r["entitaeten"]:
        if not spalten_ok(e):
            continue
        if such and such not in e["entity_id"]:
            continue
        if domain and e["domain"] != domain:
            continue
        if status_f == "aktiv" and e["status"] != "aktiv":
            continue
        if status_f == "verwaist" and e["status"] == "aktiv":
            continue
        regeln_e = {v["regel"] for v in (e["werte"] or {}).values() if v["regel"]}
        if regel_f == "mit" and not regeln_e:
            continue
        if regel_f == "ohne" and (regeln_e or e["status"] != "aktiv"):
            continue
        if regel_f and regel_f not in ("mit", "ohne") and regel_f not in regeln_e:
            continue
        if hinweis and not (e["hinweise"] or e["geschuetzt"]):
            continue
        out.append(e)
    # ab 0.4.1 (Punkt 74) Mehrfachsortierung: sort=status,-rows (Minus = absteigend), stabil von der letzten Stufe her
    for k, ab in reversed(sortierung(q)):
        out.sort(key=_sortwert(k), reverse=ab)
    return out


def sortierung(q):
    """Sortierstufen [(spalte, absteigend)] aus sort=a,-b; alte Form sort=a&desc=1 bleibt gültig. Standard: -rows."""
    roh = [s.strip() for s in (q.get("sort") or "").split(",") if s.strip()]
    if len(roh) == 1 and not roh[0].startswith("-") and "desc" in q:
        roh = [("-" if q.get("desc", "1") == "1" else "") + roh[0]]
    stufen = [(s.lstrip("-"), s.startswith("-")) for s in roh if s.lstrip("-") in SORTIERBAR]
    return stufen or [("rows", True)]


def _sortwert(k):
    if k == "loeschbar":
        return lambda e: e["loeschbar"]["states"]
    if k == "labels":      # Punkt 115: ohne Label hinten (aufsteigend)
        return lambda e: (not e.get("labels"), (e.get("labels") or "").lower())
    if k in ("entity_id", "status", "herkunft"):
        return lambda e: (e.get(k) or "").lower()
    return lambda e: e[k] if e[k] is not None else -1


async def entitaeten(request):
    ziel, vm = _parameter(request)
    r = await asyncio.to_thread(Z.probelauf, ziel, vm)
    if not r:
        return web.json_response({"fehler": "Noch keine Analyse vorhanden"}, status=404)
    fehler = {}
    liste = _gefiltert(r, request.query, fehler)
    if request.query.get("nur_ids") == "1":    # Punkt 91: „alle N gefilterten auswählen“
        return web.json_response({"gesamt": len(liste), "ids": [e["entity_id"] for e in liste]})
    off = max(0, int(request.query.get("offset", 0) or 0))
    lim = int(request.query.get("limit", 100) or 0)          # 0 = alle
    seite = liste[off:off + lim] if lim > 0 else liste[off:]
    domains = sorted({e["domain"] for e in r["entitaeten"]})
    alle = r["entitaeten"]
    return web.json_response({"gesamt": len(liste), "alle": len(alle), "offset": off, "limit": lim, "domains": domains,
                              "filter_fehler": fehler, "optionen": spaltenfilter.optionen(alle),
                              "summen": {"gefiltert": summen(liste), "gesamt": summen(alle)},
                              "zeilen": seite})


async def export_csv(request):
    ziel, vm = _parameter(request)
    r = await asyncio.to_thread(Z.probelauf, ziel, vm)
    if not r:
        raise web.HTTPNotFound()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["entity_id", "herkunft_art", "herkunft", "herkunft_info", "labels", "status", "rows", "rows_24h", "state_class", "lts_rows", "kurz_rows",
                "kurzzeit", "kurzzeit_regel", "langzeit", "langzeit_regel",
                "loeschmenge_sofort_states", "loeschmenge_sofort_kurz", "loeschmenge_sofort_lts",
                "attr_bestand_bytes", "attr_zeilen", "attr_zuwachs_tag_bytes", "attr_avg_bytes",
                "loeschmenge_sofort_attr_bytes", "attr_anteil_prozent", "hinweise",
                # Punkte 118/120 (0.4.11): erster/letzter über alle Datenarten, dahinter je Datenart
                *[f"{a}{k}" for a in ("", "states_", "kurz_", "lts_") for k in ("erster", "letzter")]])
    ts = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) if t else ""  # noqa: E731
    zeilen = _gefiltert(r, request.query)      # hängt auch die Labels an (Punkt 115)
    name = "dbh_probelauf.csv"
    if request.method == "POST":               # Punkt 91: „Auswahl als CSV“ – genau die gewählten Entitäten, ohne Filter
        ids = set((await request.json()).get("ids") or [])
        zeilen, name = [e for e in r["entitaeten"] if e["entity_id"] in ids], "dbh_auswahl.csv"
    for e in zeilen:
        wv = e["werte"] or {}
        zelle = lambda a, k: (wv.get(a) or {}).get(k) or ""  # noqa: E731
        w.writerow([e["entity_id"], e.get("herkunft_art", ""), e.get("herkunft", ""), e.get("herkunft_info", ""),
                    e.get("labels", ""), e["status"], e["rows"], e["rows_24h"], e["state_class"], e["lts_rows"], e["kurz_rows"],
                    zelle("kurzzeit", "anzeige"), zelle("kurzzeit", "regel"), zelle("langzeit", "anzeige"), zelle("langzeit", "regel"),
                    e["loeschbar"]["states"], e["loeschbar"]["kurz"], e["loeschbar"]["lts"],
                    *[("" if e.get(k) is None else (round(e[k]) if isinstance(e[k], float) and k != "attr_anteil" else e[k]))
                      for k in ("attr_bytes", "attr_zeilen", "attr_zuwachs_tag", "attr_avg", "attr_loesch", "attr_anteil")],
                    " | ".join(e["hinweise"]),
                    *[ts(e.get(k)) for k in ("first_ts", "last_ts", "st_first_ts", "st_last_ts", "kurz_first_ts", "kurz_last_ts",
                                             "lts_first_ts", "lts_last_ts")]])
    return web.Response(text="﻿" + buf.getvalue(), content_type="text/csv", charset="utf-8",
                        headers={"Content-Disposition": f"attachment; filename={name}"})


async def regeln_lesen(_request):
    daten = rg.lade(REGELN, VORLAGE)
    return web.json_response({"daten": daten, "version": Z.mtime(REGELN),
                              "ueberschneidungen": rg.entity_ueberschneidungen(daten)})


async def regeln_schreiben(request):
    body = await request.json()
    if float(body.get("version") or 0) != Z.mtime(REGELN):
        return web.json_response({"fehler": "Regeln wurden zwischenzeitlich geändert – bitte neu laden"}, status=409)
    daten = rg.speichere(REGELN, body["daten"])
    return web.json_response({"daten": daten, "version": Z.mtime(REGELN),
                              "ueberschneidungen": rg.entity_ueberschneidungen(daten)})


# Punkte 123/124 (0.5.2): Regeln verwalten – Zurücksetzen, Sicherungen, Export und Import.
# Jede Ersetzung legt vorher eine Sicherung an und ist gesperrt, solange ein Löschlauf läuft.
def _ersetzen_gesperrt(body):
    if Z.job and Z.job.get("art") == "loeschen":
        return web.json_response({"fehler": "gesperrt, solange der Löschlauf läuft"}, status=409)
    if float(body.get("version") or 0) != Z.mtime(REGELN):
        return web.json_response({"fehler": "Regeln wurden zwischenzeitlich geändert – bitte neu laden"}, status=409)
    return None


def _ersetze_regeln(daten, grund):
    sicherung = rg.sichere(REGELN, SICHERUNGEN, grund)
    daten = rg.speichere(REGELN, daten)
    _LOG.info("Regeln ersetzt (%s): %d Regeln, davon %d aktiv; Sicherung %s", grund, *rg.kennzahlen(daten).values(), sicherung)
    return web.json_response({"daten": daten, "version": Z.mtime(REGELN), "sicherung": sicherung,
                              "ueberschneidungen": rg.entity_ueberschneidungen(daten)})


def _vorlage():
    with open(VORLAGE, encoding="utf-8") as f:
        return rg.lies_import(json.load(f))


async def regeln_verwalten(_request):
    """Kennzahlen für die Rückfragen: aktuelle Regeln, Startregeln und die Liste der Sicherungen."""
    return web.json_response({"aktuell": rg.kennzahlen(rg.lade(REGELN, VORLAGE)), "vorlage": rg.kennzahlen(_vorlage()),
                              "sicherungen": rg.liste_sicherungen(SICHERUNGEN), "behalten": rg.SICHERUNGEN_BEHALTEN,
                              "gesperrt": bool(Z.job and Z.job.get("art") == "loeschen")})


async def regeln_zuruecksetzen(request):
    body = await request.json()
    gesperrt = _ersetzen_gesperrt(body)
    return gesperrt if gesperrt is not None else _ersetze_regeln(_vorlage(), "zuruecksetzen")


async def regeln_wiederherstellen(request):
    body = await request.json()
    try:
        daten = rg.lies_sicherung(SICHERUNGEN, body.get("name"))
    except (rg.RegelFehler, ValueError) as e:
        return web.json_response({"fehler": str(e)}, status=400)
    gesperrt = _ersetzen_gesperrt(body)
    return gesperrt if gesperrt is not None else _ersetze_regeln(daten, "wiederherstellen")


async def regeln_export(_request):
    daten = rg.lade(REGELN, VORLAGE)
    inhalt = {"_export": {"app": "RS DB Hygiene Manager", "zeit": time.strftime("%Y-%m-%d %H:%M:%S")}, **daten}
    name = f"rs_db_hygiene_regeln_{time.strftime('%Y-%m-%d')}.json"
    return web.Response(text=json.dumps(inhalt, ensure_ascii=False, indent=1), content_type="application/json", charset="utf-8",
                        headers={"Content-Disposition": f"attachment; filename={name}"})


async def regeln_import(request):
    """Importierte Datei prüfen (pruefen = true: nur Vorschau) bzw. die Regeln damit ersetzen."""
    body = await request.json()
    try:
        daten = rg.lies_import(body.get("datei"))
    except (rg.RegelFehler, ValueError, TypeError) as e:
        return web.json_response({"fehler": str(e)}, status=400)
    if body.get("pruefen"):
        return web.json_response({"neu": rg.kennzahlen(daten), "aktuell": rg.kennzahlen(rg.lade(REGELN, VORLAGE)),
                                  "export": (body.get("datei") or {}).get("_export")})
    gesperrt = _ersetzen_gesperrt(body)
    return gesperrt if gesperrt is not None else _ersetze_regeln(daten, "import")


async def regel_vorschau(request):
    """Welche Entitäten träfe diese Regel – und wo wird sie im Regelwerk überstimmt (Punkt 29)?

    Treffer: die Regel allein betrachtet (mit Zähler-Schutz). Überstimmt: im vollständigen Regelwerk, in das die
    Regel an ihrer Stelle eingesetzt wird (bestehende ID ersetzt, neue am Ende), gewinnt bei mindestens einem Wert,
    den sie setzt, eine andere Regel.
    """
    body = await request.json()
    regel = rg.normalisiere({"regeln": [body], "events": {}})["regeln"][0]
    regel["aktiv"] = True
    if regel["typ"] == "event":
        return web.json_response(await _event_vorschau(regel))
    allein = rg.Aufloeser({"standard": rg.STANDARD, "regeln": [regel]})
    bestand = rg.lade(REGELN, VORLAGE)
    liste = [regel if x["id"] == regel["id"] else x for x in bestand["regeln"]]
    if all(x["id"] != regel["id"] for x in bestand["regeln"]):
        liste.append(regel)
    gesamt = rg.Aufloeser({"standard": bestand["standard"], "regeln": liste})
    r = await asyncio.to_thread(Z.probelauf, None, False)
    treffer, geschuetzt, ueberstimmt, von = [], [], 0, {}
    for e in (r or {}).get("entitaeten", []):
        x = allein.loese(e["entity_id"], e["state_class"], e["device_class"], e["status"])
        if x["geschuetzt"]:
            geschuetzt.append(e["entity_id"])
            continue
        if not any(x[a]["regel"] for a in rg.WERTE):
            continue
        treffer.append({"entity_id": e["entity_id"], "rows_24h": e["rows_24h"]})
        g = gesamt.loese(e["entity_id"], e["state_class"], e["device_class"], e["status"])
        sieger = {g[a]["regel"] for a in rg.WERTE if regel[a] and g[a]["regel"] != regel["id"]}
        if sieger:
            ueberstimmt += 1
            for s in sieger:
                von[s or "Standard"] = von.get(s or "Standard", 0) + 1
    treffer.sort(key=lambda t: -t["rows_24h"])
    return web.json_response({"anzahl": len(treffer), "treffer": treffer[:200], "geschuetzt": geschuetzt[:200],
                              "anzahl_geschuetzt": len(geschuetzt), "anzahl_ueberstimmt": ueberstimmt,
                              "ueberstimmt_von": dict(sorted(von.items(), key=lambda kv: -kv[1]))})


async def regel_pruefen(request):
    """Punkt 54: Warnungen vor dem Speichern einer Regel (Energie-Dashboard, Zähler, kurze Kurzzeit, Helfer, breite Event-Muster)."""
    body = await request.json()
    regel = rg.normalisiere({"regeln": [body], "events": {}})["regeln"][0]
    pl = await asyncio.to_thread(Z.probelauf, None, False)
    if not pl:
        return web.json_response({"warnungen": []})
    ktx = await asyncio.to_thread(warnungen.kontext, HA_CONFIG)
    return web.json_response({"warnungen": await asyncio.to_thread(warnungen.pruefe_regel, regel, pl, ktx)})


async def _event_vorschau(regel):
    """Vorschau einer Event-Regel (Punkt 51): Treffer sind Event-Typen; überstimmt, wo eine andere Event-Regel gewinnt."""
    bestand = rg.lade(REGELN, VORLAGE)
    liste = [regel if x["id"] == regel["id"] else x for x in bestand["regeln"]]
    if all(x["id"] != regel["id"] for x in bestand["regeln"]):
        liste.append(regel)
    gesamt = {"standard": bestand["standard"], "regeln": liste}
    r = await asyncio.to_thread(Z.probelauf, None, False)
    pkd = (r or {}).get("stand", {}).get("purge_keep_days") or _aktuelle_pkd()   # Punkt 102: kein fester Rückfall 365 mehr
    treffer, ueberstimmt, von = [], 0, {}
    for ev in (r or {}).get("events", []):
        allein = rg.event_regel({"regeln": [regel]}, ev["event_type"], pkd)
        if not allein["regel"]:
            continue
        treffer.append({"entity_id": ev["event_type"], "rows_24h": ev["zeilen_24h"]})
        sieger = rg.event_regel(gesamt, ev["event_type"], pkd)["regel"]
        if sieger != regel["id"]:
            ueberstimmt += 1
            von[sieger or "Standard"] = von.get(sieger or "Standard", 0) + 1
    treffer.sort(key=lambda t: -t["rows_24h"])
    return {"anzahl": len(treffer), "treffer": treffer[:200], "geschuetzt": [], "anzahl_geschuetzt": 0,
            "anzahl_ueberstimmt": ueberstimmt, "ueberstimmt_von": dict(sorted(von.items(), key=lambda kv: -kv[1])),
            "einheit": "Event-Typen"}


async def lts_ohne(_request):
    if not os.path.exists(CACHE):
        return web.json_response([])
    import sqlite3
    con = sqlite3.connect(f"file:{CACHE}?mode=ro", uri=True)
    z = [dict(zip(("statistic_id", "source", "rows", "in_registry"), x))
         for x in con.execute("SELECT * FROM lts_ohne_entitaet ORDER BY rows DESC")]
    con.close()
    return web.json_response(z)


# ---------------------------------------------------------------- Löschlauf (Stufe A)

def _loesch_optionen():
    opt = optionen()
    return (bool(opt.get("loeschen_freigegeben")), int(opt.get("loeschen_max_zeilen") or 5_000_000),
            int(opt.get("loeschen_max_zeilen_b") or 2_000_000))     # Punkt 82: eigene Grenze für Stufe B


def _pkd_wirksam():
    """Punkt 103 (0.4.8): Die configuration.yaml hat Vorrang; die App-Option wirkt nur, wenn sie kleiner ist (0 = Wert der Datei)."""
    yaml = analyse.lies_purge_keep_days(f"{HA_CONFIG}/configuration.yaml")
    opt = int(optionen().get("purge_keep_days") or 0)
    return min(opt, yaml) if opt else yaml


def _aktuelle_pkd():
    """purge_keep_days, wie die nächste Analyse ihn nähme (Punkt 83) – nach der Regel aus Punkt 103."""
    return _pkd_wirksam()


async def _loesch_plan(request_or_regel, ausnahme=None):
    regel = request_or_regel if isinstance(request_or_regel, (str, type(None))) else (request_or_regel.query.get("regel") or None)
    if ausnahme is None:   # Punkt 53: einmalige Ausnahme über der Obergrenze, vom Nutzer bestätigt
        ausnahme = not isinstance(request_or_regel, (str, type(None))) and request_or_regel.query.get("ausnahme") == "1"
    uhr = _Stoppuhr("Plan")                                   # Punkt 108: langsame Schritte ins Log
    pl = await asyncio.to_thread(Z.probelauf, None, False)   # ohne passive Regeln, Zeitraum = purge_keep_days
    uhr("Probelauf")
    if not pl:
        return None
    frei, grenze, grenze_b = _loesch_optionen()
    stufe_b = await asyncio.to_thread(_integration_da)
    uhr("Integration prüfen (HA-API)")
    exakt = stufe_b and await asyncio.to_thread(_integration_exakt)       # Punkt 121
    p = await asyncio.to_thread(loeschen.plan, pl, CACHE, grenze, regel, stufe_b, bool(ausnahme), grenze_b, exakt)
    uhr("Löschplan")
    p["freigegeben"] = frei
    pkd = await asyncio.to_thread(_aktuelle_pkd)
    p["sperren"] = loeschen.sperren(p, frei, Z.job, os.environ.get("SUPERVISOR_TOKEN"), pkd)
    # Läufe aus HA analysieren bei altem Stand vorher selbst (Punkt 83) – dafür keine Sperre wegen der Analyse
    p["sperren_auto"] = loeschen.sperren(p, frei, Z.job, os.environ.get("SUPERVISOR_TOKEN"), pkd, analyse_pruefen=False)
    p["analyse_veraltet"] = loeschen.analyse_veraltet(p["analyse_zeit"], p["purge_keep_days"], pkd, loeschen.ANALYSE_FRISCH_S)
    # Punkt 110 (0.4.9): während eines Backups startet kein neuer Lauf (manuell und aus HA)
    bk = await asyncio.to_thread(loeschen.backup_laeuft, os.environ.get("SUPERVISOR_TOKEN"))
    uhr("Backup prüfen")
    p["backup"] = bk[1] if bk[0] else None
    if bk[0] and not Z.lauf:
        text = f"Backup läuft ({bk[1]}) – der Löschlauf startet erst danach."
        p["sperren"].append(text)
        p["sperren_auto"].append(text)
    regel_daten = rg.lade(REGELN, VORLAGE)
    p["regel_namen"] = {r["id"]: r["name"] for r in regel_daten["regeln"]}
    p["verwaist_ungedeckt"] = loeschen.verwaist_ungedeckt(pl, regel_daten)
    p["nicht_ausgefuehrt"] = _nicht_ausgefuehrt(pl, regel_daten, p["stufe_b"])
    try:   # Punkt 54: Risiken dieses Laufs
        ktx = await asyncio.to_thread(warnungen.kontext, HA_CONFIG)
        uhr("Energie- und Helfer-Quellen")
        p["risiken"] = warnungen.risiken_plan(p, pl, ktx)
    except Exception:  # noqa: BLE001 – Warnungen sind Zusatz, der Plan soll trotzdem kommen
        _LOG.exception("Risiken des Plans nicht ermittelt")
        p["risiken"] = []
    laeufe = await asyncio.to_thread(loeschen.protokolle, LAEUFE, 1, Z.lauf.start if Z.lauf else None)
    uhr("Löschprotokoll")
    p["prognose_bytes"] = (pl.get("summe") or {}).get("prognose_bytes")      # für den Status der Integration
    p["prognose_tage"] = (pl.get("stand") or {}).get("ziel_tage")
    p["letzter_lauf"] = {k: laeufe[0].get(k) for k in ("start", "ende", "status", "geloescht", "geplant_zeilen", "nur_regel", "fehler", "backup_warte_s")} if laeufe else None
    return p


async def loesch_plan(request):
    p = await _loesch_plan(request)
    if p is None:
        return web.json_response({"fehler": "Noch keine Analyse vorhanden"}, status=404)
    return web.json_response(p)


async def loesch_start(request):
    body = await request.json()
    p = await _loesch_plan(body.get("regel") or None, bool(body.get("ausnahme")))
    if p is None:
        return web.json_response({"fehler": "Noch keine Analyse vorhanden"}, status=404)
    if p["sperren"]:
        return web.json_response({"fehler": " ".join(p["sperren"])}, status=409)
    if body.get("kennung") != p["kennung"]:
        return web.json_response({"fehler": "Der Plan hat sich seit der Anzeige geändert (Regeln oder Analyse) – bitte neu laden und erneut prüfen."}, status=409)
    if not await _starte_lauf(p, "manuell"):
        return web.json_response({"fehler": "Es läuft bereits ein Job"}, status=409)
    return web.json_response({"gestartet": True, "job": Z.job}, status=202)


async def _starte_lauf(p, ausgeloest):
    db = optionen().get("db_pfad", f"{HA_CONFIG}/home-assistant_v2.db")
    lauf = loeschen.Lauf(p, db, LAEUFE, os.environ.get("SUPERVISOR_TOKEN"), ausgeloest)

    async def danach():   # danach Protokoll aufräumen und frische Zahlen für Kacheln und Löschpotenzial
        Z.lauf = None
        await asyncio.to_thread(_protokoll_aufraeumen)
        await starte_job("analyse")

    ok = await starte_job("loeschen", lauf.fuehre_aus, {"fortschritt": lauf.fortschritt, "nur_regel": p["nur_regel"]}, danach)
    if ok:
        Z.lauf = lauf
    return ok


# ---------------------------------------------------------------- Automatik (Punkt 62): Schnittstelle für die Integration

_AUTO = {"zeit": 0, "schl": None, "p": None, "rechnet": False}
LANGSAM_S = 5
WARNUNG_S = 10      # Punkt 117 (0.4.10): erst ab hier WARNING; der Probelauf braucht nach jeder Analyse gut 5 s


def _langsam(dauer, text, *args):
    """Punkt 117: langsame Schritte ab LANGSAM_S als INFO, ab WARNUNG_S als WARNING ins Log."""
    if dauer > LANGSAM_S:
        _LOG.log(logging.WARNING if dauer > WARNUNG_S else logging.INFO, text, *args)


class _Stoppuhr:
    """Punkt 108 (0.4.9): misst Schritte; was länger als LANGSAM_S dauert, steht mit Name und Dauer im Log
    (ab 0.4.10 erst über WARNUNG_S als Warnung, Punkt 117)."""

    def __init__(self, was):
        self.was, self.t = was, time.time()

    def __call__(self, schritt):
        jetzt = time.time()
        _langsam(jetzt - self.t, "%s: Schritt „%s“ dauerte %.1f s", self.was, schritt, jetzt - self.t)
        self.t = jetzt


async def _auto_plan_rechnen(schl):
    if _AUTO["rechnet"]:
        return
    _AUTO["rechnet"] = True
    t0 = time.time()
    try:
        p = await _loesch_plan(None)
        _AUTO.update(zeit=time.time(), schl=schl, p=p)
    except Exception:  # noqa: BLE001 – der alte Stand bleibt gültig
        _LOG.exception("Status für HA: Plan nicht berechnet")
    finally:
        _AUTO["rechnet"] = False
        dauer = time.time() - t0
        _langsam(dauer, "Status für HA: Plan neu berechnet in %.1f s (die Antworten kamen solange aus dem letzten Stand)", dauer)


async def _auto_plan():
    """Plan für den Status der Integration (fragt alle 30 s, im Lauf alle 5 s).

    Ab 0.4.9 (Punkt 108) wartet die Abfrage nie auf die Neuberechnung: Ist der Stand älter als 15 s oder haben sich
    Analyse, Regeln, Optionen oder der Job geändert, rechnet ein Hintergrund-Task nach; geantwortet wird sofort mit dem
    letzten Stand. Nur ganz ohne Stand (erste Abfrage nach dem Start) wird gewartet."""
    schl = (Z.mtime(CACHE), Z.mtime(REGELN), Z.mtime(OPTIONEN_PFAD), (Z.job or {}).get("art"))
    if _AUTO["p"] is None:
        await _auto_plan_rechnen(schl)
    elif _AUTO["schl"] != schl or time.time() - _AUTO["zeit"] > 15:
        asyncio.create_task(_auto_plan_rechnen(schl))
    return _AUTO["p"]


def _info(pfad):
    """Tabelle info eines Caches (Analyse oder Attribut-Analyse) als dict, leer wenn es ihn nicht gibt."""
    if not os.path.exists(pfad):
        return {}
    import sqlite3
    con = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
    try:
        return dict(con.execute("SELECT schluessel, wert FROM info"))
    except sqlite3.Error:
        return {}
    finally:
        con.close()


async def auto_status(_request):
    uhr = _Stoppuhr("Status für HA")
    p = await _auto_plan()
    uhr("Plan (Zwischenspeicher)")
    info = _info(CACHE)
    db = optionen().get("db_pfad", f"{HA_CONFIG}/home-assistant_v2.db")
    db_bytes = os.path.getsize(db) if os.path.exists(db) else None
    laeufe = await asyncio.to_thread(loeschen.protokolle, LAEUFE, 1, Z.lauf.start if Z.lauf else None)
    uhr("Löschprotokoll")
    st = automatik.status(p, Z.job, Z.letzter_fehler, info, db_bytes, (p or {}).get("prognose_bytes"),
                          optionen(), laeufe, _info(ATTR), (p or {}).get("prognose_tage"))
    st["stand_alter_s"] = round(time.time() - _AUTO["zeit"], 1) if _AUTO["zeit"] else None
    return web.json_response(st)


async def auto_loeschlauf(request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 – Aufruf ohne Body
        body = {}
    if not optionen().get("automatik_freigegeben"):
        return web.json_response({"gestartet": False, "sperren": [automatik.AUTOMATIK_AUS]}, status=409)
    regel = body.get("regel") or None
    p = await _loesch_plan(regel, False)
    if p is None:
        return web.json_response({"gestartet": False, "sperren": ["Noch keine Analyse vorhanden."]}, status=409)
    if p["sperren_auto"]:
        return web.json_response({"gestartet": False, "sperren": p["sperren_auto"]}, status=409)
    if p["analyse_veraltet"]:
        # Punkt 83: Analyse älter als 6 h oder purge_keep_days geändert – erst analysieren, dann mit frischem Plan löschen
        grund = p["analyse_veraltet"]
        if not await starte_job("analyse", danach=lambda: _auto_lauf_nach_analyse(regel, grund)):
            return web.json_response({"gestartet": False, "sperren": ["Es läuft bereits ein Job."]}, status=409)
        _AUTO["zeit"] = 0
        _LOG.info("Lauf aus HA: zuerst Analyse (%s)", grund)
        return web.json_response({"gestartet": True, "analyse_vorher": True, "grund": grund, "plan": None, "risiken": []}, status=202)
    if not await _starte_lauf(p, "automatik"):
        return web.json_response({"gestartet": False, "sperren": ["Es läuft bereits ein Job."]}, status=409)
    _AUTO["zeit"] = 0
    st = automatik.status(p, Z.job, None, {}, None, None, optionen(), [])
    return web.json_response({"gestartet": True, "plan": st["plan"], "risiken": [r["text"] for r in p.get("risiken", [])]}, status=202)


async def _auto_lauf_nach_analyse(regel, grund):
    """Punkt 83: nach der vorgeschalteten Analyse den Lauf mit frischem Plan starten. Darf er nicht starten, steht das im
    Löschprotokoll (Status „nicht gestartet“) – die Integration meldet dann trotzdem rs_db_hygiene_lauf_beendet."""
    _AUTO["zeit"] = 0
    p, gruende = None, []
    if not (Z.letzter_job or {}).get("ok"):
        gruende = [f"Die vorgeschaltete Analyse ist fehlgeschlagen ({Z.letzter_fehler or 'ohne Meldung'})."]
    else:
        p = await _loesch_plan(regel, False)
        if p is None:
            gruende = ["Noch keine Analyse vorhanden."]
        elif p["sperren_auto"] or p["analyse_veraltet"]:
            gruende = p["sperren_auto"] or [p["analyse_veraltet"]]
    if not gruende:
        p["analyse_vorher"] = True
        if await _starte_lauf(p, "automatik"):
            return
        gruende = ["Es läuft bereits ein Job."]
    _LOG.warning("Lauf aus HA nach der Analyse (%s) nicht gestartet: %s", grund, " ".join(gruende))
    await asyncio.to_thread(loeschen.protokoll_nicht_gestartet, LAEUFE, "automatik", gruende, p)
    _AUTO["zeit"] = 0


async def auto_analyse(_request):
    ok = await starte_job("analyse")
    _AUTO["zeit"] = 0
    return web.json_response({"gestartet": ok, "sperren": [] if ok else ["Es läuft bereits ein Job."]}, status=202 if ok else 409)


async def auto_attribut_analyse(_request):
    """ab 0.4.1 (Punkt 65): Attribut-Analyse aus HA heraus (Knopf, Dienst, Automation) – lesend, ohne Automatik-Schalter."""
    ok = await starte_job("attribute")
    _AUTO["zeit"] = 0
    return web.json_response({"gestartet": ok, "sperren": [] if ok else ["Es läuft bereits ein Job."]}, status=202 if ok else 409)


async def auto_abbrechen(_request):
    if not Z.lauf:
        return web.json_response({"abbruch": False, "sperren": ["Kein Löschlauf aktiv."]}, status=409)
    Z.lauf.abbruch = True
    return web.json_response({"abbruch": True})


async def loesch_abbrechen(_request):
    if not Z.lauf:
        return web.json_response({"fehler": "Kein Löschlauf aktiv"}, status=409)
    Z.lauf.abbruch = True
    return web.json_response({"abbruch": True})


async def loesch_protokoll(_request):
    laufend = Z.lauf.start if Z.lauf else None
    return web.json_response(await asyncio.to_thread(loeschen.protokolle, LAEUFE, 100000, laufend))


async def loesch_summe(_request):
    return web.json_response(await asyncio.to_thread(loeschen.summe_liste, LAEUFE_SUMME))


def _protokoll_aufraeumen():
    """Monatssumme nachtragen und Läufe älter als protokoll_tage löschen (Punkt 45)."""
    try:
        tage = int(optionen().get("protokoll_tage", 365) or 0)
        weg = loeschen.aufraeumen(LAEUFE, LAEUFE_SUMME, tage, Z.lauf.start if Z.lauf else None)
        if weg:
            _LOG.info("Löschprotokoll: %d Läufe älter als %d Tage entfernt", weg, tage)
    except Exception:  # noqa: BLE001 – Aufräumen darf die App nicht stören
        _LOG.exception("Löschprotokoll aufräumen fehlgeschlagen")


_INTEGRATION = {"zeit": 0, "da": False, "exakt": False}


def _integration_abfragen():
    if time.time() - _INTEGRATION["zeit"] > 60:
        d = loeschen.integration_dienste(os.environ.get("SUPERVISOR_TOKEN"))
        _INTEGRATION.update(zeit=time.time(), da="purge" in d, exakt="purge_states" in d)


def _integration_da():
    """Ist die Mini-Integration (Dienst rs_db_hygiene.purge) aktiv? 60 s zwischengespeichert."""
    _integration_abfragen()
    return _INTEGRATION["da"]


def _integration_exakt():
    """Punkt 121 (0.4.12): Bietet die Integration purge_states (ab 0.4.5) – Löschen bis zur lokalen Mitternacht?"""
    _integration_abfragen()
    return _INTEGRATION["exakt"]


def _nicht_ausgefuehrt(pl, regel_daten, stufe_b=False):
    """Was ein Lauf nicht löscht (Punkt 44): Attribute (Stufe C) – ohne Integration auch Langzeit, 5-Min-Statistik, Events."""
    aktiv = [r for r in regel_daten["regeln"] if r.get("aktiv", True)]
    su = pl["summe"]
    return {"langzeit_regeln": sum(1 for r in aktiv if r.get("langzeit") not in (None, "", "unbegrenzt")),
            "attribute_regeln": sum(1 for r in aktiv if r.get("attribute") not in (None, "", "wie_kurzzeit")),
            "lts_zeilen": su.get("loeschbar_lts", 0), "kurz_zeilen": su.get("loeschbar_kurz", 0),
            "attr_bytes": su.get("loeschbar_attr_bytes"), "events_zeilen": su.get("loeschbar_events", 0), "stufe_b": stufe_b}


async def beim_start(app):
    await asyncio.to_thread(_protokoll_aufraeumen)
    try:   # Punkt 85: Prüfung bei jedem Start (die GUI zeigt sie als Banner)
        for w in (await asyncio.to_thread(pkd_pruefung))["warnungen"]:
            _LOG.warning("%s %s", w.get("titel", ""), w["text"])
    except Exception:  # noqa: BLE001 – die Prüfung darf den Start nicht verhindern
        _LOG.exception("Prüfung purge_keep_days fehlgeschlagen")
    SCHLUESSEL["wert"] = await asyncio.to_thread(automatik.schluessel, DATA)
    if os.environ.get("SUPERVISOR_TOKEN"):   # nur im Add-on: bei HA anmelden (Discovery)
        app["discovery"] = asyncio.create_task(asyncio.to_thread(automatik.melde_discovery, SCHLUESSEL["wert"]))
    app["nachtlauf"] = asyncio.create_task(nachtlauf())


def erstelle_app():
    app = web.Application(middlewares=[nur_ingress], client_max_size=4 * 1024 * 1024)
    app.add_routes([
        web.get("/", index),
        web.get("/api/status", status),
        web.post("/api/job/{art}", job_starten),
        web.get("/api/probelauf", probelauf_summe),
        web.get("/api/entitaeten", entitaeten),
        web.get("/api/export.csv", export_csv),
        web.post("/api/export.csv", export_csv),
        web.get("/api/regeln", regeln_lesen),
        web.put("/api/regeln", regeln_schreiben),
        web.post("/api/regeln/vorschau", regel_vorschau),
        web.post("/api/regeln/pruefen", regel_pruefen),
        web.get("/api/regeln/verwalten", regeln_verwalten),
        web.post("/api/regeln/zuruecksetzen", regeln_zuruecksetzen),
        web.post("/api/regeln/wiederherstellen", regeln_wiederherstellen),
        web.get("/api/regeln/export", regeln_export),
        web.post("/api/regeln/import", regeln_import),
        web.get("/api/lts_ohne_entitaet", lts_ohne),
        web.get("/api/loeschen/plan", loesch_plan),
        web.post("/api/loeschen/start", loesch_start),
        web.post("/api/loeschen/abbrechen", loesch_abbrechen),
        web.get("/api/loeschen/protokoll", loesch_protokoll),
        web.get("/api/loeschen/summe", loesch_summe),
        web.get("/api/auto/status", auto_status),
        web.post("/api/auto/loeschlauf", auto_loeschlauf),
        web.post("/api/auto/analyse", auto_analyse),
        web.post("/api/auto/attribut_analyse", auto_attribut_analyse),
        web.post("/api/auto/abbrechen", auto_abbrechen),
    ])
    app.on_startup.append(beim_start)
    return app


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    web.run_app(erstelle_app(), host="0.0.0.0", port=int(os.environ.get("DBH_PORT", 8099)), access_log=None)
