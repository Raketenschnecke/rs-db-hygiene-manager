"""Statusbericht zum Regelwerk für die wöchentliche Mail – liest nur, rechnet eine reine Funktion.

Herkunft: Punkt 376 (0.6.2b1): Statusbericht. Die Anbindung (Endpunkt, Datenbeschaffung, Mailversand) liegt in main.py;
dieses Modul bekommt ein Eingabe-Dict `e` und liefert ein Ergebnis-Dict. Es fragt nie die Recorder-Datenbank ab und öffnet
zuwachs.db ausschließlich lesend (mode=ro). Nur Standardbibliothek plus die App-Module probelauf, regeln und tote.
baue() wirft nie: Jeder Teilblock ist abgesichert, bei Fehlern bleiben seine Felder null und ampel_gruende nennt den Grund.

Eingabe `e` (jeder Eintrag darf fehlen oder None sein – betroffene Felder werden null, der Grund steht in ampel_gruende):
  probelauf, tote, regelcheck, vorschlaege, laeufe, zuwachs_pfad, registry, stichtag, db_bytes, prognose_bytes,
  prognose_tage, lts_ohne, app_version, regeln

Festlegungen
  Eigene Regel      Der wirksame Kurzzeit-Wert einer aktiven Entität stammt aus einer Regel (werte.kurzzeit.regel gesetzt), die
                    weder den App-Standard (keine Regel-ID) noch die Regel HA-Standard (Typ ha_standard, ID HA:standard) ist.
                    Verwaiste und deaktivierte Entitäten zählen für die Abdeckung nicht.
  Bytegewichte      wie verlauf._gewichte (probelauf.B_STATE, B_KURZ, B_LTS, B_EVENT, B_ATTR_FALLBACK * B_ATTR_FAKTOR je Zeile);
                    1 MB = 10⁶ Byte, 1 GB = 10⁹ Byte (dezimal, wie Sensor und Oberfläche der App).
  zuwachs_mb_woche  Ist: neue Zeilen je Tabelle aus zuwachs.db der letzten `zeitraum_tage` vollen Tage (ohne heute) mal
                    Bytegewicht, Ø je gemessenem Tag auf 7 Tage umgerechnet (inklusive Events der Kennung ev:). Gemessen heißt:
                    Tag liegt in der Abdeckung von states und kurz (info <quelle>_von/_bis) und nicht in deren
                    <quelle>_luecken; andere Tage zählen nicht als 0. Ohne gemessenen Tag null mit Grund.
  zuwachs_prognose_mb_woche
                    Näherung gleicher Art (Zufluss, nicht Endbestand) aus dem Probelauf: je aktiver Entität rows_24h States
                    plus Attribute, 288 5-Min- und 24 Stundenzeilen, falls die Entität Statistik hat (kurz_rows/lts_rows > 0),
                    dazu die Event-Zeilen je Tag der Event-Typen; mal 7. Ohne Probelauf null. Ist (neue, erstmals benutzte
                    Attributzeilen) und Prognose (Attribut je State) sind nur bedingt vergleichbar – die Prognose liegt
                    tendenziell höher; die Ampel-Schwelle ist daher eine Prozentgrenze, kein exakter Vergleich.
  Zeitraum          Kalendertage heute − zeitraum + 1 bis heute (Ortszeit), also bei 7 Tagen die letzten 7 Tage samt heute.
                    Läufe zählen, wenn ihr Starttag darin liegt (nicht „jetzt − n·24 h“).
  Läufe             Status laut loeschen.py: fertig = ok, Fehler = fehler, abgebrochen und unterbrochen = abgebrochen;
                    „läuft“ und „nicht gestartet“ zählen nur in gesamt. Nacht ohne Lauf = Kalendertag im Zeitraum ohne Lauf mit
                    einem dieser Status – auch ein unterbrochener Lauf (ohne ende) zählt als Lauf seines Starttags. Der heutige
                    Tag zählt erst, wenn es nach NACHT_ENDE_STUNDE Uhr ist.
  Löschmenge        „entfernte Zeilen“ = States/Statistik + Attributzeilen, überall dieselbe Formel je Paket: art attr nur
                    attr_geloescht (vorher − nachher sind dort abgetrennte States); sonst vorher − nachher (bei Paketen mit einer
                    Entität die gemessene Paket-Zahl wie loeschen.py) plus attr_geloescht. geloescht_zeilen = Summe über alle
                    Pakete der Läufe im Zeitraum; je_regel ordnet über die Kurzzeit-Regel der Entität im Probelauf zu, der Rest
                    (Event-Typen, Entitäten ohne Regel, Regeln nicht im Probelauf) steht unter „ohne_zuordnung“. Die Summe je
                    Regel ist damit immer gleich dem Gesamtwert.
  Umbenennung       Registry-Eintrag mit gleicher id (ersatzweise platform + unique_id) und anderer entity_id; als Verdacht zählt
                    nur, wenn eine Regel die alte ID fest nennt (tote.feste_ids).
  purge_keep_days   aus dem Probelauf (stand), zum Vergleich: damit rechnet der Prognose-Sensor der Integration.
  prognose_tage     ziel_tage des Probelaufs: Zeitraum, für den prognose_gb und die Ersparnis rechnen (ohne Angabe = purge_keep_days).
  ersparnis_mb der Empfehlungen
                    ersparnis_bytes des Vorschlags (eingesparter Platz im Prognosezustand) in MB – keine zeitliche Umrechnung.
  details           "zahlen" (Standard): keine Entity-ID, kein Muster, kein Event-Typ in der Antwort. Empfehlungstexte sind feste
                    Texte je art (ART_TEXT, unbekannte art: „Neue Regel“), Regelnamen nur übernommen, wenn sie nichts davon enthalten (sonst Regel-ID);
                    "ids": alles.
"""
import datetime
import json
import os
import re
import sqlite3
import tempfile
import time

import probelauf
import regeln as rg
import tote as tote_modul

# Empfehlungstexte je Vorschlagsart (optimierung.vorschlaege, Feld art) – feste Texte, daher auch bei details=zahlen ohne IDs
ART_TEXT = {"gruppe": "Gruppenregel für gleichartige Entitäten (ein Muster statt vieler Einzelregeln)",
            "klasse": "Regel für eine Geräteklasse",
            "herkunft": "Regel für eine Integration (alle Entitäten ohne eigene Kurzzeit, auch künftige)",
            "entitaet": "Einzelregel für eine Entität (z. B. Vielschreiber, große Attribute, nirgends verwendet)",
            "langzeit": "Langzeitstatistik nirgends verwendeter Entitäten begrenzen",
            "attribute": "Attribute mit großem Zuwachs kürzer aufbewahren",
            "event": "Regel für einen Event-Typ mit viel Volumen",
            "unbenutzt": "Regel für nirgends verwendete Entitäten (passt sich bei jeder Analyse an)",
            "verwaist": "Regel für verwaiste Entitäten"}

VORGABEN = {"abdeckung_gelb_pct": 80, "schreibrate_faktor": 3.0, "zuwachs_prognose_pct": 120,
            "top_n_ohne_regel": 10, "tote_muster_rot": 1, "regelcheck_rot": 1}
MIN_ZEILEN_TAG = 100          # Schreibratensprung zählt erst ab so vielen Zeilen je Tag im aktuellen Zeitraum
NACHT_ENDE_STUNDE = 6         # ab dieser Uhrzeit zählt der heutige Tag als Nacht, in der ein Lauf hätte stattfinden müssen
MB = 10 ** 6          # wie Sensor und Oberfläche (dezimal)
GB = 10 ** 9
_ID_MUSTER = re.compile(r"\b[a-z_]+\.[a-z0-9_]+\b")
STATUS_OK, STATUS_FEHLER, STATUS_ABBRUCH = ("fertig",), ("Fehler",), ("abgebrochen", "unterbrochen")
OHNE_ZUORDNUNG = "ohne_zuordnung"
GRUENDE = ("geloescht", "unbekannt", "verwaist")


def schwellen_aus_optionen(opt):
    """Schwellen aus den App-Optionen bericht_<schlüssel>; fehlend oder ungültig → Vorgabe."""
    out = dict(VORGABEN)
    for k in VORGABEN:
        w = (opt or {}).get("bericht_" + k) if isinstance(opt, dict) else None
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w != w or w < 0:
            continue
        if k == "schreibrate_faktor":
            if w > 0:
                out[k] = float(w)
        else:
            out[k] = int(w)
    return out


def lies_json(pfad):
    try:
        with open(pfad, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def schreibe_json(pfad, daten):
    """Atomar: erst in eine eindeutige Temp-Datei im selben Ordner, dann os.replace (zwei Schreiber stören sich nicht)."""
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(pfad) + ".", suffix=".tmp", dir=os.path.dirname(pfad) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False)
        os.replace(tmp, pfad)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _datum(ts):
    return datetime.date.fromtimestamp(ts)


def _tag(ts):
    return (_datum(ts) - datetime.date(1970, 1, 1)).days


def _gewichte():
    return {"states": probelauf.B_STATE, "kurz": probelauf.B_KURZ, "lts": probelauf.B_LTS,
            "attr": probelauf.B_ATTR_FALLBACK * probelauf.B_ATTR_FAKTOR, "events": probelauf.B_EVENT}


def _zahl_ok(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


def _aktive(pl):
    return [x for x in (pl or {}).get("entitaeten", []) if isinstance(x, dict) and x.get("entity_id") and x.get("status") == "aktiv"]


def _registry_eintraege(reg):
    try:
        return [x for x in reg["data"]["entities"] if isinstance(x, dict) and x.get("entity_id")]
    except (KeyError, TypeError):
        return None


def _reg_map(eintraege):
    """{Schlüssel: [entity_id, platform, unique_id]}; Schlüssel = Registry-ID, ersatzweise pu:<platform>|<unique_id>."""
    out = {}
    for x in eintraege:
        k = x.get("id") or f"pu:{x.get('platform')}|{x.get('unique_id')}"
        out[k] = [x["entity_id"], x.get("platform"), x.get("unique_id")]
    return out


def stichtag_neu(e, jetzt=None):
    """Was als neuer Stichtag gespeichert wird: aktive Entitäten und die Registry-Zuordnung."""
    e = e if isinstance(e, dict) else {}
    jetzt = time.time() if jetzt is None else jetzt
    try:
        pl = e.get("probelauf")
        ein = _registry_eintraege(e.get("registry"))
        return {"zeit": jetzt, "app_version": e.get("app_version"),
                "entitaeten": sorted(x["entity_id"] for x in _aktive(pl)) if isinstance(pl, dict) else None,
                "registry": _reg_map(ein) if ein is not None else None}
    except Exception:  # noqa: BLE001 – ein Stichtag ohne Inhalt ist besser als ein Abbruch
        return {"zeit": jetzt, "app_version": e.get("app_version"), "entitaeten": None, "registry": None}


def _luecken(info, quelle):
    try:
        return {t for von, bis in json.loads(info.get(f"{quelle}_luecken") or "[]") for t in range(int(von), int(bis) + 1)}
    except (ValueError, TypeError):
        return set()


def _gemessen(info, quellen, von, bis):
    """Tage in [von, bis), die in der Abdeckung aller `quellen` liegen und in keiner ihrer Lücken."""
    try:
        a = max(int(info[f"{q}_von"]) for q in quellen)
        b = min(int(info[f"{q}_bis"]) for q in quellen)
    except (KeyError, ValueError, TypeError):
        return set()
    weg = set().union(*(_luecken(info, q) for q in quellen))
    return {t for t in range(max(von, a), min(bis, b + 1)) if t not in weg}


def _zuwachs_lesen(pfad, heute, zeitraum):
    """(aktuell {eid: Ø Zeilen/Tag}, davor {…}, Bytes je Woche | None, Tage aktuell, Tage davor) – nur lesend, nur gemessene Tage."""
    von, vor_von = heute - zeitraum, heute - 2 * zeitraum
    con = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True, timeout=30)
    try:
        info = dict(con.execute("SELECT schluessel, wert FROM info"))
        spalten = {z[1] for z in con.execute("PRAGMA table_info(tage)")}
        ev = "events" if "events" in spalten else "0"
        zeilen = con.execute(f"SELECT entity_id, tag, states, kurz, lts, attr, {ev} FROM tage WHERE tag >= ? AND tag < ?",
                             (vor_von, heute)).fetchall()
    finally:
        con.close()
    t_jetzt = _gemessen(info, ("states", "kurz"), von, heute)
    t_davor = _gemessen(info, ("states", "kurz"), vor_von, von)
    t_attr = _gemessen(info, ("attr",), vor_von, heute)
    g = _gewichte()
    summe, byt = {}, 0.0
    for eid, tag, s, k, lt, a, evn in zeilen:
        aktuell = tag >= von
        if tag not in (t_jetzt if aktuell else t_davor):
            continue
        s, k, lt, a, evn = (s or 0), (k or 0), (lt or 0), (a or 0), (evn or 0)
        if aktuell:
            byt += s * g["states"] + k * g["kurz"] + lt * g["lts"] + a * g["attr"] + evn * g["events"]
        if not str(eid).startswith("ev:"):
            ziel = summe.setdefault((aktuell, eid), 0)
            summe[(aktuell, eid)] = ziel + s + k + (a if tag in t_attr else 0)
    jetzt_r = {eid: v / len(t_jetzt) for (akt, eid), v in summe.items() if akt}
    davor_r = {eid: v / len(t_davor) for (akt, eid), v in summe.items() if not akt and t_davor}
    return jetzt_r, davor_r, (byt / len(t_jetzt) * 7 if t_jetzt else None), len(t_jetzt), len(t_davor)


def _prognose_zufluss(pl):
    g = _gewichte()
    summe = 0.0
    for x in _aktive(pl):
        r = x.get("rows_24h") or 0
        summe += r * g["states"] + r * g["attr"]
        if (x.get("kurz_rows") or 0) > 0 or (x.get("lts_rows") or 0) > 0:
            summe += probelauf.KURZ_PRO_TAG * g["kurz"] + probelauf.LTS_PRO_TAG * g["lts"]
    summe += sum((v.get("pro_tag") or 0) for v in pl.get("events") or [] if isinstance(v, dict)) * g["events"]
    return summe * 7


def _entfernt_je_entitaet(laeufe):
    """{Entität bzw. Event-Typ: entfernte Zeilen} über alle Pakete der Läufe – eine Formel für Gesamt und je Regel."""
    out = {}
    for p in laeufe:
        for pk in p.get("pakete") or []:
            if not isinstance(pk, dict):
                continue
            je = pk.get("je_entitaet") or {}
            for eid, v in je.items():
                if not isinstance(v, dict):
                    continue
                try:
                    attr = int(v.get("attr_geloescht") or 0)
                    if pk.get("art") == "attr":
                        n = attr           # dort sind vorher − nachher abgetrennte States, keine Löschung
                    else:
                        if v.get("nachher") is None:
                            d = 0
                        elif len(je) == 1 and pk.get("geloescht") is not None:
                            d = int(pk["geloescht"])
                        else:
                            d = max(0, int(v.get("vorher") or 0) - int(v["nachher"]))
                        n = d + attr
                except (TypeError, ValueError):
                    continue
                if n:
                    out[eid] = out.get(eid, 0) + n
    return out


def _unkritisch(text, kritisch):
    """True, wenn der Text keine Entity-ID, kein Muster (* ? [) und keinen der `kritisch` genannten Begriffe enthält."""
    t = str(text or "")
    return bool(t) and not _ID_MUSTER.search(t) and not any(c in t for c in "*?[") and not any(k in t for k in kritisch)


def _leer():
    return {"stand": {"analyse_zeit": None, "erstellt": None, "zeitraum_tage": None, "stichtag": None, "app_version": None},
            "ampel": None, "ampel_gruende": [],
            "abdeckung": {"entitaeten": None, "eigene_regel": None, "eigene_regel_pct": None, "nur_standard": None,
                          "neu_seit_stichtag": None, "neu_ohne_regel": None, "neu_ohne_regel_top": []},
            "drift": {"tote_muster": None, "tote_je_grund": {"geloescht": None, "unbekannt": None, "verwaist": None},
                      "regeln_ohne_treffer": None, "umbenennung_verdacht_anzahl": None, "umbenennung_verdacht": [],
                      "verwaist": None, "lts_ohne_entitaet": None, "schreibrate_sprung_anzahl": None, "schreibrate_sprung": []},
            "wirkung": {"db_gb": None, "prognose_gb": None, "prognose_tage": None, "purge_keep_days": None, "zuwachs_mb_woche": None, "zuwachs_prognose_mb_woche": None,
                        "geloescht_zeilen": None, "je_regel": [], "top_ohne_regel": []},
            "laeufe": {"gesamt": None, "ok": None, "fehler": None, "abgebrochen": None, "naechte_ohne_lauf": None,
                       "letzter": {"start": None, "ende": None, "status": None, "geloescht": None}},
            "regelcheck": {"rot": None, "gelb": None, "grau": None, "tot": None},
            "empfehlungen": []}


def _zahl(x, n=1):
    return None if x is None else round(x, n)


def baue(e, zeitraum_tage=7, top_n=5, details="zahlen", schwellen=None, jetzt=None):
    """Statusbericht als Dict (Struktur siehe Modulkopf und Schnittstelle). Wirft nie wegen fehlender oder kaputter Eingaben."""
    e = e if isinstance(e, dict) else {}
    s = {**VORGABEN, **(schwellen if isinstance(schwellen, dict) else {})}
    jetzt = time.time() if not _zahl_ok(jetzt) else jetzt
    try:
        zeitraum = max(1, int(zeitraum_tage or 7))
        top_n = max(0, int(top_n))
    except (TypeError, ValueError):
        zeitraum, top_n = 7, 5
    ids = details == "ids"
    heute = _tag(jetzt)
    out = _leer()
    gr, gelb, rot = [], [], []        # Gründe für fehlende Angaben; Ampel-Gründe gelb und rot
    pl = e.get("probelauf") if isinstance(e.get("probelauf"), dict) else None
    regeldaten = e.get("regeln") if isinstance(e.get("regeln"), dict) else None
    st = e.get("stichtag") if isinstance(e.get("stichtag"), dict) else None
    out["stand"].update(analyse_zeit=((pl or {}).get("stand") or {}).get("analyse_zeit"), erstellt=jetzt,
                        zeitraum_tage=zeitraum, stichtag=(st or {}).get("zeit"), app_version=e.get("app_version"))
    typ_von = {r.get("id"): r.get("typ") for r in (regeldaten or {}).get("regeln", []) if isinstance(r, dict)}
    akt = _aktive(pl) if pl else []
    kritisch = {x["entity_id"] for x in (pl or {}).get("entitaeten", []) if isinstance(x, dict) and x.get("entity_id")}
    kritisch |= {v["event_type"] for v in (pl or {}).get("events", []) if isinstance(v, dict) and v.get("event_type")}

    def eigene(x):
        rid = ((x.get("werte") or {}).get("kurzzeit") or {}).get("regel")
        return bool(rid) and rid != rg.HA_STANDARD_ID and typ_von.get(rid) != "ha_standard"

    def zeile(x):
        return {"entity_id": x["entity_id"], "zeilen_tag": x.get("rows_24h") or 0}

    def nach_zeilen(liste):
        return sorted(liste, key=lambda x: -(x.get("rows_24h") or 0))

    def abdeckung():
        ab = out["abdeckung"]
        n_eig = sum(1 for x in akt if eigene(x))
        ab.update(entitaeten=len(akt), eigene_regel=n_eig, eigene_regel_pct=round(100 * n_eig / len(akt), 1) if akt else None,
                  nur_standard=len(akt) - n_eig)
        out["wirkung"]["top_ohne_regel"] = [zeile(x) for x in nach_zeilen(x for x in akt if not eigene(x))[:top_n]]
        rangliste = {x["entity_id"] for x in nach_zeilen(akt)[:int(s["top_n_ohne_regel"])]}
        if st is not None and isinstance(st.get("entitaeten"), list):
            alt = {i for i in st["entitaeten"] if isinstance(i, str)}
            neu = [x for x in akt if x["entity_id"] not in alt]
            neu_ohne = nach_zeilen(x for x in neu if not eigene(x))
            ab.update(neu_seit_stichtag=len(neu), neu_ohne_regel=len(neu_ohne), neu_ohne_regel_top=[zeile(x) for x in neu_ohne[:top_n]])
            treffer = {x["entity_id"] for x in neu_ohne} & rangliste
            if treffer:
                gelb.append(f"{len(treffer)} neue Entität(en) ohne Regel gehören zu den {s['top_n_ohne_regel']} stärksten Schreibern.")
        else:
            gr.append("Erster Bericht – Vergleich ab dem nächsten.")
        out["drift"]["verwaist"] = sum(1 for x in pl.get("entitaeten") or [] if isinstance(x, dict) and rg.ist_verwaist(x.get("status")))
        if ab["eigene_regel_pct"] is not None and ab["eigene_regel_pct"] < s["abdeckung_gelb_pct"]:
            gelb.append(f"Nur {ab['eigene_regel_pct']:g} % der aktiven Entitäten haben eine eigene Regel "
                        f"(Schwelle {s['abdeckung_gelb_pct']} %).")

    def tote():
        t = e.get("tote")
        if not isinstance(t, dict):
            gr.append("Tote Muster nicht geprüft (keine Daten).")
            return
        je = dict.fromkeys(GRUENDE, 0)
        for lst in t.values():
            for m in lst or []:
                if isinstance(m, dict) and m.get("grund") in je:
                    je[m["grund"]] += 1
        n = sum(je.values())
        out["drift"]["tote_muster"], out["drift"]["tote_je_grund"] = n, je
        if n and n >= s["tote_muster_rot"] > 0:
            rot.append(f"{n} tote Regelmuster (Entität gelöscht, unbekannt oder verwaist).")

    def ohne_treffer():
        if not regeldaten or pl is None:
            gr.append("Regeln ohne Treffer nicht berechnet (Regeln oder Probelauf fehlen).")
            return
        treffer = {r.get("regel"): r.get("entitaeten") or 0 for r in pl.get("regeln") or [] if isinstance(r, dict)}
        out["drift"]["regeln_ohne_treffer"] = sum(
            1 for r in regeldaten.get("regeln", []) if isinstance(r, dict) and r.get("aktiv")
            and r.get("typ") not in ("ha_standard", "event") and not treffer.get(r.get("id")))

    def umbenennung():
        reg = _registry_eintraege(e.get("registry"))
        if reg is None:
            gr.append("Umbenennungen nicht geprüft (Registry nicht lesbar).")
            return
        if st is None:
            return                      # Grund „erster Bericht“ steht schon bei der Abdeckung
        if not isinstance(st.get("registry"), dict):
            gr.append("Stichtag ohne Registry – Umbenennungen sind erst ab dem nächsten Bericht prüfbar.")
            return
        alt = {k: v for k, v in st["registry"].items() if isinstance(v, (list, tuple)) and len(v) >= 3}
        nach_pu = {(v[1], v[2]): v[0] for v in alt.values() if v[2]}
        fest = {}
        for r in (regeldaten or {}).get("regeln", []):
            if isinstance(r, dict) and r.get("id") is not None:
                for m in tote_modul.feste_ids(r):
                    fest.setdefault(m, []).append(r["id"])
        paare = []
        for k, (neu_id, plat, uid) in _reg_map(reg).items():
            alt_id = alt[k][0] if k in alt else nach_pu.get((plat, uid)) if uid else None
            if alt_id and alt_id != neu_id and fest.get(alt_id):
                paare.append({"alt": alt_id, "neu": neu_id, "regeln": sorted(map(str, fest[alt_id]))})
        paare.sort(key=lambda p: p["alt"])
        out["drift"]["umbenennung_verdacht_anzahl"], out["drift"]["umbenennung_verdacht"] = len(paare), paare
        if paare:
            gelb.append(f"{len(paare)} umbenannte Entität(en) werden noch von Regeln unter dem alten Namen geführt.")

    def zuwachs():
        zp = e.get("zuwachs_pfad")
        if not zp or not os.path.exists(zp):
            gr.append("Keine Zuwachs-Statistik vorhanden – Zuwachs und Schreibratensprung nicht berechnet.")
            return
        try:
            aktuell, davor, wo, n_jetzt, n_davor = _zuwachs_lesen(zp, heute, zeitraum)
        except (sqlite3.Error, OSError, ValueError):
            gr.append("Zuwachs-Statistik nicht lesbar – Zuwachs und Schreibratensprung nicht berechnet.")
            return
        if wo is None:
            gr.append("Zuwachs-Statistik hat für den Zeitraum keine gemessenen Tage – Zuwachs und Schreibratensprung nicht berechnet.")
            return
        out["wirkung"]["zuwachs_mb_woche"] = round(wo / MB, 1)
        if n_jetzt < zeitraum:
            gr.append(f"Zuwachs nur aus {n_jetzt} von {zeitraum} Tagen mit Messung berechnet.")
        if not n_davor:
            gr.append("Schreibratensprung nicht berechnet (Zeitraum davor nicht gemessen).")
            return
        sp = [{"entity_id": eid, "faktor": round(a / davor[eid], 1), "zeilen_tag": round(a)} for eid, a in aktuell.items()
              if a >= MIN_ZEILEN_TAG and davor.get(eid, 0) > 0 and a / davor[eid] >= s["schreibrate_faktor"]]
        sp.sort(key=lambda x: -x["zeilen_tag"])
        out["drift"]["schreibrate_sprung_anzahl"], out["drift"]["schreibrate_sprung"] = len(sp), sp
        if sp:
            gelb.append(f"{len(sp)} Entität(en) schreiben mehr als {s['schreibrate_faktor']:g}-mal so oft wie im Zeitraum davor.")

    def prognose():
        w = out["wirkung"]
        if pl is not None:
            w["zuwachs_prognose_mb_woche"] = round(_prognose_zufluss(pl) / MB, 1)
            ist, soll = w["zuwachs_mb_woche"], w["zuwachs_prognose_mb_woche"]
            if ist is not None and soll and ist > soll * s["zuwachs_prognose_pct"] / 100:
                gelb.append(f"Der Zuwachs liegt bei {round(100 * ist / soll)} % der Prognose (Schwelle {s['zuwachs_prognose_pct']} %).")
        db_b, pb = e.get("db_bytes"), e.get("prognose_bytes")
        w["db_gb"] = _zahl(db_b / GB, 2) if _zahl_ok(db_b) else None
        w["prognose_gb"] = _zahl(pb / GB, 2) if _zahl_ok(pb) else None
        pt = e.get("prognose_tage")
        w["prognose_tage"] = int(pt) if _zahl_ok(pt) and pt > 0 else None
        pk = ((pl or {}).get("stand") or {}).get("purge_keep_days")
        w["purge_keep_days"] = int(pk) if _zahl_ok(pk) and pk > 0 else None
        lo = e.get("lts_ohne")
        out["drift"]["lts_ohne_entitaet"] = lo if isinstance(lo, int) and not isinstance(lo, bool) else None

    def laeufe():
        roh = e.get("laeufe")
        if not isinstance(roh, list):
            gr.append("Keine Löschläufe übergeben – Läufe und Löschmenge nicht berechnet.")
            return
        alle = [p for p in roh if isinstance(p, dict) and _zahl_ok(p.get("start"))]
        von_tag = heute - zeitraum + 1
        grenze = datetime.datetime.combine(datetime.date(1970, 1, 1) + datetime.timedelta(days=von_tag), datetime.time.min).timestamp()
        im = [p for p in alle if p["start"] >= grenze]
        zaehlt = [p for p in im if p.get("status") in STATUS_OK + STATUS_FEHLER + STATUS_ABBRUCH]
        tage_mit = {_tag(p["start"]) for p in zaehlt}
        bis = heute + 1 if datetime.datetime.fromtimestamp(jetzt).hour >= NACHT_ENDE_STUNDE else heute
        lf = out["laeufe"]
        lf.update(gesamt=len(im), ok=sum(p.get("status") in STATUS_OK for p in im),
                  fehler=sum(p.get("status") in STATUS_FEHLER for p in im),
                  abgebrochen=sum(p.get("status") in STATUS_ABBRUCH for p in im),
                  naechte_ohne_lauf=sum(1 for d in range(von_tag, bis) if d not in tage_mit))
        letzter = max(alle, key=lambda p: p["start"], default=None)
        if letzter:
            lf["letzter"] = {"start": letzter["start"], "ende": letzter.get("ende"), "status": letzter.get("status"),
                             "geloescht": letzter.get("geloescht")}
        if lf["fehler"]:
            rot.append(f"{lf['fehler']} Löschlauf/-läufe mit Fehler im Zeitraum.")
        je = _entfernt_je_entitaet(im)
        gesamt = sum(je.values())
        out["wirkung"]["geloescht_zeilen"] = gesamt
        zu_regel = {x["entity_id"]: ((x.get("werte") or {}).get("kurzzeit") or {}).get("regel")
                    for x in (pl or {}).get("entitaeten", []) if isinstance(x, dict) and x.get("entity_id")}
        je_r = {}
        for eid, n in je.items():
            je_r[zu_regel.get(eid) or OHNE_ZUORDNUNG] = je_r.get(zu_regel.get(eid) or OHNE_ZUORDNUNG, 0) + n
        liste, gelistet = [], 0
        for r in (pl or {}).get("regeln") or []:
            if not isinstance(r, dict) or r.get("regel") is None:
                continue
            n = je_r.pop(r["regel"], 0)
            gelistet += n
            if r.get("entitaeten") or n:
                liste.append({"regel": r["regel"], "name": r.get("name") or r["regel"], "treffer": r.get("entitaeten") or 0,
                              "geloescht_zeilen": n, "ersparnis_mb": round((r.get("ersparnis_bytes") or 0) / MB, 1)})
        liste.sort(key=lambda x: -x["ersparnis_mb"])
        if gesamt - gelistet > 0:
            liste.append({"regel": OHNE_ZUORDNUNG, "name": "ohne Zuordnung", "treffer": 0, "geloescht_zeilen": gesamt - gelistet,
                          "ersparnis_mb": 0.0})
        out["wirkung"]["je_regel"] = liste

    def regelcheck():
        rc = e.get("regelcheck")
        if not isinstance(rc, dict):
            gr.append("Regelcheck nicht verfügbar.")
            return
        out["regelcheck"] = {k: rc.get(k) for k in ("rot", "gelb", "grau", "tot")}
        if _zahl_ok(rc.get("rot")) and rc["rot"] >= s["regelcheck_rot"] > 0:
            rot.append(f"Regelcheck meldet {rc['rot']} Befund(e) der Stufe rot.")

    def empfehlungen():
        vs = e.get("vorschlaege")
        if not isinstance(vs, list):
            gr.append("Empfehlungen nicht verfügbar.")
            return
        emp = []
        for v in sorted((v for v in vs if isinstance(v, dict) and not v.get("ignoriert")), key=lambda v: -(v.get("ersparnis_bytes") or 0)):
            n = v.get("anzahl") or 0
            art = v.get("art") if v.get("art") in ART_TEXT else None
            text = str(v.get("titel") or "").strip() if ids and v.get("titel") else ART_TEXT.get(art, "Neue Regel")
            emp.append({"text": text, "art": art, "entitaeten": n, "ersparnis_mb": round((v.get("ersparnis_bytes") or 0) / MB, 1)})
        out["empfehlungen"] = emp[:top_n]

    if pl is None:
        gr.append("Kein Probelauf verfügbar – Abdeckung, Wirkung und Ampel nicht berechnet.")
    for name, fn in (("Abdeckung", abdeckung if pl is not None else None), ("Tote Muster", tote), ("Regeln ohne Treffer", ohne_treffer),
                     ("Umbenennungen", umbenennung), ("Zuwachs", zuwachs), ("Prognose", prognose), ("Läufe", laeufe),
                     ("Regelcheck", regelcheck), ("Empfehlungen", empfehlungen)):
        if fn is None:
            continue
        try:
            fn()
        except Exception:  # noqa: BLE001 – ein defekter Block darf den Bericht nicht verhindern
            gr.append(f"{name}: Berechnung fehlgeschlagen (Eingabedaten unbrauchbar).")

    out["ampel"] = None if pl is None else "rot" if rot else "gelb" if gelb else "gruen"
    out["ampel_gruende"] = rot + gelb + gr
    if not ids:
        # Regel-ID und Name: Regeln vom Typ E: heißen nach ihrer Entität (E:sensor.x) – dann laufende Nummer statt ID
        for i, r in enumerate(out["wirkung"]["je_regel"], 1):
            if not _unkritisch(r["regel"], kritisch):
                r["regel"] = f"Regel {i}"
            if not _unkritisch(r["name"], kritisch):
                r["name"] = str(r["regel"])
        a, d, w = out["abdeckung"], out["drift"], out["wirkung"]
        a["neu_ohne_regel_top"], d["umbenennung_verdacht"], d["schreibrate_sprung"], w["top_ohne_regel"] = [], [], [], []
    return out
