"""Regelmodell: Vorhaltezeiten pro Entität.

Ab 0.3.4 (Sammelliste 61): Es gibt keinen eigenen Wert „Attribute“ mehr. Attribute folgen immer der Kurzzeit und werden
mit den Kurzzeit-States gelöscht (HA räumt dabei nicht mehr benutzte Attributzeilen ab). Ein gesetzter Attribut-Wert
in der Regeldatei wird beim Laden verworfen (Hinweis im App-Protokoll); der Schlüssel bleibt intern als „wie_kurzzeit“.

  kurzzeit   Rohdaten (states) und 5-Min-Statistik     Standard "global" (= purge_keep_days)
  attribute  Attribute der Rohdaten                     Standard "wie_kurzzeit"
  langzeit   Stundenstatistik (statistics)              Standard "unbegrenzt"

Werte:  "<n>d" Tage, "<n>w" Wochen (7 d), "<n>m" Monate (30 d), "<n>j" Jahre (365 d),
        "global" (nur kurzzeit), "wie_kurzzeit" (nur attribute), "unbegrenzt" (nur langzeit),
        "keine" (attribute, langzeit) bzw. null = Regel setzt diesen Wert nicht.

Auflösung je Wert getrennt: Es gilt die höchstrangige aktive Regel, die den Wert setzt.
Rang: entity > glob > klasse, innerhalb eines Typs die Reihenfolge in der Datei.
Zähler-Schutz: glob/klasse greifen nie bei state_class total/total_increasing.

Rang ab 0.1.7: E:-Regeln (eigene Vorhaltezeiten aus dem Entitäts-Dialog) stehen immer ganz vorn, unabhängig
von ihrer Position in der Datei; danach entity > glob > klasse > verwaist.
Je Wert wird neben dem Gewinner auch die Liste der überstimmten Regeln geliefert (Nachvollziehbarkeit).

Typ „verwaist“ (ab 0.1.6) wirkt ausschließlich auf verwaiste Entitäten. Ab 0.1.7 gelten für Verwaiste zusätzlich
die entity-Regeln (E: und andere mit genauer Entity-ID) – mit Vorrang; glob und klasse nur für aktive.
  Muster: Art-Wörter alle | geloescht | ohne_spur (ohne Art-Wort = alle) und optional Entity-Muster (glob).
  Nur hier ist Kurzzeit „keine“ erlaubt (sofort alles); empfohlen ist eine Frist wie 30 Tage.

Typ „event“ (ab 0.3.1, Punkt 51): Vorhaltezeit je Event-Typ als Regel in der Regelliste.
  Muster sind Event-Typen, auch mit Platzhalter (call_service, script_*); gesetzt wird nur die Kurzzeit (= Vorhaltezeit
  der Events), Attribute und Langzeit nicht. Wirkt nie auf Entitäten. Rang: genaue Namen vor Platzhaltern, dann die
  Reihenfolge in der Datei. Der Eintrag "events" aus 0.3.0 wird beim Laden in Regeln EV:<typ> übernommen; fehlt er
  ganz, entsteht der Startwert EV:call_service mit 7 Tagen.

Typ „schutz“ (ab 0.4.9, Punkt 111): Daten, die nie gekürzt werden dürfen. Höchster Rang, noch vor E:-Regeln;
  setzt fest Kurzzeit „global“ und Langzeit „unbegrenzt“ (andere Werte werden beim Laden ersetzt). Muster wie glob
  (Platzhalter erlaubt), gilt für aktive und verwaiste Entitäten, auch für Zähler. Der Löschplan schließt geschützte
  Entitäten zusätzlich ausdrücklich aus.

Typ „ha_standard“ (ab 0.5.3, Punkt 127): genau eine Regel mit der festen ID HA:standard. Sie bildet das Löschverhalten
  von Home Assistant in der Grundeinstellung ab (Recorder purge_keep_days 10): Kurzzeit 10 Tage (States, 5-Min-Statistik,
  Attribute), Langzeit unbegrenzt, Events 10 Tage. Gilt für alle Entitäten – auch verwaiste und Zähler – und alle
  Event-Typen, mit dem niedrigsten Rang: Jede andere Regel geht vor. Name, Muster, Werte und Notiz sind fest, nur
  aktiv/passiv lässt sich ändern. Die Regel kann nicht entfernt werden: Fehlt sie, wird sie beim Laden passiv ergänzt
  (bestehende Installationen, ältere Exporte); die Startregeln enthalten sie aktiv.
"""
import fnmatch
import json
import logging
import os
import re
import shutil
import time

_LOG = logging.getLogger("regeln")
WERTE = ("kurzzeit", "attribute", "langzeit")
EINHEIT_TAGE = {"d": 1, "w": 7, "m": 30, "j": 365}
EINHEIT_NAME = {"d": "Tage", "w": "Wochen", "m": "Monate", "j": "Jahre"}
SONDERWERTE = {"kurzzeit": {"global", "keine"}, "attribute": {"wie_kurzzeit", "keine"}, "langzeit": {"unbegrenzt", "keine"}}
STANDARD = {"kurzzeit": "global", "attribute": "wie_kurzzeit", "langzeit": "unbegrenzt"}
TYP_RANG = {"schutz": 0, "entity": 1, "glob": 2, "klasse": 3, "verwaist": 4, "event": 5, "ha_standard": 6}
SCHUTZ_WERTE = {"kurzzeit": "global", "attribute": None, "langzeit": "unbegrenzt"}   # Punkt 111: fest
# Punkt 127 (0.5.3): Regel „HA-Standard“ – Werte, Name und Notiz fest, nur aktiv/passiv änderbar
HA_STANDARD_ID = "HA:standard"
HA_STANDARD_TAGE = 10           # Voreinstellung des Recorders (purge_keep_days)
HA_STANDARD = {"id": HA_STANDARD_ID, "typ": "ha_standard", "muster": ["*"],
               "name": f"HA-Standard: alles {HA_STANDARD_TAGE} Tage, Langzeit unbegrenzt",
               "notiz": (f"Bildet das Löschverhalten von Home Assistant in der Grundeinstellung ab (purge_keep_days {HA_STANDARD_TAGE}): "
                         f"States, 5-Min-Statistik, Attribute und Events nach {HA_STANDARD_TAGE} Tagen, Langzeitstatistik nie. "
                         "Gilt für alles ohne eigene Regel (niedrigster Rang). Nicht löschbar, nur passiv schaltbar."),
               "kurzzeit": f"{HA_STANDARD_TAGE}d", "attribute": None, "langzeit": "unbegrenzt"}
VERWAIST_ARTEN = {"alle", "geloescht", "ohne_spur"}
# Status aus der Analyse → Art-Wort der Regel
VERWAIST_STATUS = {"verwaist_geloescht": "geloescht", "verwaist": "ohne_spur"}
_DAUER = re.compile(r"^(\d{1,5})([dwmj])$")
# Startwert, wenn die Regeldatei noch keine Events kennt: call_service 7 Tage
STANDARD_EVENTS = {"call_service": "7d"}
_EVENT_TYP = re.compile(r"^[a-z0-9_.]{1,64}$")
_EVENT_MUSTER = re.compile(r"^[a-z0-9_.*?\[\]-]{1,64}$")


class RegelFehler(ValueError):
    pass


def pruefe_wert(art, wert):
    if wert is None:
        return None
    wert = str(wert).strip().lower()
    if wert in SONDERWERTE[art]:
        return wert
    m = _DAUER.match(wert)
    if not m or int(m.group(1)) < 1:
        raise RegelFehler(f"{art}: ungültiger Wert '{wert}' (erlaubt: n d/w/m/j oder {', '.join(sorted(SONDERWERTE[art]))})")
    return wert


def in_tagen(wert):
    """'6m' -> 180; Sonderwerte -> None."""
    m = _DAUER.match(wert or "")
    return int(m.group(1)) * EINHEIT_TAGE[m.group(2)] if m else None


def anzeige(wert):
    m = _DAUER.match(wert or "")
    if not m:
        return {"global": "global", "wie_kurzzeit": "wie Kurzzeit", "unbegrenzt": "unbegrenzt", "keine": "keine"}.get(wert, "–")
    n, e = int(m.group(1)), m.group(2)
    return f"{n} {EINHEIT_NAME[e]}" if n != 1 else f"1 {EINHEIT_NAME[e][:-1] if e != 'd' else 'Tag'}"


def normalisiere(daten):
    """Prüft und normalisiert eine Regeldatei; wirft RegelFehler."""
    std = dict(STANDARD)
    for art in WERTE:
        w = pruefe_wert(art, (daten.get("standard") or {}).get(art, STANDARD[art]))
        std[art] = w or STANDARD[art]
    std["attribute"] = "wie_kurzzeit"      # Punkt 61: Attribute folgen immer der Kurzzeit
    regeln, ids = [], set()
    for i, r in enumerate(daten.get("regeln") or []):
        rid = str(r.get("id") or f"R{i + 1:02d}")
        if rid in ids:
            raise RegelFehler(f"Regel-ID doppelt: {rid}")
        ids.add(rid)
        typ = r.get("typ")
        if typ == "ha_standard" or rid == HA_STANDARD_ID:     # Punkt 127: alles fest außer aktiv
            if typ != "ha_standard" or rid != HA_STANDARD_ID:
                raise RegelFehler(f"{rid}: Typ ha_standard und ID {HA_STANDARD_ID} gehören fest zusammen (genau eine Regel)")
            regeln.append({**HA_STANDARD, "aktiv": r.get("aktiv", True) is not False})
            continue
        if typ not in TYP_RANG:
            raise RegelFehler(f"{rid}: typ muss schutz, entity, glob, klasse, verwaist oder event sein")
        roh = r.get("muster") or []
        if isinstance(roh, str):
            roh = re.split(r"[\n,]", roh)
        muster = [str(m).strip() for m in roh if str(m).strip()]
        if not muster:
            raise RegelFehler(f"{rid}: mindestens ein Muster nötig")
        if typ == "klasse" and any("|" not in m for m in muster):
            raise RegelFehler(f"{rid}: klasse-Muster haben die Form domain|device_class")
        neu = {"id": rid, "name": str(r.get("name") or rid), "typ": typ, "muster": muster,
               "aktiv": r.get("aktiv", True) is not False, "notiz": str(r.get("notiz") or "")}
        for art in WERTE:
            neu[art] = pruefe_wert(art, r.get(art))
        if typ == "schutz":                   # Punkt 111: Werte fest, unabhängig von der Eingabe
            neu.update(SCHUTZ_WERTE)
        if neu["attribute"] is not None:      # Punkt 61: eigener Attribut-Wert entfällt
            if neu["attribute"] != "wie_kurzzeit":
                _LOG.warning("Regel %s: Attribut-Wert %s verworfen – Attribute folgen ab 0.3.4 immer der Kurzzeit", rid, neu["attribute"])
            neu["attribute"] = None
            if not neu["kurzzeit"] and not neu["langzeit"]:
                _LOG.warning("Regel %s setzte nur Attribute und wurde entfernt", rid)
                continue
        if neu["kurzzeit"] == "keine" and typ != "verwaist":
            raise RegelFehler(f"{rid}: Kurzzeit „keine“ ist nur beim Regeltyp verwaist erlaubt")
        if typ == "verwaist":
            neu["muster"] = [m.lower() if m.lower() in VERWAIST_ARTEN else m for m in muster]
        if typ == "event":
            if any(not _EVENT_MUSTER.match(m) for m in muster):
                raise RegelFehler(f"{rid}: Event-Muster sind Event-Typen wie call_service oder script_* (Kleinbuchstaben, Ziffern, _ . * ?)")
            if neu["attribute"] or neu["langzeit"]:
                raise RegelFehler(f"{rid}: Event-Regeln setzen nur die Vorhaltezeit (Kurzzeit), nicht Attribute oder Langzeit")
            if not neu["kurzzeit"] or neu["kurzzeit"] == "keine":
                raise RegelFehler(f"{rid}: Event-Regeln brauchen eine Vorhaltezeit (global oder n d/w/m/j)")
        if not any(neu[a] for a in WERTE):
            raise RegelFehler(f"{rid}: Regel setzt keinen Wert")
        regeln.append(neu)
    # Übernahme aus 0.3.0 (Punkt 51): "events": {typ: wert} → Regeln EV:<typ>; ohne Schlüssel der Startwert
    for typ, wert in (daten["events"] if "events" in daten else STANDARD_EVENTS).items():
        typ = str(typ).strip()
        if not _EVENT_TYP.match(typ):
            raise RegelFehler(f"Event-Typ ungültig: {typ}")
        w = str(wert or "global").strip().lower()
        rid = f"EV:{typ}"
        if w == "global" or rid in ids:
            continue
        m = _DAUER.match(w)
        if not m or int(m.group(1)) < 1:
            raise RegelFehler(f"Events {typ}: ungültiger Wert '{w}' (erlaubt: global oder n d/w/m/j)")
        ids.add(rid)
        regeln.append({"id": rid, "name": f"Events {typ}", "typ": "event", "muster": [typ], "aktiv": True,
                       "notiz": "übernommen aus der Event-Liste (0.3.0)", "kurzzeit": w, "attribute": None, "langzeit": None})
    # Punkt 127: Die Regel HA-Standard ist nicht löschbar – fehlt sie, wird sie passiv ergänzt
    if HA_STANDARD_ID not in {r["id"] for r in regeln}:
        regeln.append({**HA_STANDARD, "aktiv": False})
    # "events" bleibt leer stehen, damit der Startwert nach dem Löschen der Regel nicht wiederkommt
    return {"standard": std, "regeln": regeln, "events": {}}


def event_regel(daten, typ, purge_keep_days, mit_passiven=False):
    """Wirksame Event-Regel für einen Event-Typ: {tage, wert, regel, muster, ueberstimmt}.

    Rang: genaue Namen vor Platzhaltern, innerhalb davon die Reihenfolge in der Datei. Ohne Regel gilt global
    (= purge_keep_days); längere Zeiten sind durch purge_keep_days gedeckelt.
    """
    treffer = []
    for i, r in enumerate(daten["regeln"]):
        if r["typ"] not in ("event", "ha_standard") or not (r["aktiv"] or mit_passiven):
            continue
        if r["typ"] == "ha_standard":         # Punkt 127: alle Event-Typen, nach allen Event-Regeln
            treffer.append((2, i, r, "*"))
            continue
        m = next((m for m in r["muster"] if m == typ), None) or next((m for m in r["muster"] if fnmatch.fnmatchcase(typ, m)), None)
        if m:
            treffer.append((0 if m == typ else 1, i, r, m))
    treffer.sort(key=lambda t: (t[0], t[1]))
    if not treffer:
        return {"tage": purge_keep_days, "wert": "global", "regel": None, "muster": None, "ueberstimmt": []}
    _g, _i, r, m = treffer[0]
    w = r["kurzzeit"]
    tage = purge_keep_days if w == "global" else min(in_tagen(w), purge_keep_days)
    return {"tage": tage, "wert": w, "regel": r["id"], "muster": m,
            "ueberstimmt": [{"regel": t[2]["id"], "wert": t[2]["kurzzeit"]} for t in treffer[1:]]}


def event_tage(daten, typ, purge_keep_days):
    """Wirksame Vorhaltezeit eines Event-Typs in Tagen (über die Event-Regeln)."""
    return event_regel(daten, typ, purge_keep_days)["tage"]


def lade(pfad, vorlage=None):
    if not os.path.exists(pfad) and vorlage and os.path.exists(vorlage):
        with open(vorlage, encoding="utf-8") as f:
            speichere(pfad, json.load(f))
    if not os.path.exists(pfad):
        return normalisiere({})
    with open(pfad, encoding="utf-8") as f:
        return normalisiere(json.load(f))


def speichere(pfad, daten):
    daten = normalisiere(daten)
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)
    os.replace(tmp, pfad)
    return daten


# Punkte 123/124 (0.5.2): Sicherungen vor Zurücksetzen, Wiederherstellen und Import
# Dateiname regeln_<JJJJMMTT>_<hhmmss>_<grund>[<n>].json – n nur, wenn zwei Sicherungen in dieselbe Sekunde fallen
SICHERUNGEN_BEHALTEN = 10
_SICHERUNG = re.compile(r"^regeln_\d{8}_\d{6}_([a-z]+?)(\d*)\.json$")
GRUENDE = {"zuruecksetzen": "vor Zurücksetzen", "wiederherstellen": "vor Wiederherstellen", "import": "vor Import"}


def kennzahlen(daten):
    """Anzahl Regeln und davon aktive – für Rückfragen und die Sicherungsliste."""
    r = daten.get("regeln") or []
    return {"anzahl": len(r), "aktiv": sum(1 for x in r if x.get("aktiv", True) is not False)}


def lies_import(roh):
    """Prüft eine Regeldatei aus Export oder Sicherung; wirft RegelFehler. Liefert die normalisierten Daten."""
    if not isinstance(roh, dict) or not isinstance(roh.get("regeln"), list):
        raise RegelFehler("Keine Regeldatei: es fehlt die Liste „regeln“")
    # ohne Schlüssel „events“ ergänzt normalisiere den Startwert EV:call_service – beim Import nicht erwünscht
    return normalisiere({**roh, "events": roh.get("events") or {}})


def sichere(pfad, ordner, grund):
    """Kopie der aktuellen Regeldatei nach ordner; behält die letzten SICHERUNGEN_BEHALTEN. Liefert den Dateinamen."""
    if not os.path.exists(pfad):
        return None
    os.makedirs(ordner, exist_ok=True)
    stamm, n = f"regeln_{time.strftime('%Y%m%d_%H%M%S')}_{grund}", 1
    name = stamm + ".json"
    while os.path.exists(os.path.join(ordner, name)):
        n += 1
        name = f"{stamm}{n}.json"
    shutil.copyfile(pfad, os.path.join(ordner, name))   # neue Änderungszeit = Zeitpunkt der Sicherung
    for alt in liste_sicherungen(ordner)[SICHERUNGEN_BEHALTEN:]:
        os.remove(os.path.join(ordner, alt["name"]))
    return name


def liste_sicherungen(ordner):
    """Sicherungen, neueste zuerst: [{name, zeit, grund, anzahl, aktiv}] – unlesbare Dateien mit fehler."""
    if not os.path.isdir(ordner):
        return []
    aus = []
    for name in os.listdir(ordner):
        m = _SICHERUNG.match(name)
        if not m:
            continue
        p = os.path.join(ordner, name)
        e = {"name": name, "zeit": os.path.getmtime(p), "grund": GRUENDE.get(m.group(1), m.group(1))}
        try:
            with open(p, encoding="utf-8") as f:
                e.update(kennzahlen(json.load(f)))
        except (OSError, ValueError) as x:
            e["fehler"] = str(x)
        aus.append(e)
    return sorted(aus, key=lambda e: (e["zeit"], e["name"]), reverse=True)


def lies_sicherung(ordner, name):
    if not _SICHERUNG.match(name or ""):
        raise RegelFehler("Unbekannte Sicherung")
    p = os.path.join(ordner, name)
    if not os.path.exists(p):
        raise RegelFehler("Sicherung nicht gefunden")
    with open(p, encoding="utf-8") as f:
        return lies_import(json.load(f))


class Aufloeser:
    """Ermittelt je Entität die wirksamen drei Werte samt Herkunft."""

    def __init__(self, daten, mit_vorgemerkten=False):
        self.std = daten["standard"]
        self.regeln = sorted(
            (r for r in daten["regeln"] if r["aktiv"] or mit_vorgemerkten),
            key=lambda r: (rang(r), daten["regeln"].index(r)))

    @staticmethod
    def _trifft(r, eid, domain, dc, status="aktiv"):
        if r["typ"] == "verwaist":
            arten = {m for m in r["muster"] if m in VERWAIST_ARTEN} or {"alle"}
            globs = [m for m in r["muster"] if m not in VERWAIST_ARTEN]
            if "alle" not in arten and VERWAIST_STATUS.get(status) not in arten:
                return None
            if globs:
                return next((m for m in globs if fnmatch.fnmatchcase(eid, m)), None)
            return ", ".join(sorted(arten))
        if r["typ"] == "ha_standard":         # Punkt 127: alles
            return "*"
        if r["typ"] == "entity":
            return next((m for m in r["muster"] if m == eid), None)
        if r["typ"] in ("glob", "schutz"):
            return next((m for m in r["muster"] if fnmatch.fnmatchcase(eid, m)), None)
        return next((m for m in r["muster"] if m == f"{domain}|{dc}"), None)

    def loese(self, eid, state_class="", device_class="", status="aktiv"):
        """Je Wert: Gewinner {wert, regel, muster, ueberstimmt: [{wert, regel, muster}, …]} plus geschuetzt."""
        domain = eid.split(".", 1)[0]
        zaehler = (state_class or "").startswith("total")
        verwaist = status != "aktiv"
        ergebnis, ueberstimmt, geschuetzt = {}, {a: [] for a in WERTE}, []
        for r in self.regeln:
            # verwaist-Regeln nur für Verwaiste; glob/klasse nur für Aktive; entity für beide (Punkt 30)
            if r["typ"] == "event":       # Event-Regeln wirken nie auf Entitäten
                continue
            if r["typ"] == "verwaist" and not verwaist:
                continue
            if verwaist and r["typ"] in ("glob", "klasse"):
                continue
            setzt = [a for a in WERTE if r[a]]
            if not setzt:
                continue
            m = self._trifft(r, eid, domain, device_class or "", status)
            if not m:
                continue
            # Punkt 127: HA-Standard gilt auch für Zähler (HA löscht deren States ebenso; Langzeit bleibt unbegrenzt)
            if zaehler and r["typ"] not in ("entity", "verwaist", "schutz", "ha_standard"):
                geschuetzt.append(r["id"])
                continue
            if r["typ"] == "schutz" and not ergebnis.get("schutz"):
                ergebnis["schutz"] = r["id"]      # Punkt 111: geschützt (Schloss in der GUI, Ausschluss im Löschplan)
            for a in setzt:
                eintrag = {"wert": r[a], "regel": r["id"], "muster": m}
                if a in ergebnis:
                    ueberstimmt[a].append(eintrag)   # Kette für die Nachvollziehbarkeit (Punkt 29)
                else:
                    ergebnis[a] = eintrag
        for a in WERTE:
            ergebnis.setdefault(a, {"wert": self.std[a], "regel": None, "muster": None})
            ergebnis[a]["ueberstimmt"] = ueberstimmt[a]
        ergebnis["geschuetzt"] = geschuetzt
        ergebnis.setdefault("schutz", None)
        return ergebnis


def rang(r):
    """Sortierschlüssel: Schutz vor allem (Punkt 111), dann E:-Regeln (Punkt 28), dann nach Typ."""
    if r["typ"] == "schutz":
        return -1
    return 0 if str(r.get("id", "")).startswith("E:") else TYP_RANG[r["typ"]]


def entity_ueberschneidungen(daten):
    """Entitäten, die von mehr als einer entity-Regel genannt werden: {entity_id: [Regel-IDs in Rangfolge]}."""
    genannt = {}
    reihenfolge = sorted(daten["regeln"], key=lambda r: (rang(r), daten["regeln"].index(r)))
    for r in reihenfolge:
        if r["typ"] == "entity":
            for m in r["muster"]:
                genannt.setdefault(m, []).append(r["id"])
    return {e: ids for e, ids in genannt.items() if len(ids) > 1}


def wirksame_tage(ergebnis, purge_keep_days):
    """Wirksame Vorhaltezeiten in Tagen (None = unbegrenzt) plus Hinweise.

    kurzzeit ist durch purge_keep_days gedeckelt, attribute durch kurzzeit.
    """
    hinweise = []
    kw = ergebnis["kurzzeit"]["wert"]
    k = purge_keep_days if kw == "global" else 0 if kw == "keine" else in_tagen(kw)
    if k > purge_keep_days:
        hinweise.append(f"Kurzzeit {anzeige(kw)} wirkt erst bei purge_keep_days ≥ {k}")
        k = purge_keep_days
    aw = ergebnis["attribute"]["wert"]
    a = k if aw == "wie_kurzzeit" else 0 if aw == "keine" else in_tagen(aw)
    if a > k:
        hinweise.append(f"Attribute {anzeige(aw)} länger als Kurzzeit – wirksam {k} Tage")
        a = k
    lw = ergebnis["langzeit"]["wert"]
    lz = None if lw == "unbegrenzt" else 0 if lw == "keine" else in_tagen(lw)
    if lz is not None and lz < k:
        hinweise.append("Langzeit kürzer als Kurzzeit")
    return {"kurzzeit": k, "attribute": a, "langzeit": lz}, hinweise
