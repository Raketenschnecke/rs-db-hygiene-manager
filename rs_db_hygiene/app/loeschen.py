"""Löschlauf Stufe A (0.2.0): Kurzzeit-States per recorder.purge_entities.

Die App schreibt nicht selbst in die Datenbank. Sie ruft den HA-Dienst auf; HA löscht in der Warteschlange des
Recorders paketweise und räumt dabei auch die nicht mehr benutzten Attributzeilen ab. Die App zählt lesend vorher
und nachher (Sammelliste 32–37).

Sicherungen:
  - Hauptschalter loeschen_freigegeben (App-Konfiguration), sonst kein Aufruf
  - nur aktive Regeln (der Plan kommt aus dem Probelauf ohne passive Regeln), Zähler-Schutz wie im Probelauf
  - nur Entitäten, deren Kurzzeit kürzer als purge_keep_days ist; Untergrenze 1 Tag, höchstens 7300 Tage (20 Jahre)
  - Obergrenze Zeilen pro Lauf; kleinste Mengen zuerst, Rest im nächsten Lauf
  - immer eine explizite Entitätsliste: Ein Aufruf ohne Entitäten würde in HA ALLE Entitäten treffen
  - vor jedem Paket wird lesend nachgezählt; die Obergrenze gilt für die tatsächlich gezählten Zeilen
"""
import bisect
import datetime
import fnmatch
import hashlib
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request

import regeln as rg

_LOG = logging.getLogger("loeschen")

TAG = 86400
# Punkt 79 (0.4.2): 20 Jahre. recorder.purge_entities nimmt keep_days ohne Obergrenze an (cv.positive_int;
# Test am 10.10.2026: 366, 7300 und 7301 → HTTP 200). Bis 0.4.1 war hier 365.
MAX_KEEP_DAYS = 7300
MIN_KEEP_DAYS = 1
PAKET = 100              # Entitäten je Dienstaufruf
POLL_S = 10
RECORDER_WARTE_S = 60        # Punkt 261: so lange darf ein Auftrag dauern, ohne dass „Recorder beschäftigt“ im Protokoll steht
STILLSTAND_S = 600       # so lange darf die Zahl der alten Zeilen unverändert bleiben
PAKET_MAX_S = 3600
ANALYSE_MAX_ALTER_S = 26 * 3600
ANALYSE_FRISCH_S = 6 * 3600     # Punkt 83: Läufe aus HA mit älterer Analyse analysieren vorher neu
LOG_TAKT_S = 60                 # Punkt 109: Fortschritt im App-Log
BACKUP_MAX_S = 2 * 3600         # Punkt 110: so lange wartet ein Lauf höchstens auf ein Backup
BACKUP_PRUEF_S = 60             # Punkt 110: ab so viel Stillstand wird nach einem Backup gefragt


def mitternacht(tage, jetzt=None):
    """Punkt 121 (0.4.12): Beginn (0:00 Ortszeit) des Kalendertags „heute − tage“. Vorhaltung N Tage = der laufende Tag
    plus N volle Kalendertage; alles vor mitternacht(N) wird gelöscht. tage 0 = alles vor heute 0:00."""
    tag = datetime.date.fromtimestamp(time.time() if jetzt is None else jetzt) - datetime.timedelta(days=int(tage))
    return datetime.datetime(tag.year, tag.month, tag.day).timestamp()


def _z(n):
    """Zahl mit Tausenderpunkt (Log)."""
    return f"{int(n or 0):,}".replace(",", ".")


def _dauer(s):
    s = int(s or 0)
    return f"{s // 60}:{s % 60:02d} min" if s >= 60 else f"{s} s"


_BACKUP = {"zeit": 0.0, "wert": (False, ""), "entity": None}


def _backup_entity(config="/homeassistant"):
    """Entity-ID des Backup-Manager-Zustands (Plattform backup) aus der Registry – sprachunabhängig."""
    if _BACKUP["entity"] is None:
        _BACKUP["entity"] = ""
        try:
            with open(os.path.join(config, ".storage", "core.entity_registry"), encoding="utf-8") as f:
                for e in json.load(f).get("data", {}).get("entities", []):
                    if e.get("platform") == "backup" and "manager_state" in str(e.get("unique_id", "")):
                        _BACKUP["entity"] = e["entity_id"]
                        break
        except (OSError, ValueError):
            pass
    return _BACKUP["entity"]


def backup_laeuft(token, cache_s=20):
    """Punkt 110 (0.4.9): Läuft gerade ein Backup? Liefert (ja/nein, Beschreibung); 20 s zwischengespeichert.

    1. Supervisor-Jobs (/jobs/info): ein noch nicht beendeter Job mit „backup“ im Namen (auch Teil-Jobs).
    2. Zustand des Backup-Managers von HA (create_backup, receive_backup, restore_backup).
    Fehler beim Abfragen gelten als „kein Backup“ – das Verhalten ist dann wie vor 0.4.9."""
    if time.time() - _BACKUP["zeit"] < cache_s:
        return _BACKUP["wert"]
    erg = (False, "")
    kopf = {"Authorization": f"Bearer {token}"}
    try:
        with urllib.request.urlopen(urllib.request.Request("http://supervisor/jobs/info", headers=kopf), timeout=10) as r:
            jobs = (json.load(r).get("data") or {}).get("jobs") or []

        def offen(liste):
            for j in liste:
                if not j.get("done") and "backup" in str(j.get("name", "")).lower():
                    yield j
                yield from offen(j.get("child_jobs") or [])
        j = next(offen(jobs), None)
        if j:
            erg = (True, f"Supervisor-Job {j.get('name')}")
    except Exception:  # noqa: BLE001
        pass
    if not erg[0] and _backup_entity():
        try:
            url = f"http://supervisor/core/api/states/{_backup_entity()}"
            with urllib.request.urlopen(urllib.request.Request(url, headers=kopf), timeout=10) as r:
                zustand = json.load(r).get("state")
            if zustand in ("create_backup", "receive_backup", "restore_backup"):
                erg = (True, f"Backup-Manager: {zustand}")
        except Exception:  # noqa: BLE001
            pass
    _BACKUP.update(zeit=time.time(), wert=erg)
    return erg
DIENST_URL = "http://supervisor/core/api/services/recorder/purge_entities"
JOBNAMEN = {"analyse": "Analyse", "attribute": "Attribut-Analyse", "beide": "Analyse und Attribut-Analyse", "loeschen": "Löschlauf"}


class LoeschFehler(Exception):
    pass


# ---------------------------------------------------------------- Plan

def _histogramme(cache_pfad):
    con = sqlite3.connect(f"file:{cache_pfad}?mode=ro", uri=True)
    info = dict(con.execute("SELECT schluessel, wert FROM info"))
    mid_von = dict(con.execute("SELECT entity_id, metadata_id FROM entitaet"))
    h = {}
    for mid, tag, n in con.execute("SELECT metadata_id, tag, n FROM st_tage"):
        h.setdefault(mid, []).append((tag, n))
    # Punkt 230: 5-Min-Statistik je Tag – für den an die States gekoppelten 5-Min-Auftrag
    lid_von = dict(con.execute("SELECT entity_id, lts_id FROM entitaet"))
    hk = {}
    for lid, tag, n in con.execute("SELECT lts_id, tag, n FROM kurz_tage"):
        hk.setdefault(lid, []).append((tag, n))
    con.close()
    return int(info["heute_tag"]), mid_von, h, lid_von, hk


def _alt_zaehler(h, heute):
    """Zeilen älter als d Tage aus dem Tageshistogramm, per Präfixsumme (die Suche läuft bis zu 7300 Tage).

    Liefert (alt, aeltester_tag); alt(d) = Summe n aller Tage mit tag < heute - d."""
    h = sorted(h)
    tage = [t for t, _n in h]
    kum = [0]
    for _t, n in h:
        kum.append(kum[-1] + n)
    return (lambda d: kum[bisect.bisect_left(tage, heute - d)]), (tage[0] if tage else heute)


def plan(pl, cache_pfad, grenze, nur_regel=None, stufe_b=True, ausnahme=False, grenze_b=None, exakt=False):
    """Was ein Lauf jetzt löschen würde – aus Probelauf (ohne passive Regeln) und Tageshistogrammen des Caches.

    Die Zeilenzahlen sind Schätzungen auf Tagesbasis; der Lauf zählt vor jedem Paket genau nach.
    grenze gilt für die States, grenze_b (ab 0.4.3, Punkt 82) getrennt für Stufe B; ohne grenze_b gilt dieselbe Zahl.
    exakt (ab 0.4.12, Punkt 121): Integration mit purge_states – gelöscht wird bis zur lokalen Mitternacht, Kurzzeit
    „keine“ bis heute 0:00 (keep_days 0). Sonst wie bisher „jetzt minus keep_days“ mit Untergrenze 1 Tag.
    """
    grenze_b = grenze if grenze_b is None else grenze_b
    min_tage = 0 if exakt else MIN_KEEP_DAYS
    pkd = int(pl["stand"]["purge_keep_days"])
    heute, mid_von, hist, lid_von, hist_kurz = _histogramme(cache_pfad)
    kandidaten, regeln, wartend = [], {}, {}
    for e in pl["entitaeten"]:
        if e.get("schutz"):                  # Punkt 111: geschützt – nie im Plan (zusätzlich zu global/unbegrenzt)
            continue
        w = (e.get("werte") or {}).get("kurzzeit") or {}
        rid = w.get("regel")
        if not rid or (nur_regel and rid != nur_regel):
            continue
        k = w.get("tage")
        if k is None or k >= pkd:
            continue                     # HA löscht ohnehin nach purge_keep_days
        keine = k < 1                    # Kurzzeit „keine“
        hinweis = "Untergrenze 1 Tag" if keine and not exakt else ""
        k = max(k, min_tage)
        if k > MAX_KEEP_DAYS:
            continue
        alt, aeltester = _alt_zaehler(hist.get(mid_von.get(e["entity_id"]), []), heute)
        if keine:     # Punkt 100: Kurzzeit „keine“ – Zeilen jünger als die Grenze (exakt: von heute) folgen im nächsten Lauf
            warte = alt(-1) - alt(k)
            if warte > 0:
                w_ = wartend.setdefault(rid, {"regel": rid, "zeilen": 0, "entitaeten": 0})
                w_["zeilen"] += warte
                w_["entitaeten"] += 1
        gesamt = alt(k)
        if gesamt <= 0:
            continue
        kandidaten.append({"entity_id": e["entity_id"], "regel": rid, "status": e["status"], "kurzzeit_tage": k,
                           "zeilen_gesamt": gesamt, "alt": alt, "hinweis": hinweis,
                           "kurz_wert": w.get("wert"),   # Punkt 242: Wert der Regel mit der vom User gewählten Einheit (z. B. „6m“)
                           # über den ältesten Tag hinaus ändert sich nichts mehr (begrenzt die Suche bei 20 Jahren)
                           "obergrenze_tage": min(pkd - 1, MAX_KEEP_DAYS, heute - aeltester),
                           # Punkt 63: Attributzeilen aus der Attribut-Analyse (bei „teilweise“ höchstens so viele)
                           "attr_zeilen_gesamt": (e.get("loeschbar") or {}).get("attr_zeilen")})
    kandidaten.sort(key=lambda c: (c["zeilen_gesamt"], c["entity_id"]))   # kleinste Mengen zuerst

    rest, eintraege = grenze, []
    for c in kandidaten:
        alt = c.pop("alt")
        if c["zeilen_gesamt"] <= rest:
            c.update(keep_days=c["kurzzeit_tage"], zeilen=c["zeilen_gesamt"], art="vollständig")
        else:
            # Teilweise: so viele alte Tage, wie in die Obergrenze passen; der Rest folgt im nächsten Lauf
            d = next((d for d in range(c["kurzzeit_tage"] + 1, min(c["obergrenze_tage"], MAX_KEEP_DAYS) + 1)
                      if 0 < alt(d) <= rest), None)
            if d is None:
                # Punkt 53 (1): Liegt schon der älteste Tag über der ganzen Obergrenze, passt die Entität nie hinein
                d_alt = next((d for d in range(min(c["obergrenze_tage"], MAX_KEEP_DAYS), c["kurzzeit_tage"], -1) if alt(d) > 0), None)
                if d_alt is not None and alt(d_alt) > grenze:
                    c["zu_gross"] = True
                    c["hinweis"] = (c["hinweis"] + " · " if c["hinweis"] else "") + \
                        f"ältester Tag {alt(d_alt):,} Zeilen > Obergrenze – loeschen_max_zeilen erhöhen oder einmalige Ausnahme".replace(",", ".")
                if c.get("zu_gross") and ausnahme:
                    c.update(keep_days=d_alt, zeilen=alt(d_alt), art="Ausnahme", ausnahme=True)
                else:
                    c.update(keep_days=None, zeilen=0, art="nächster Lauf")
            else:
                c.update(keep_days=d, zeilen=alt(d), art="teilweise")
        rest -= c["zeilen"]
        c.pop("obergrenze_tage")
        c["attr_zeilen"] = c["attr_zeilen_gesamt"] if c["keep_days"] is not None else 0   # 0 = „keine“ (exakt)
        eintraege.append(c)
        r = regeln.setdefault(c["regel"], {"regel": c["regel"], "entitaeten": 0, "zeilen": 0, "zeilen_gesamt": 0})
        r["entitaeten"] += 1
        r["zeilen"] += c["zeilen"]
        r["zeilen_gesamt"] += c["zeilen_gesamt"]

    gruppen = {}
    for c in eintraege:
        if c["keep_days"] is not None:
            g = gruppen.setdefault(c["keep_days"], {"keep_days": c["keep_days"], "entitaeten": [], "zeilen": 0})
            g["entitaeten"].append(c["entity_id"])
            g["zeilen"] += c["zeilen"]
    gruppen = sorted(gruppen.values(), key=lambda g: (g["zeilen"], g["keep_days"]))

    # Stufe B (0.3.0): 5-Min-Statistik, Langzeit und Events über die Mini-Integration.
    # Ab 0.4.3 (Punkt 82) mit eigener Obergrenze: Bis dahin bekam B nur den Rest der States-Grenze und kam bei großem
    # States-Rückstand nie an die Reihe (Nachtlauf 11.10.2026: 0,8 Mio. B-Zeilen „nächster Lauf“).
    auftraege_b = plan_b(pl, pkd, nur_regel, exakt)
    # Punkt 254: Hat eine Entität einen eigenen Attribut-Auftrag, gehören ihre Attributzeilen dazu und nicht zum States-Eintrag
    attr_namen = {a["name"] for a in auftraege_b if a["art"] == "attr"}
    for c in eintraege:
        if c["entity_id"] in attr_namen:
            c["attr_zeilen"] = 0
    # Punkt 230: Der 5-Min-Auftrag folgt dem States-Auftrag derselben Entität in diesem Lauf – gleiches keep_days (auch
    # bei Obergrenze „teilweise“ oder Ausnahme); wartet der States-Teil auf den nächsten Lauf, wartet auch die 5-Min-Statistik
    st_von = {c["entity_id"]: c for c in eintraege}
    for a in auftraege_b:
        c = st_von.get(a["name"]) if a["art"] == "kurz" else None
        if c is None or c["keep_days"] == a["keep_days"]:
            continue
        if c["keep_days"] is None:
            a.update(wartet=True, gekoppelt=True)      # bleibt fällig (zeilen), wird in diesem Lauf nicht ausgeführt
        else:
            alt_k, _a = _alt_zaehler(hist_kurz.get(lid_von.get(a["name"]), []), heute)
            a.update(keep_days=c["keep_days"], zeilen=min(a["zeilen"], alt_k(c["keep_days"])), gekoppelt=True)
    rest = grenze_b
    for a in sorted(auftraege_b, key=lambda a: (a["zeilen"], a["name"])):
        if not stufe_b:      # Punkt 52: ohne Integration nicht einplanen und nicht mitzählen
            a["art_lauf"] = "übersprungen"
        elif a.get("wartet"):    # Punkt 230: States derselben Entität folgen erst im nächsten Lauf
            a["art_lauf"] = "nächster Lauf"
        elif a["zeilen"] <= rest:
            a["art_lauf"] = "vollständig"
        elif rest > 0:
            a["art_lauf"] = "teilweise"
        else:
            a["art_lauf"] = "nächster Lauf"
        # Punkt 75 (0.4.4): was davon in diesem Lauf gelöscht wird (bei „teilweise“ der Rest der Obergrenze)
        a["zeilen_lauf"] = (a["zeilen"] if a["art_lauf"] == "vollständig" else min(rest, a["zeilen"]) if a["art_lauf"] == "teilweise" else 0)
        if stufe_b:
            rest -= a["zeilen_lauf"]
        r = regeln.setdefault(a["regel"], {"regel": a["regel"], "entitaeten": 0, "zeilen": 0, "zeilen_gesamt": 0})
        r["zeilen"] += a["zeilen_lauf"]
        r["zeilen_gesamt"] += a["zeilen"]
    im_lauf_b = [a for a in auftraege_b if a["art_lauf"] not in ("nächster Lauf", "übersprungen")]

    kennung = hashlib.sha1(json.dumps([bool(ausnahme), bool(exakt), [[g["keep_days"], sorted(g["entitaeten"])] for g in gruppen],
                                       sorted([a["art"], a["name"], a["keep_days"], a["alle"]] for a in im_lauf_b)],
                                      default=str).encode()).hexdigest()[:12]
    # Punkt 256: Attribut-Aufträge zählen Attributzeilen, die abgetrennt (nicht gelöscht) werden – nicht in geplant/fällig/Läufe
    zeilen_b = sum(a["zeilen"] for a in auftraege_b if a["art"] != "attr")
    attr_lauf = [a for a in im_lauf_b if a["art"] == "attr"]
    # Punkt 199 (0.5.5b10): je Eintrag die überstimmten Regeln in Rangfolge (Kurzzeit, Langzeit bzw. Events) – für den
    # Tooltip „R12 gewinnt vor R30 …“ in der Löschtabelle; nur Anzeige, der Lauf nutzt es nicht
    namen = {c["entity_id"] for c in eintraege} | {a["name"] for a in auftraege_b}
    ketten = {}
    for e in pl["entitaeten"]:
        if e["entity_id"] in namen:
            w = e.get("werte") or {}
            # Punkt 225 (0.5.5b14): mit dem Wert der unterlegenen Regel („R07 hat Vorrang vor: HA:standard – 90 Tage“)
            ketten[e["entity_id"]] = {a: [{"regel": u["regel"], "anzeige": u.get("anzeige")} for u in (w.get(a) or {}).get("ueberstimmt") or []]
                                      for a in ("kurzzeit", "langzeit")}
    for ev in pl.get("events") or []:
        if ev["event_type"] in namen:
            ketten[ev["event_type"]] = {"events": [{"regel": u["regel"], "anzeige": u.get("anzeige") or rg.anzeige(u.get("wert"))}
                                                   for u in ev.get("ueberstimmt") or []]}
    return {
        "ketten": ketten,
        "analyse_zeit": pl["stand"]["analyse_zeit"], "purge_keep_days": pkd, "grenze": grenze, "grenze_b": grenze_b,
        "nur_regel": nur_regel, "ausnahme": bool(ausnahme),
        "kennung": kennung, "gruppen": gruppen, "entitaeten": eintraege, "auftraege_b": auftraege_b,
        "wartet_untergrenze": sorted(wartend.values(), key=lambda w: w["regel"]),   # Punkt 100
        "regeln": sorted(regeln.values(), key=lambda r: r["regel"]),
        "stufe_b": stufe_b,
        "exakt": bool(exakt),     # Punkt 121: Grenze lokale Mitternacht (Integration mit purge_states)
        "summe": {"zeilen": sum(c["zeilen"] for c in eintraege) + sum(a["zeilen_lauf"] for a in auftraege_b if a["art"] != "attr"),
                  # Punkt 256: Attribut-Aufträge dieses Laufs (Attributzeilen und Entitäten) – getrennt, nicht in „zeilen“
                  "attr_getrennt": sum(a["zeilen_lauf"] for a in attr_lauf), "attr_getrennt_entitaeten": len(attr_lauf),
                  # Punkt 75: je Tabelle nur, was in diesem Lauf gelöscht wird; zeilen_b = alles Fällige (auch „nächster Lauf“)
                  "zeilen_b_lauf": {art: sum(a["zeilen_lauf"] for a in auftraege_b if a["art"] == art) for art in ART_B},
                  "zeilen_states": sum(c["zeilen"] for c in eintraege),
                  "attr_zeilen": sum(c["attr_zeilen"] or 0 for c in eintraege),
                  "attr_bekannt": any(c["attr_zeilen_gesamt"] is not None for c in eintraege),
                  "zeilen_gesamt": sum(c["zeilen_gesamt"] for c in eintraege) + zeilen_b,
                  "zeilen_b": {art: sum(a["zeilen"] for a in auftraege_b if a["art"] == art) for art in ART_B},
                  # Punkt 200 (0.5.5b12): alles Fällige je Obergrenze und wie viele Läufe es ungefähr braucht
                  "zeilen_states_gesamt": sum(c["zeilen_gesamt"] for c in eintraege),
                  "laeufe": laeufe_noetig(sum(c["zeilen_gesamt"] for c in eintraege), zeilen_b if stufe_b else 0, grenze, grenze_b),
                  "entitaeten": sum(1 for c in eintraege if c["keep_days"] is not None),
                  "verschoben": sum(1 for c in eintraege if c["keep_days"] is None) + (len(auftraege_b) - len(im_lauf_b) if stufe_b else 0),
                  "uebersprungen": 0 if stufe_b else len(auftraege_b),
                  "zu_gross": sum(1 for c in eintraege if c.get("zu_gross")),
                  "ausnahmen": sum(1 for c in eintraege if c.get("ausnahme")),
                  "auftraege_b": len(im_lauf_b),
                  "aufrufe": sum(-(-len(g["entitaeten"]) // PAKET) for g in gruppen)},
    }


def laeufe_noetig(states, b, grenze, grenze_b):
    """Punkt 200: Läufe, bis alles Fällige gelöscht ist – je Obergrenze aufgerundet, die längere zählt. Schätzung:
    neue Zeilen kommen jeden Tag dazu, und Entitäten, die nicht ganz in den Rest der Obergrenze passen, warten."""
    def n(z, g):
        return -(-z // g) if z > 0 and g else 0
    return max(n(states, grenze), n(b, grenze_b if grenze_b is not None else grenze))


# Stufe B: Art im Plan → Tabelle der Mini-Integration
ART_B = {"kurz": "statistics_short_term", "lts": "statistics", "events": "events", "attr": "states_attribute"}
ART_NAME = {"states": "States", "kurz": "5-Min-Statistik", "lts": "Langzeit", "events": "Events", "attr": "Attribute"}
ATTR_STANDARD_ID = "A:standard"     # Punkt 254: Attribut-Standard hat keine Regel-ID, im Plan erscheint er unter diesem Namen
MAX_KEEP_DAYS_B = MAX_KEEP_DAYS     # Punkt 79: wie die States 20 Jahre (bis 0.4.1: 3650)


def plan_b(pl, pkd, nur_regel=None, exakt=False):
    """Aufträge für 5-Min-Statistik, Langzeit (je statistic_id = entity_id) und Events (je Event-Typ).

    - 5-Min-Statistik folgt der Kurzzeit; nur wenn sie kürzer als purge_keep_days ist (sonst löscht HA selbst). In plan()
      danach an das keep_days des States-Auftrags derselben Entität gekoppelt (Punkt 230).
    - Langzeit: Tage aus der Regel; „keine“ = alle Zeilen (bei Verwaisten zusätzlich statistics_meta).
    - Events: Vorhaltezeit je Typ, nur wenn kürzer als purge_keep_days.
    Zeilen = Löschmenge sofort aus dem Probelauf (Tagesbasis).
    exakt (Punkt 121): Kurzzeit „keine“ = keep_days 0 (bis heute 0:00), sonst Untergrenze 1 Tag.
    """
    min_tage = 0 if exakt else MIN_KEEP_DAYS
    out = []
    for e in pl["entitaeten"]:
        werte, lo_ = e.get("werte") or {}, e.get("loeschbar") or {}
        # Punkt 254: Attribute früher als die States abtrennen (Attribut-Wert kürzer als die Kurzzeit) – auch bei Schutz-Entitäten,
        # deren Schutz die Attribute nicht umfasst; die States bleiben, nur der Verweis auf die Attribute fällt weg
        at, kz = werte.get("attribute") or {}, werte.get("kurzzeit") or {}
        if (at.get("tage") is not None and kz.get("tage") is not None and 0 < at["tage"] < kz["tage"] and at["tage"] < pkd
                and lo_.get("attr_zeilen") and at["tage"] <= MAX_KEEP_DAYS_B):
            rid = at.get("regel") or ATTR_STANDARD_ID
            if not nur_regel or nur_regel == rid:
                out.append({"art": "attr", "name": e["entity_id"], "regel": rid, "status": e["status"], "wert": at.get("wert"),
                            "keep_days": at["tage"], "alle": False, "mit_meta": False, "zeilen": lo_["attr_zeilen"],
                            "hinweis": "Attribute getrennt – States bleiben"})
        if e.get("schutz"):                  # Punkt 111
            continue
        if not e.get("lts_rows") and not e.get("kurz_rows"):
            continue
        k = werte.get("kurzzeit") or {}
        if k.get("regel") and (not nur_regel or k["regel"] == nur_regel) and (k.get("tage") or 0) < pkd and lo_.get("kurz"):
            out.append({"art": "kurz", "name": e["entity_id"], "regel": k["regel"], "status": e["status"], "wert": k.get("wert"),
                        "keep_days": max(min_tage, k.get("tage") or 0), "alle": False, "mit_meta": False,
                        "zeilen": lo_["kurz"], "hinweis": "Untergrenze 1 Tag" if not k.get("tage") and not exakt else ""})
        lz = werte.get("langzeit") or {}
        if lz.get("regel") and (not nur_regel or lz["regel"] == nur_regel) and lz.get("tage") is not None and lo_.get("lts"):
            alle = lz["tage"] == 0
            if not alle and lz["tage"] > MAX_KEEP_DAYS_B:
                continue
            out.append({"art": "lts", "name": e["entity_id"], "regel": lz["regel"], "status": e["status"], "wert": lz.get("wert"),
                        "keep_days": None if alle else lz["tage"], "alle": alle, "mit_meta": alle and rg.ist_verwaist(e["status"]),   # Punkt 186: Deaktivierte behalten ihren Statistik-Eintrag
                        "zeilen": lo_["lts"], "hinweis": ("Langzeit „keine“ – samt statistics_meta" if alle and rg.ist_verwaist(e["status"])
                                                          else "Langzeit „keine“ – alle Stundenwerte" if alle else "")})
    for ev in pl.get("events") or []:
        if ev["wert"] == "global" or ev["tage"] >= pkd or not ev["loeschbar"]:
            continue
        regel = ev.get("regel") or "Events"      # ab 0.3.1 die Event-Regel (EV:…)
        if nur_regel and nur_regel != regel:
            continue
        out.append({"art": "events", "name": ev["event_type"], "regel": regel, "status": "", "wert": ev.get("wert"),
                    "keep_days": max(min_tage, ev["tage"]), "alle": False, "mit_meta": False,
                    "zeilen": ev["loeschbar"], "hinweis": ""})
    return out


def analyse_veraltet(analyse_zeit, plan_pkd, aktuell_pkd, max_alter_s=None, exakt=False, jetzt=None):
    """Punkt 83: Grund, warum der Plan nicht mehr zum Stand passt (leer = passt).

    plan_pkd: purge_keep_days der Analyse; aktuell_pkd: Wert jetzt (App-Option bzw. configuration.yaml).
    exakt (Punkt 126, 0.5.3): Grenze lokale Mitternacht – stammt die Analyse von einem früheren Kalendertag, rechnet der
    Plan mit dem Vortag (Entitäten, die gestern schon bereinigt waren, stehen mit 0 Zeilen darin) und der Lauf löscht
    sie nicht mit. Dann gilt die Analyse als veraltet, auch wenn sie jünger als max_alter_s ist."""
    if not analyse_zeit:
        return "Es gibt noch keine Analyse."
    if aktuell_pkd and plan_pkd and int(aktuell_pkd) != int(plan_pkd):
        return (f"purge_keep_days hat sich seit der Analyse geändert ({int(plan_pkd):,} → {int(aktuell_pkd):,} Tage)".replace(",", ".")
                + " – der Plan beruht auf dem alten Wert.")
    jetzt = time.time() if jetzt is None else jetzt
    if exakt:
        tag = datetime.date.fromtimestamp(float(analyse_zeit))
        if tag != datetime.date.fromtimestamp(jetzt):
            return f"Die Analyse stammt vom {tag.strftime('%d.%m.%Y')} – für die Grenze lokale Mitternacht des heutigen Tages wird neu analysiert."
    alter = jetzt - float(analyse_zeit)
    if alter > (max_alter_s or ANALYSE_MAX_ALTER_S):
        return f"Die Analyse ist {alter / 3600:.0f} Stunden alt."
    return ""


def protokoll_nicht_gestartet(ordner, ausgeloest, gruende, p=None):
    """Punkt 83: Lauf aus HA, der nach der vorgeschalteten Analyse nicht starten durfte. Der Eintrag sorgt dafür, dass die
    Integration das Event rs_db_hygiene_lauf_beendet meldet – eine wartende Automation läuft sonst in ihr Zeitlimit."""
    os.makedirs(ordner, exist_ok=True)
    jetzt = time.time()
    prot = {"start": jetzt, "ende": jetzt, "status": "nicht gestartet", "ausgeloest": ausgeloest,
            "nur_regel": (p or {}).get("nur_regel"), "grenze": (p or {}).get("grenze"), "grenze_b": (p or {}).get("grenze_b"),
            "purge_keep_days": (p or {}).get("purge_keep_days"), "analyse_zeit": (p or {}).get("analyse_zeit"),
            "analyse_vorher": True, "kennung": (p or {}).get("kennung"), "geplant_zeilen": 0, "geloescht": 0,
            "pakete": [], "fehler": " ".join(gruende)}
    pfad = os.path.join(ordner, f"{int(jetzt)}.json")
    with open(pfad + ".tmp", "w", encoding="utf-8") as f:
        json.dump(prot, f, ensure_ascii=False)
    os.replace(pfad + ".tmp", pfad)
    return prot


def sperren(p, freigegeben, job, token, aktuell_pkd=None, analyse_pruefen=True):
    """Gründe, warum der Lauf jetzt nicht starten darf (leer = darf).

    analyse_pruefen=False (Läufe aus HA, ab 0.4.3): Alter und purge_keep_days der Analyse nicht sperren – die Automatik
    startet in diesem Fall vorher selbst eine Analyse (Punkt 83)."""
    s = []
    if not freigegeben:
        s.append("Hauptschalter aus: „Löschen freigegeben“ einschalten (Einstellungen → Apps → RS DB Hygiene Manager → Konfiguration); wirkt nach dem Speichern sofort, ohne Neustart der App.")
    if job:
        s.append(f"Es läuft bereits: {JOBNAMEN.get(job.get('art'), job.get('art'))}.")
    if analyse_pruefen and time.time() - float(p["analyse_zeit"]) > ANALYSE_MAX_ALTER_S:
        s.append("Die Analyse ist älter als 26 Stunden – bitte zuerst „Analyse starten“.")
    elif analyse_pruefen and aktuell_pkd and int(aktuell_pkd) != int(p["purge_keep_days"]):
        s.append(analyse_veraltet(p["analyse_zeit"], p["purge_keep_days"], aktuell_pkd) + " Bitte zuerst „Analyse starten“.")
    b_im_lauf = (p.get("summe") or {}).get("auftraege_b", 0) if p.get("stufe_b", True) else 0
    if not p["gruppen"] and not b_im_lauf:
        s.append("Nach den aktiven Regeln gibt es nichts zu löschen."
                 + ("" if p.get("stufe_b", True) or not (p.get("summe") or {}).get("auftraege_b")
                    else " Statistik und Events bräuchten die Integration RS DB Hygiene (Stufe B), die nicht aktiv ist."))
    if not token:
        s.append("Kein Zugang zur HA-API (SUPERVISOR_TOKEN fehlt).")
    return s


# ---------------------------------------------------------------- Lauf

def attr_cache_bereinigen(attr_pfad, cache_pfad, eintraege):
    """Punkt 260: erledigte Attributzeilen aus der Attribut-Analyse (attribute.db) nehmen, damit Plan und Löschmenge nach einem Lauf sofort
    stimmen. eintraege: [(entity_id, keep_days)] – gelöscht wurden Attributzeilen, die zuletzt vor heute-keep_days benutzt wurden
    (Tag der Analyse, wie im Probelauf). Liefert die Zahl entfernter Tageszeilen; Fehler sind nicht schlimm (die nächste Attribut-Analyse
    rechnet ohnehin neu)."""
    if not attr_pfad or not cache_pfad or not eintraege or not os.path.exists(attr_pfad):
        return 0
    try:
        cc = sqlite3.connect(f"file:{cache_pfad}?mode=ro", uri=True)
        heute = int(dict(cc.execute("SELECT schluessel, wert FROM info"))["heute_tag"])
        mid_von = dict(cc.execute("SELECT entity_id, metadata_id FROM entitaet"))
        cc.close()
        ac = sqlite3.connect(attr_pfad, timeout=30)
        n = 0
        for eid, tage in eintraege:
            mid = mid_von.get(eid)
            if mid is not None and tage is not None:
                n += ac.execute("DELETE FROM attr_tage WHERE metadata_id = ? AND art = 'letzt' AND tag < ?", (mid, heute - int(tage))).rowcount
        ac.commit()
        ac.close()
        return n
    except (sqlite3.Error, OSError, KeyError, ValueError) as e:
        _LOG.warning("Attribut-Cache nicht bereinigt (%s) – die nächste Attribut-Analyse erneuert ihn", e)
        return 0


def _db(db_pfad):
    return sqlite3.connect(f"file:{db_pfad}?mode=ro", uri=True, timeout=30)


def _metadata_ids(db_pfad, entity_ids):
    con = _db(db_pfad)
    try:
        frage = ",".join("?" * len(entity_ids))
        return dict(con.execute(f"SELECT entity_id, metadata_id FROM states_meta WHERE entity_id IN ({frage})", entity_ids))
    finally:
        con.close()


def _attr_kandidaten(db_pfad, mids, grenze_ts):
    """Punkt 63: Attribut-IDs der alten States je metadata_id (vor dem Löschen). HA entfernt danach die, die niemand
    mehr nutzt; wie viele das waren, ergibt der Vergleich mit _attr_vorhanden() nach dem Lauf."""
    if not mids:
        return {}
    con = _db(db_pfad)
    try:
        frage = ",".join("?" * len(mids))
        out = {}
        for m, a in con.execute(f"SELECT metadata_id, attributes_id FROM states WHERE metadata_id IN ({frage}) "
                                "AND last_updated_ts < ? AND attributes_id IS NOT NULL GROUP BY 1, 2", [*mids, grenze_ts]):
            out.setdefault(m, set()).add(a)
        return out
    finally:
        con.close()


def _attr_wegfallend(db_pfad, mid, grenze_ts, ids):
    """Punkt 267: Wie viele der Attributzeilen ids fallen nach dem Abtrennen wirklich weg? Eine Attributzeile bleibt, wenn irgendein State sie
    noch benutzt: ein neuerer State derselben Entität, der neueste State der Entität (behält seine Attribute) oder ein State einer
    anderen Entität (Attribute werden über ihren Hash geteilt). Nur lesend; EXISTS bricht je Attributzeile beim ersten Treffer ab."""
    ids = sorted(ids)
    if not ids:
        return 0
    con = _db(db_pfad)
    try:
        neueste = con.execute("SELECT MAX(state_id) FROM states WHERE metadata_id = ?", (mid,)).fetchone()[0]
        benutzt = 0
        for i in range(0, len(ids), 500):
            teil = ids[i:i + 500]
            benutzt += con.execute(
                f"SELECT COUNT(*) FROM state_attributes sa WHERE sa.attributes_id IN ({','.join('?' * len(teil))}) AND EXISTS "
                "(SELECT 1 FROM states s WHERE s.attributes_id = sa.attributes_id "
                "AND NOT (s.metadata_id = ? AND s.last_updated_ts < ? AND s.state_id <> ?))",
                [*teil, mid, grenze_ts, -1 if neueste is None else neueste]).fetchone()[0]
        return len(ids) - benutzt
    finally:
        con.close()


def _attr_vorhanden(db_pfad, ids):
    ids = sorted(ids)
    if not ids:
        return set()
    con = _db(db_pfad)
    try:
        da = set()
        for i in range(0, len(ids), 900):
            teil = ids[i:i + 900]
            da |= {a for (a,) in con.execute(f"SELECT attributes_id FROM state_attributes WHERE attributes_id IN ({','.join('?' * len(teil))})", teil)}
        return da
    finally:
        con.close()


def _attr_messen(db_pfad, kandidaten, eintrag, mid_von_eid):
    """Gelöschte Attributzeilen je Entität eintragen; liefert die Summe."""
    alle = set().union(*kandidaten.values()) if kandidaten else set()
    try:
        da = _attr_vorhanden(db_pfad, alle)
    except sqlite3.Error:
        return 0
    summe = 0
    for eid, m in mid_von_eid.items():
        k = kandidaten.get(m, set())
        n = len(k - da)
        if eid in eintrag["je_entitaet"]:
            eintrag["je_entitaet"][eid]["attr_geloescht"] = n
        summe += n
    eintrag["attr_geloescht"] = summe
    return summe


def _zaehle(db_pfad, mids, grenze_ts):
    """Zeilen älter als grenze_ts je metadata_id (Index metadata_id + last_updated_ts)."""
    if not mids:
        return {}
    con = _db(db_pfad)
    try:
        frage = ",".join("?" * len(mids))
        z = dict(con.execute(f"SELECT metadata_id, COUNT(*) FROM states WHERE metadata_id IN ({frage}) "
                             "AND last_updated_ts < ? GROUP BY metadata_id", [*mids, grenze_ts]))
    finally:
        con.close()
    return {m: z.get(m, 0) for m in mids}


def rufe_dienst(entity_ids, keep_days, token, url=DIENST_URL, vor_ts=None):
    """States löschen: recorder.purge_entities mit keep_days – oder ab 0.4.12 (Punkt 121) mit vor_ts über
    rs_db_hygiene.purge_states (HAs PurgeEntitiesTask mit exaktem Zeitpunkt, z. B. lokale Mitternacht)."""
    if not entity_ids:   # niemals ohne Entitäten: HA würde dann alle Entitäten löschen
        raise LoeschFehler("interner Fehler: Dienstaufruf ohne Entitäten verhindert")
    if not (0 if vor_ts is not None else MIN_KEEP_DAYS) <= keep_days <= MAX_KEEP_DAYS:
        raise LoeschFehler(f"interner Fehler: keep_days {keep_days} außerhalb {MIN_KEEP_DAYS}–{MAX_KEEP_DAYS}")
    if vor_ts is not None:
        if vor_ts > time.time():
            raise LoeschFehler("interner Fehler: Löschgrenze in der Zukunft verhindert")
        url = DIENST_EXAKT_URL if url == DIENST_URL else url
        body = json.dumps({"entity_id": list(entity_ids), "vor_ts": float(vor_ts)}).encode()
    else:
        body = json.dumps({"entity_id": list(entity_ids), "keep_days": int(keep_days)}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        if r.status >= 300:
            raise LoeschFehler(f"Dienstaufruf fehlgeschlagen: HTTP {r.status}")


DIENST_B_URL = "http://supervisor/core/api/services/rs_db_hygiene/purge?return_response"
DIENST_EXAKT_URL = "http://supervisor/core/api/services/rs_db_hygiene/purge_states"     # Punkt 121 (Integration ab 0.4.5)
B_PRO_AUFRUF = 100_000        # Zeilen je Aufruf der Mini-Integration (ca. 1 s Arbeit im Recorder)


def rufe_dienst_b(art, name, keep_days, alle, mit_meta, max_zeilen, token, url=DIENST_B_URL, vor_ts=None):
    """Ein Aufruf von rs_db_hygiene.purge für genau eine statistic_id bzw. einen Event-Typ; liefert die Antwort.
    Ab 0.4.12 (Punkt 121) mit vor_ts = exakte Grenze (lokale Mitternacht) statt keep_days."""
    if not name:
        raise LoeschFehler("interner Fehler: Aufruf der Integration ohne Namen verhindert")
    if not alle and not (0 if vor_ts is not None else MIN_KEEP_DAYS) <= int(keep_days) <= MAX_KEEP_DAYS_B:
        raise LoeschFehler(f"interner Fehler: keep_days {keep_days} außerhalb {MIN_KEEP_DAYS}–{MAX_KEEP_DAYS_B}")
    daten = {"tabelle": ART_B[art], "namen": [name], "max_zeilen": int(max_zeilen), "alle": bool(alle), "mit_meta": bool(mit_meta)}
    if not alle and vor_ts is not None:
        if vor_ts > time.time():
            raise LoeschFehler("interner Fehler: Löschgrenze in der Zukunft verhindert")
        daten["vor_ts"] = float(vor_ts)
    elif not alle:
        daten["keep_days"] = int(keep_days)
    req = urllib.request.Request(url, data=json.dumps(daten).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=960) as r:
        antwort = json.load(r)
    return antwort.get("service_response", antwort)


def integration_dienste(token, url="http://supervisor/core/api/services"):
    """Dienste der Integration rs_db_hygiene, die HA gerade anbietet (leer, wenn nicht installiert oder nicht abfragbar)."""
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return set().union(*[set(d.get("services") or {}) for d in json.load(r) if d.get("domain") == "rs_db_hygiene"])
    except Exception:  # noqa: BLE001 – dann eben nicht verfügbar
        return set()


def integration_da(token, url="http://supervisor/core/api/services"):
    """Ist der Dienst rs_db_hygiene.purge in HA registriert (Integration installiert und eingerichtet)?"""
    return "purge" in integration_dienste(token, url)


def vorhandene_entitaeten(token, url="http://supervisor/core/api/states"):
    """Punkt 84: entity_ids, die HA jetzt als Zustand kennt; None, wenn die Abfrage scheitert."""
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return {s["entity_id"] for s in json.load(r)}
    except Exception:  # noqa: BLE001 – der Lauf überspringt die Verwaisten dann vorsichtshalber
        _LOG.warning("Zustände aus HA nicht abrufbar – Verwaiste werden in diesem Paket übersprungen")
        return None


class Lauf:
    """Ein Löschlauf; schreibt nach jedem Paket sein Protokoll unter laeufe/<start>.json."""

    def __init__(self, p, db_pfad, ordner, token, ausgeloest, dienst=rufe_dienst, schlafe=time.sleep, dienst_b=rufe_dienst_b,
                 vorhanden=None, backup=None, uhr=time.time, attr_pfad=None, cache_pfad=None):
        self.attr_pfad, self.cache_pfad = attr_pfad, cache_pfad      # Punkt 260: Attribut-Cache nach dem Lauf bereinigen
        self.p, self.db, self.token, self.dienst, self.schlafe, self.dienst_b = p, db_pfad, token, dienst, schlafe, dienst_b
        self.backup = backup or (lambda: backup_laeuft(token))     # Punkt 110
        self.uhr = uhr
        # Punkt 121 (0.4.12): mit Integration ≥ 0.4.5 bis zur lokalen Mitternacht; Bezugstag = Start des Laufs
        # (ein Lauf über Mitternacht löscht dann nicht plötzlich einen Tag mehr, als geplant und gezählt war)
        self.exakt = bool(p.get("exakt"))
        self.bezug = time.time()
        alle_b = [a for a in p.get("auftraege_b", []) if a.get("art_lauf") not in ("nächster Lauf", "übersprungen")] if p.get("stufe_b") else []
        # Punkt 254: Attribute abtrennen läuft nach den States – was die States-Löschung schon mitnimmt, muss nicht erst umgeschrieben werden
        self.b_liste = [a for a in alle_b if a["art"] != "attr"]
        self.attr_liste = [a for a in alle_b if a["art"] == "attr"]
        self._attr_gel = 0         # Punkt 266: Attributzeilen, die frühere Attribut-Aufträge dieses Laufs entfernt haben (Fortschritt)
        self._attr_alt = None      # Punkt 256: erster „Integration zu alt“-Eintrag; weitere Attribut-Aufträge werden nur gezählt
        # Punkt 53 (1): bestätigte Ausnahmen dürfen die Obergrenze einmalig überschreiten (nur ihr ältester Tag)
        self.ausnahmen = {c["entity_id"] for c in p.get("entitaeten", []) if c.get("ausnahme")}
        # Punkt 84: Verwaiste des Plans; vor ihrem Paket wird geprüft, ob sie inzwischen wieder existieren
        self.verwaiste = {c["entity_id"] for c in p.get("entitaeten", []) if c.get("status", "aktiv") != "aktiv"}
        self.vorhanden = vorhanden or vorhandene_entitaeten
        self.abbruch = False
        self.start = time.time()
        os.makedirs(ordner, exist_ok=True)
        self.pfad = os.path.join(ordner, f"{int(self.start)}.json")
        self.prot = {"start": self.start, "ende": None, "status": "läuft", "ausgeloest": ausgeloest,
                     "nur_regel": p["nur_regel"], "grenze": p["grenze"], "grenze_b": p.get("grenze_b", p["grenze"]),
                     "purge_keep_days": p["purge_keep_days"],
                     # Punkt 83: auf welchem Analyse-Stand der Plan beruht
                     "analyse_zeit": p.get("analyse_zeit"), "analyse_vorher": bool(p.get("analyse_vorher")),
                     "kennung": p["kennung"], "geplant_zeilen": p["summe"]["zeilen"], "geloescht": 0,
                     # Punkt 243: ab hier zählt die Summe eines Laufs die Attributzeilen mit (ältere Läufe ohne dieses Feld bleiben unverändert)
                     "geplant_attr": (p["summe"].get("attr_zeilen") or 0) + (p["summe"].get("attr_getrennt") or 0),   # Punkt 256: auch die getrennten
                     # Punkt 242: Wert der Regel je Entität bzw. Event-Typ und Datenart (Einheit nach Wahl des Users) für „K 7T/L 1J“
                     "vorhaltung": self._vorhaltung(p),
                     # Punkt 200 (0.5.5b12): alles Fällige beim Start – Rest danach = fällig − gelöscht (je Obergrenze)
                     "faellig_states": p["summe"].get("zeilen_states_gesamt"),
                     "faellig_b": sum(v for k, v in (p["summe"].get("zeilen_b") or {}).items() if k != "attr") if p.get("stufe_b") else 0,   # Punkt 256: ohne Attribute
                     # Punkt 116 (0.4.10): Wartezeit auf HA-Backups (Sekunden, Anzahl Pausen), je Lauf und je Paket
                     "backup_warte_s": 0, "backup_pausen": 0,
                     # Punkt 121 (0.4.12): Grenze lokale Mitternacht (exakt) oder „jetzt minus keep_days“
                     "exakt": bool(p.get("exakt")),
                     "pakete": [], "fehler": None}
        self.fortschritt = {"paket": 0, "pakete": p["summe"]["aufrufe"] + len(self.b_liste) + len(self.attr_liste), "geloescht": 0, "grenze": p["grenze"],
                            "geplant": p["summe"]["zeilen"] + ((p["summe"].get("attr_getrennt") or 0) if p.get("stufe_b") else 0), "aktuell": ""}   # Punkt 266: inkl. Attributzeilen
        self._schreibe()

    def _schreibe(self):
        tmp = self.pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.prot, f, ensure_ascii=False)
        os.replace(tmp, self.pfad)

    def _log_fortschritt(self, vorher_summe, rest):
        """Punkt 109: eine Zeile Fortschritt (Paket, Anteil, Gesamt)."""
        f = self.fortschritt
        ziel = max(f.get("geplant") or 0, f.get("gezaehlt") or 0)
        weg = max(0, vorher_summe - rest)
        _LOG.info("Löschlauf %s · Paket %d/%d %s: %s von %s gelöscht (%d %%), noch %s · gesamt %s von ca. %s (%d %%)%s",
                  _dauer(time.time() - self.start), f["paket"], f["pakete"], f.get("aktuell", ""), _z(weg), _z(vorher_summe),
                  100 * weg // vorher_summe if vorher_summe else 100, _z(rest), _z(f["geloescht"]), _z(ziel),
                  100 * f["geloescht"] // ziel if ziel else 100, f" · {f['backup']}" if f.get("backup") else "")

    @staticmethod
    def _vorhaltung(p):
        """Punkt 242: {Entität bzw. Event-Typ: {states|kurz|lts|events: Regelwert wie „6m“}} – die Pakete kennen nur keep_days in Tagen,
        die Anzeige „K 7T/L 1J“ braucht die vom User gewählte Einheit der Regel."""
        v = {}
        for c in p.get("entitaeten") or []:
            if c.get("kurz_wert"):
                v.setdefault(c["entity_id"], {})["states"] = c["kurz_wert"]
        for a in p.get("auftraege_b") or []:
            if a.get("wert"):
                v.setdefault(a["name"], {})[a["art"]] = a["wert"]
        return v

    def _grenze(self, tage):
        """Löschgrenze für keep_days tage: lokale Mitternacht (exakt) oder wie bisher jetzt − tage × 24 h."""
        return mitternacht(tage, self.bezug) if self.exakt else time.time() - tage * TAG

    def _states_loeschen(self, entity_ids, tage, grenze_ts):
        if self.exakt:
            self.dienst(entity_ids, tage, self.token, vor_ts=grenze_ts)
        else:
            self.dienst(entity_ids, tage, self.token)

    def _backup_gewartet(self, sekunden):
        """Punkt 116 (0.4.10): eine Backup-Pause im Protokoll festhalten – im Lauf und im laufenden Paket."""
        s = max(0.0, float(sekunden))
        ziele = [self.prot] + ([self.prot["pakete"][-1]] if self.prot["pakete"] else [])
        for z in ziele:
            z["backup_warte_s"] = round((z.get("backup_warte_s") or 0) + s, 1)
            z["backup_pausen"] = (z.get("backup_pausen") or 0) + 1
        self.fortschritt.pop("backup", None)
        return s

    def _warte(self, mids, grenze_ts, vorher):
        """Bis HA die alten Zeilen gelöscht hat (0), sich nichts mehr tut oder die Zeit abläuft.

        Ab 0.4.9: Stockt es, weil ein Backup die Datenbank sperrt, wird gewartet statt „Stillstand“ gemeldet (Punkt 110,
        höchstens BACKUP_MAX_S); alle LOG_TAKT_S eine Zeile Fortschritt im App-Log (Punkt 109).
        Ab 0.4.10 (Punkt 116): Die Pause zählt ab der letzten Bewegung vor dem Erkennen (das Backup sperrt schon, bevor
        es nach BACKUP_PRUEF_S erkannt wird) und steht mit Dauer im Protokoll."""
        beginn = zuletzt_bewegt = zuletzt_log = self.uhr()
        stand, summe_vorher = sum(vorher.values()), sum(vorher.values())
        backup_seit = None

        def backup_ende(jetzt):
            return self._backup_gewartet(jetzt - backup_seit) if backup_seit is not None else 0

        while True:
            self.schlafe(POLL_S)
            z = _zaehle(self.db, mids, grenze_ts)
            neu = sum(z.values())
            self.fortschritt["aktuell_rest"] = neu
            # live mitzählen (Punkt 39): bisherige Pakete + in diesem Paket schon gelöscht
            self.fortschritt["geloescht"] = self.prot["geloescht"] + max(0, summe_vorher - neu)
            jetzt = self.uhr()
            if neu == 0:
                if backup_seit is not None:
                    _LOG.info("Löschlauf: Backup beendet nach %s, Paket fertig", _dauer(backup_ende(jetzt)))
                self.fortschritt.pop("backup", None)
                return z, "fertig"
            if neu < stand:
                stand, zuletzt_bewegt = neu, jetzt
                if backup_seit is not None:
                    _LOG.info("Löschlauf: Backup beendet nach %s, es wird weiter gelöscht", _dauer(backup_ende(jetzt)))
                    backup_seit = None
            elif jetzt - zuletzt_bewegt >= BACKUP_PRUEF_S:
                laeuft, was = self.backup()
                if laeuft:
                    if backup_seit is None:
                        backup_seit = zuletzt_bewegt      # Punkt 116: seit der letzten Bewegung gesperrt
                        _LOG.info("Löschlauf: Backup läuft (%s) – HA löscht erst danach weiter, es wird gewartet", was)
                    self.fortschritt["backup"] = f"wartet auf Backup seit {_dauer(jetzt - backup_seit)}"
                    if jetzt - backup_seit > BACKUP_MAX_S:
                        backup_ende(jetzt)
                        return z, f"Backup dauert länger als {_dauer(BACKUP_MAX_S)}"
                    zuletzt_bewegt = jetzt                # kein Stillstand, solange das Backup läuft
                    beginn += POLL_S                      # Wartezeit zählt nicht zur Höchstdauer des Pakets
            ergebnis = ("Stillstand" if jetzt - zuletzt_bewegt > STILLSTAND_S else "Zeit abgelaufen" if jetzt - beginn > PAKET_MAX_S
                        else "abgebrochen (HA löscht ggf. weiter)" if self.abbruch else None)
            if ergebnis:
                backup_ende(jetzt)
                return z, ergebnis
            if jetzt - zuletzt_log >= LOG_TAKT_S:
                zuletzt_log = jetzt
                self._log_fortschritt(summe_vorher, neu)

    def _warte_auf_backup(self, was_vor):
        """Punkt 110: vor einem Dienstaufruf warten, solange ein Backup läuft (höchstens BACKUP_MAX_S).
        Ab 0.4.10 steht die Pause im Protokoll (Punkt 116)."""
        laeuft, was = self.backup()
        if not laeuft:
            return
        seit = self.uhr()
        _LOG.info("Löschlauf: Backup läuft (%s) – %s wartet", was, was_vor)
        try:
            while laeuft:
                if self.abbruch:
                    raise LoeschFehler("abgebrochen")
                if self.uhr() - seit > BACKUP_MAX_S:
                    raise LoeschFehler(f"Backup dauert länger als {_dauer(BACKUP_MAX_S)} – Lauf beendet")
                self.fortschritt["backup"] = f"wartet auf Backup seit {_dauer(self.uhr() - seit)}"
                self.schlafe(30)
                laeuft, was = self.backup()
        finally:
            gewartet = self._backup_gewartet(self.uhr() - seit)
        _LOG.info("Löschlauf: Backup beendet nach %s, es geht weiter", _dauer(gewartet))

    def _nachziehen(self, eid, mid, d, rest):
        """Punkt 53 (2): Entität, die genau gezählt nicht in den Rest passt, mit dem kleinsten keep_days > d löschen,
        bei dem die gezählte Menge hineinpasst (Suche halbierend; die Menge fällt mit wachsendem keep_days)."""
        if rest <= 0 or self.abbruch:
            return rest
        jetzt = time.time()
        grenze = (lambda tage: mitternacht(tage, self.bezug)) if self.exakt else (lambda tage: jetzt - tage * TAG)  # noqa: E731
        zaehle = lambda tage: _zaehle(self.db, [mid], grenze(tage))[mid]  # noqa: E731
        hoch = min(int(self.p["purge_keep_days"]) - 1, MAX_KEEP_DAYS)
        if hoch <= d or zaehle(hoch) > rest:
            return rest                       # passt auch mit dem größten keep_days nicht – nächster Lauf
        tief = d + 1                          # kleinstes keep_days suchen, dessen Menge in den Rest passt
        while tief < hoch:
            mitte = (tief + hoch) // 2
            if zaehle(mitte) <= rest:
                hoch = mitte
            else:
                tief = mitte + 1
        d2, grenze_ts = hoch, grenze(hoch)
        vorher = zaehle(d2)
        if not vorher:
            return rest                       # ein einzelner Tag ist größer als der Rest – nächster Lauf
        self.fortschritt["paket"] += 1
        self.fortschritt["pakete"] += 1
        self.fortschritt["aktuell"] = f"Nachzug {eid}: keep_days {d2}"
        self.fortschritt["aktuell_rest"] = vorher
        eintrag = {"keep_days": d2, "entitaeten": [eid], "gesendet": [eid], "vorher": vorher, "nachher": None, "geloescht": 0,
                   "beginn": time.time(), "dauer_s": None, "ergebnis": None, "nachzug": True,
                   "je_entitaet": {eid: {"vorher": vorher}}}
        self.prot["pakete"].append(eintrag)
        _LOG.info("Nachzug %s: keep_days %d statt %d (%d Zeilen)", eid, d2, d, vorher)
        try:
            attr_kand = _attr_kandidaten(self.db, [mid], grenze_ts)
        except sqlite3.Error:
            attr_kand = {}
        self._warte_auf_backup(f"Nachzug {eid}")     # Punkt 110
        self._states_loeschen([eid], d2, grenze_ts)
        nachher, ergebnis = self._warte([mid], grenze_ts, {mid: vorher})
        self.prot["attr_geloescht"] = self.prot.get("attr_geloescht", 0) + _attr_messen(self.db, attr_kand, eintrag, {eid: mid})
        geloescht = vorher - nachher[mid]
        eintrag["je_entitaet"][eid]["nachher"] = nachher[mid]
        eintrag.update(nachher=nachher[mid], geloescht=geloescht, ergebnis=f"Nachzug ({ergebnis})", dauer_s=round(time.time() - eintrag["beginn"], 1))
        self.prot["geloescht"] += geloescht
        self.fortschritt["geloescht"] = self.prot["geloescht"]
        self._schreibe()
        if ergebnis != "fertig":
            raise LoeschFehler(f"Nachzug {eid}: {ergebnis} – Lauf beendet")
        return rest - geloescht

    def _fuehre_b_aus(self, rest, liste=None):
        """Stufe B: je Auftrag Aufrufe von rs_db_hygiene.purge, bis nichts mehr übrig ist oder die Obergrenze greift.
        liste: Aufträge (Voreinstellung b_liste; Punkt 254: attr_liste nach den States). Liefert den Rest der Obergrenze."""
        for a in sorted(self.b_liste if liste is None else liste, key=lambda a: (a["zeilen"], a["name"])):
            if self.abbruch:
                raise LoeschFehler("abgebrochen")
            if a["art"] == "attr" and self._attr_alt is not None:     # Punkt 256: Integration zu alt – nicht 700-mal dasselbe versuchen
                self._attr_alt["weitere"] += 1
                continue
            self.fortschritt["paket"] += 1
            self.fortschritt["aktuell"] = f"{ART_NAME[a['art']]} {a['name']}" + (" (alle)" if a["alle"] else f", keep_days {a['keep_days']}")
            self.fortschritt["aktuell_rest"] = None
            eintrag = {"art": a["art"], "keep_days": a["keep_days"], "alle": a["alle"], "entitaeten": [a["name"]], "gesendet": [],
                       "vorher": None, "nachher": None, "geloescht": 0, "beginn": time.time(), "dauer_s": None, "ergebnis": None,
                       "je_entitaet": {},
                       # Punkt 277: Plan je Auftrag (Zeilen bzw. Attributzeilen dieses Laufs) – Grundlage der Spalte „Überhang“ im Löschprotokoll
                       "geplant": a.get("zeilen_lauf", a["zeilen"])}
            self.prot["pakete"].append(eintrag)
            if rest <= 0:
                eintrag["ergebnis"] = "Obergrenze erreicht"
                self._schreibe()
                continue
            eintrag["gesendet"] = [a["name"]]
            geloescht, aufrufe = 0, 0
            if a["art"] == "attr":
                # Punkt 266: „gezählt“ zählt hier Attributzeilen (die zur Löschung in Frage kommen), nicht States – so sind gezählt und
                # gelöscht (entfernte Attributzeilen) vergleichbar; die abgetrennten States stehen als „abgetrennt“ im Protokoll
                try:
                    mid_a = _metadata_ids(self.db, [a["name"]]).get(a["name"])
                    gr_ts = self._grenze(a["keep_days"])
                    # Punkt 267: nur die Attributzeilen, die nach dem Abtrennen kein State mehr benutzt – genau das, was „gelöscht“ danach zeigt
                    eintrag["gezaehlt_attr"] = _attr_wegfallend(self.db, mid_a, gr_ts, _attr_kandidaten(self.db, [mid_a], gr_ts).get(mid_a, ())) if mid_a is not None else 0
                except sqlite3.Error:
                    eintrag["gezaehlt_attr"] = 0
                self.fortschritt["gezaehlt"] = self.fortschritt.get("gezaehlt", 0) + eintrag["gezaehlt_attr"]
            while rest > 0:
                self._warte_auf_backup(f"{ART_NAME[a['art']]} {a['name']}")     # Punkt 110
                zusatz = {"vor_ts": self._grenze(a["keep_days"])} if self.exakt and not a["alle"] else {}     # Punkt 121
                try:
                    antwort = self.dienst_b(a["art"], a["name"], a["keep_days"], a["alle"], a["mit_meta"], min(rest, B_PRO_AUFRUF), self.token, **zusatz)
                except urllib.error.HTTPError as x:
                    if a["art"] != "attr" or x.code not in (400, 422):
                        raise
                    # Punkt 254: ältere Integration kennt states_attribute nicht – die übrigen Aufträge laufen weiter
                    eintrag["ergebnis"] = "Integration zu alt – bitte auf ≥ 0.5.6b9 aktualisieren"
                    self._attr_alt = {"eintrag": eintrag, "weitere": 0}
                    _LOG.warning("Attribute %s: Integration kennt states_attribute nicht (HTTP %s) – bitte aktualisieren", a["name"], x.code)
                    break
                aufrufe += 1
                if eintrag["vorher"] is None:
                    eintrag["vorher"] = antwort.get("vorher", 0)
                    if a["art"] != "attr":      # Punkt 266: bei Attributen zählt gezaehlt_attr (oben)
                        self.fortschritt["gezaehlt"] = self.fortschritt.get("gezaehlt", 0) + eintrag["vorher"]
                if a["art"] == "attr":      # Punkt 254: entfernte Attributzeilen zählen wie bei den States-Läufen
                    eintrag["attr_geloescht"] = eintrag.get("attr_geloescht", 0) + int(antwort.get("attr_zeilen_geloescht", 0))
                n = int(antwort.get("geloescht", 0))
                geloescht += n
                rest -= n
                eintrag["nachher"] = int(antwort.get("rest", 0))
                self.fortschritt["aktuell_rest"] = eintrag["nachher"]
                # Punkt 257: Attribut-Aufträge trennen States ab – Log und Fortschritt zählen sie als „abgetrennt“, nicht als 0 gelöscht
                attr = a["art"] == "attr"
                vorab = self.prot["geloescht"] + (self.prot.get("abgetrennt", 0) if attr else 0)
                _LOG.info("Löschlauf %s · %s %s: %s %s (Aufruf %d), noch %s · gesamt %s%s", _dauer(time.time() - self.start),   # Punkt 109
                          ART_NAME[a["art"]], a["name"], _z(n), "States abgetrennt" if attr else "gelöscht", aufrufe, _z(eintrag["nachher"]),
                          _z(vorab + geloescht), f" · {_z(eintrag.get('attr_geloescht'))} Attributzeilen entfernt" if attr else "")
                self.fortschritt["geloescht"] = (self.prot["geloescht"] + self._attr_gel + eintrag.get("attr_geloescht", 0)) if attr else vorab + geloescht   # Punkt 266
                if antwort.get("unbekannt"):
                    eintrag["ergebnis"] = "nicht in der DB (unbekannt)"
                if not n or not eintrag["nachher"] or self.abbruch:
                    break
            eintrag["geloescht"] = geloescht
            eintrag["je_entitaet"] = {a["name"]: {"vorher": eintrag["vorher"] or 0, "nachher": eintrag["nachher"] or 0,
                                                  **({"attr_geloescht": eintrag.get("attr_geloescht", 0)} if a["art"] == "attr" else {})}}
            eintrag["ergebnis"] = eintrag["ergebnis"] or ("fertig" if not eintrag["nachher"] else "Rest im nächsten Lauf")
            eintrag["dauer_s"] = round(time.time() - eintrag["beginn"], 1)
            eintrag["aufrufe"] = aufrufe
            fertig_ok = eintrag["ergebnis"] == "fertig"     # vor dem Recorder-Hinweis festhalten (Punkt 260)
            # Punkt 261: sehr lange Wartezeit bei wenig Arbeit = der Recorder war beschäftigt (HA-Repack, Backup, große Aufgabe davor)
            if eintrag["dauer_s"] >= RECORDER_WARTE_S and max(geloescht, eintrag["vorher"] or 0) / eintrag["dauer_s"] < 50:
                eintrag["recorder_beschaeftigt"] = True
                eintrag["ergebnis"] = f"{eintrag['ergebnis']} – Recorder war beschäftigt (vermutlich Repack oder Backup)"
                _LOG.warning("%s %s: %s s gewartet bei wenig Arbeit – Recorder beschäftigt (vermutlich Repack oder Backup)", ART_NAME[a["art"]], a["name"], eintrag["dauer_s"])
            if a["art"] == "attr" and fertig_ok:     # Punkt 260
                self.prot["attr_cache"] = self.prot.get("attr_cache", 0) + attr_cache_bereinigen(self.attr_pfad, self.cache_pfad, [(a["name"], a["keep_days"])])
            if a["art"] == "attr":
                # Punkt 254: geloescht zählt hier abgetrennte States, nicht gelöschte Zeilen – die Summe „Zeilen gelöscht“ bleibt
                # den echten Löschungen vorbehalten; die entfernten Attributzeilen stehen wie bei den States in attr_geloescht
                eintrag["abgetrennt"], eintrag["geloescht"] = geloescht, 0
                self.prot["attr_geloescht"] = self.prot.get("attr_geloescht", 0) + eintrag.get("attr_geloescht", 0)
                self.prot["abgetrennt"] = self.prot.get("abgetrennt", 0) + geloescht
                self._attr_gel += eintrag.get("attr_geloescht", 0)
                self.fortschritt["geloescht"] = self.prot["geloescht"] + self._attr_gel   # Punkt 266: Attributzeilen, vergleichbar mit „gezählt“
            else:
                self.prot["geloescht"] += geloescht
                self.fortschritt["geloescht"] = self.prot["geloescht"]
            self._schreibe()
            _LOG.info("%s %s: %d Zeilen %s in %d Aufrufen (%s)", ART_NAME[a["art"]], a["name"], geloescht, "abgetrennt" if a["art"] == "attr" else "gelöscht", aufrufe, eintrag["ergebnis"])
        if self._attr_alt and self._attr_alt["weitere"]:     # Punkt 256: ein Sammelhinweis statt hunderter Einträge
            e = self._attr_alt["eintrag"]
            e["uebersprungen_weitere"] = self._attr_alt["weitere"]
            e["ergebnis"] = f"{e['ergebnis']} – {self._attr_alt['weitere']} weitere Attribut-Aufträge übersprungen"
            _LOG.warning("Attribute: %d weitere Aufträge übersprungen (Integration zu alt)", self._attr_alt["weitere"])
            self._attr_alt["weitere"] = 0
            self._schreibe()
        return rest

    def _wieder_vorhanden(self, teil):
        """Punkt 84: Verwaiste dieses Pakets, die jetzt wieder als Zustand in HA existieren (z. B. Blitz- oder
        Waldbrand-Meldungen ohne Registry-Eintrag). Sie werden übersprungen; ist die Prüfung nicht möglich, alle Verwaisten."""
        verw = [e for e in teil if e in self.verwaiste]
        if not verw:
            return [], ""
        da = self.vorhanden(self.token)
        if da is None:
            return verw, "Prüfung auf wieder vorhandene Entitäten nicht möglich – Verwaiste übersprungen"
        wieder = [e for e in verw if e in da]
        return wieder, "wieder vorhanden – übersprungen" if wieder else ""

    def fuehre_aus(self):
        rest = self.p["grenze"]
        su = self.p.get("summe") or {}
        _LOG.info("Löschlauf gestartet (%s%s): States ca. %s Zeilen von %d Entitäten in %d Aufrufen, Statistik/Events %d Aufträge; "   # Punkt 109
                  "Obergrenzen States %s, Statistik/Events %s; Grenze %s", self.prot.get("ausgeloest"), f", Regel {self.p['nur_regel']}" if self.p.get("nur_regel") else "",
                  _z(su.get("zeilen_states")), su.get("entitaeten", 0), su.get("aufrufe", 0), len(self.b_liste),
                  _z(self.p["grenze"]), _z(self.p.get("grenze_b", self.p["grenze"])),
                  "lokale Mitternacht (volle Kalendertage)" if self.exakt else "jetzt minus keep_days (Integration ohne purge_states)")
        try:
            # ab 0.4.3 (Punkt 82): Stufe B zuerst und mit eigener Obergrenze – sie ist klein und schnell und kommt so auch
            # dann an die Reihe, wenn die States die Grenze füllen oder ein States-Paket den Lauf beendet
            rest_b = self._fuehre_b_aus(self.p.get("grenze_b", self.p["grenze"]))
            for g in self.p["gruppen"]:
                d = g["keep_days"]
                for i in range(0, len(g["entitaeten"]), PAKET):
                    if self.abbruch:
                        raise LoeschFehler("abgebrochen")
                    teil = g["entitaeten"][i:i + PAKET]
                    self.fortschritt["paket"] += 1
                    self.fortschritt["aktuell_rest"] = None      # wird gleich frisch gezählt
                    self.fortschritt["aktuell"] = f"keep_days {d}: {len(teil)} Entitäten"
                    grenze_ts = self._grenze(d)
                    wieder, wieder_grund = self._wieder_vorhanden(teil)
                    if wieder:
                        _LOG.info("%d verwaiste Entitäten übersprungen (%s): %s", len(wieder), wieder_grund, ", ".join(wieder[:5]))
                    mid = _metadata_ids(self.db, [e for e in teil if e not in wieder])
                    vorher = _zaehle(self.db, list(mid.values()), grenze_ts)
                    # Obergrenze auf die tatsächlich gezählten Zeilen, kleinste zuerst
                    auswahl, summe = [], 0
                    for eid in sorted(mid, key=lambda x: vorher[mid[x]]):
                        n = vorher[mid[eid]]
                        if n and (summe + n <= rest or eid in self.ausnahmen):
                            auswahl.append(eid)
                            summe += n
                    # Punkt 53 (2): Wer genau gezählt nicht mehr passt, wird danach mit größerem keep_days nachgezogen
                    nachzug = [eid for eid in mid if vorher[mid[eid]] and eid not in auswahl]
                    eintrag = {"keep_days": d, "entitaeten": teil, "gesendet": auswahl, "vorher": summe,
                               "nachher": None, "geloescht": 0, "beginn": time.time(), "dauer_s": None,
                               "ergebnis": None,
                               "je_entitaet": {eid: {"vorher": vorher[mid[eid]]} for eid in auswahl}}
                    if wieder:
                        eintrag.update(uebersprungen=wieder, uebersprungen_grund=wieder_grund)
                    self.prot["pakete"].append(eintrag)
                    self.fortschritt["aktuell_rest"] = summe        # nicht den Rest des vorigen Pakets stehen lassen
                    self.fortschritt["gezaehlt"] = self.fortschritt.get("gezaehlt", 0) + summe
                    if not auswahl:
                        eintrag["ergebnis"] = "nichts zu löschen oder Obergrenze erreicht"
                        self._schreibe()
                        for eid in nachzug:
                            rest = self._nachziehen(eid, mid[eid], d, rest)
                        continue
                    self._warte_auf_backup(f"Paket keep_days {d}")     # Punkt 110
                    _LOG.info("%s für %d Entitäten (%d Zeilen)", f"purge_states vor {time.strftime('%d.%m.%Y %H:%M', time.localtime(grenze_ts))} (keep_days {d})"
                              if self.exakt else f"purge_entities keep_days={d}", len(auswahl), summe)
                    mids = [mid[e] for e in auswahl]
                    try:
                        attr_kand = _attr_kandidaten(self.db, mids, grenze_ts)
                    except sqlite3.Error:
                        attr_kand = {}
                    self._states_loeschen(auswahl, d, grenze_ts)
                    nachher, ergebnis = self._warte(mids, grenze_ts, {m: vorher[m] for m in mids})
                    self.prot["attr_geloescht"] = self.prot.get("attr_geloescht", 0) + _attr_messen(
                        self.db, attr_kand, eintrag, {e: mid[e] for e in auswahl})
                    geloescht = summe - sum(nachher.values())
                    for eid in auswahl:
                        eintrag["je_entitaet"][eid]["nachher"] = nachher[mid[eid]]
                    eintrag.update(nachher=sum(nachher.values()), geloescht=geloescht, ergebnis=ergebnis,
                                   dauer_s=round(time.time() - eintrag["beginn"], 1))
                    rest -= geloescht
                    self.prot["geloescht"] += geloescht
                    self.fortschritt["geloescht"] = self.prot["geloescht"]
                    self._schreibe()
                    _LOG.info("Paket fertig: %d Zeilen gelöscht (%s)", geloescht, ergebnis)
                    if ergebnis == "fertig":     # Punkt 260: mit den States sind auch ihre unbenutzten Attributzeilen weg
                        self.prot["attr_cache"] = self.prot.get("attr_cache", 0) + attr_cache_bereinigen(self.attr_pfad, self.cache_pfad, [(e, d) for e in auswahl])
                    if ergebnis != "fertig":
                        raise LoeschFehler(f"Paket keep_days {d}: {ergebnis} – Lauf beendet")
                    for eid in nachzug:
                        rest = self._nachziehen(eid, mid[eid], d, rest)
            if self.attr_liste:      # Punkt 254: danach Attribute abtrennen (Rest der Stufe-B-Obergrenze)
                self._fuehre_b_aus(rest_b, self.attr_liste)
            self.prot["status"] = "fertig"
        except Exception as e:  # noqa: BLE001 – im Protokoll festhalten, Job meldet weiter
            self.prot["status"] = "abgebrochen" if "abgebrochen" in str(e) else "Fehler"
            self.prot["fehler"] = str(e)
            raise
        finally:
            self.prot["ende"] = time.time()
            self._schreibe()
            bw = self.prot.get("backup_warte_s") or 0      # Punkt 116
            _LOG.info("Löschlauf %s nach %s%s: %s Zeilen gelöscht (%s Attributzeilen)%s", self.prot["status"], _dauer(self.prot["ende"] - self.start),   # Punkt 109
                      f" (davon {_dauer(bw)} auf Backup gewartet)" if bw else "",
                      _z(self.prot["geloescht"]), _z(self.prot.get("attr_geloescht")), f" – {self.prot['fehler']}" if self.prot.get("fehler") else "")
        return self.prot


def _laufdateien(ordner):
    if not os.path.isdir(ordner):
        return []
    return sorted((n for n in os.listdir(ordner) if n.endswith(".json")), key=lambda n: float(n[:-5]) if n[:-5].replace(".", "").isdigit() else 0,
                  reverse=True)


def summe_nachtragen(ordner, summe_pfad):
    """Monatssumme je Entität (Punkt 45): bleibt erhalten, wenn alte Läufe aufgeräumt werden.

    Jeder beendete Lauf wird genau einmal eingerechnet (Liste "laeufe"); beim ersten Aufruf werden alle
    vorhandenen Läufe nachgetragen.
    """
    try:
        with open(summe_pfad, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {"laeufe": [], "je": {}}
    bekannt, neu = set(s["laeufe"]), 0
    for name in _laufdateien(ordner):
        try:
            with open(os.path.join(ordner, name), encoding="utf-8") as f:
                p = json.load(f)
        except (OSError, ValueError):
            continue
        if not p.get("ende") or p["start"] in bekannt:
            continue
        monat = time.strftime("%Y-%m", time.localtime(p["start"]))
        for pk in p.get("pakete", []):
            for eid, v in (pk.get("je_entitaet") or {}).items():
                if v.get("nachher") is None:
                    continue
                m = s["je"].setdefault(eid, {}).setdefault(monat, {"zeilen": 0, "laeufe": 0})
                # Punkt 76 (0.4.4): Paket mit genau einer Entität bzw. einem Event-Typ – die tatsächlich gelöschte Zahl nehmen.
                # Während des Pakets rutschen weitere Zeilen über die Altersgrenze und werden mitgelöscht (v. a. Stufe B,
                # die je Aufruf neu rechnet); „vorher − nachher“ läge dann knapp unter der Lauf-Summe.
                if len(pk.get("je_entitaet") or {}) == 1 and pk.get("geloescht") is not None:
                    d = int(pk["geloescht"])
                else:
                    d = max(0, v["vorher"] - v["nachher"])
                m["zeilen"] += d
                art = pk.get("art", "states")      # ab 0.3.0 je Tabelle getrennt
                m[art] = m.get(art, 0) + d
                if v.get("attr_geloescht"):        # ab 0.4.0: gemessene Attributzeilen (nicht in „zeilen“)
                    m["attr_zeilen"] = m.get("attr_zeilen", 0) + v["attr_geloescht"]
                m["laeufe"] += 1
        s["laeufe"].append(p["start"])
        neu += 1
    if neu or not os.path.exists(summe_pfad):
        tmp = summe_pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False)
        os.replace(tmp, summe_pfad)
    return s


def aufraeumen(ordner, summe_pfad, tage, laufend=None):
    """Läufe älter als `tage` löschen (0 = unbegrenzt); vorher in die Monatssumme übernehmen."""
    s = summe_nachtragen(ordner, summe_pfad)
    if not tage:
        return 0
    grenze, weg = time.time() - tage * TAG, 0
    for name in _laufdateien(ordner):
        try:
            start = int(float(name[:-5]))
        except ValueError:
            continue
        # unterbrochene Läufe (ohne Ende) stehen nicht in der Summe; nach Ablauf der Frist werden sie trotzdem gelöscht
        if start < grenze and (laufend is None or start != int(laufend)):
            os.remove(os.path.join(ordner, name))
            weg += 1
    return weg


def summe_liste(summe_pfad):
    """Monatssumme als Zeilen (Entität, Monat, Zeilen, Läufe) für die Tabelle."""
    try:
        with open(summe_pfad, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        return []
    return [{"entity_id": eid, "monat": m, **v} for eid, monate in s["je"].items() for m, v in monate.items()]


def protokolle(ordner, anzahl=1000, laufend=None):
    """Läufe, neueste zuerst. Läufe ohne Ende (App neu gestartet) gelten als unterbrochen."""
    out = []
    for name in _laufdateien(ordner)[:anzahl]:
        try:
            with open(os.path.join(ordner, name), encoding="utf-8") as f:
                p = json.load(f)
        except (OSError, ValueError):
            continue
        if not p.get("ende") and p.get("status") == "läuft" and p.get("start") != laufend:
            p["status"] = "unterbrochen"
        out.append(p)
    return out


def verwaist_ungedeckt(pl, regel_daten):
    """Verwaiste Entitäten, die zum Muster einer aktiven glob-Regel passen, von ihr aber nicht erfasst werden (Punkt 43).

    glob-Regeln gelten nur für aktive Entitäten; Verwaiste erfasst nur E:/entity oder eine Regel vom Typ verwaist.
    """
    globs = [r for r in regel_daten["regeln"] if r["typ"] == "glob" and r.get("aktiv", True)]
    out = {}
    for e in pl["entitaeten"]:
        if not rg.ist_verwaist(e["status"]) or ((e.get("werte") or {}).get("kurzzeit") or {}).get("regel"):
            continue
        for r in globs:
            m = next((m for m in r["muster"] if fnmatch.fnmatchcase(e["entity_id"], m)), None)
            if m:
                g = out.setdefault(r["id"], {"regel": r["id"], "name": r["name"], "kurzzeit": r.get("kurzzeit"),
                                             "muster": [], "entitaeten": 0, "zeilen": 0, "beispiele": []})
                if m not in g["muster"]:
                    g["muster"].append(m)
                g["entitaeten"] += 1
                g["zeilen"] += e.get("rows") or 0
                if len(g["beispiele"]) < 5:
                    g["beispiele"].append(e["entity_id"])
                break
    return sorted(out.values(), key=lambda g: -g["zeilen"])
