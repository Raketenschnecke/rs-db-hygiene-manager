"""Punkt 232: Regelwerk-Check – Befunde und Vorschläge zum bestehenden Regelwerk (liest nur).

Gegenstück zu den Empfehlungen (optimierung.vorschlaege), die nur neue Regeln für Daten ohne eigene Kurzzeit vorschlagen:
Der Check prüft die aktiven Regeln selbst – wirkungslose und überflüssige Regeln, doppelte Muster, Vorrang zwischen Typen,
tote Muster, zusammenlegbare Regeln und lange Listen, die ein Platzhalter ersetzen kann.

Alles rechnet auf dem Probelauf (Analyse-Cache, nur aktive Regeln), ohne neue Abfrage der HA-Datenbank. Jede Variante eines
Vorschlags wird bewertet, indem die betroffenen Entitäten (und Event-Typen) mit dem heutigen und dem geänderten Regelwerk
aufgelöst werden: ändert sich nirgends ein wirksamer Wert, ist sie wirkungsneutral; sonst nennt sie die Unterschiede.

Stufen: rot = Fehler oder Konflikt, gelb = Verdichtung, grau = Hinweis.
"""
import fnmatch
from collections import Counter

import optimierung
import regeln as rg

PRAEFIX = "check:"           # Kennung im gemeinsamen Ignorier-Speicher der Empfehlungen
MIN_PLATZHALTER = 3          # Listen ab so vielen genauen IDs auf Platzhalter prüfen
MIN_EVENT_LISTE = 10         # Event-Regeln ab so vielen genauen Namen → Vorschlag „*“
UEBERSTIMMT_FAKTOR = 3       # „meist überstimmt“: mindestens dreimal so oft überstimmt wie gewonnen …
UEBERSTIMMT_MIN = 20         # … und mindestens so viele Entitäten
ZUSAMMEN_TYPEN = ("schutz", "entity", "glob", "klasse", "herkunft", "event")
DOPPELT_TYPEN = ("schutz", "glob", "klasse", "herkunft", "event")
STUFE_RANG = {"rot": 0, "gelb": 1, "grau": 2}
BEISPIELE = 30
_CACHE = {"schl": None, "daten": None}


def _platzhalter(m):
    return any(c in m for c in "*?[")


def _ausnahme(r):
    """Ausnahme-Regel aus der Auflösung eines Vorrang-Befunds – nie zusammenlegen oder in Platzhalter umbauen (verlöre den Vorrang)."""
    return r["typ"] == "entity" and r["name"].startswith("Ausnahme: ")


def _aktiv(bestand):
    return [r for r in bestand["regeln"] if r["aktiv"]]


def _kennzahl(regeln):
    """Regeln (ohne HA-Standard) und Muster – für „vorher → nachher“."""
    l = [r for r in regeln if r["typ"] != "ha_standard"]
    return {"regeln": len(l), "muster": sum(len(r["muster"]) for r in l)}


def _anwenden(bestand, aenderung):
    """Regelliste nach einer Änderung {entfernen: [ids], setzen: [Regeln]} – ersetzte Regeln behalten ihren Platz."""
    weg, setzen = set(aenderung.get("entfernen") or []), {r["id"]: r for r in aenderung.get("setzen") or []}
    out = []
    for r in bestand["regeln"]:
        if r["id"] in weg:
            continue
        out.append(setzen.pop(r["id"], r))
    out.extend(setzen.values())
    return {**bestand, "regeln": out}


def _vorfilter(r, ents):
    """Entitäten, die eine Regel treffen kann (optimierung._vorfilter; Vielschreiber mit Hysterese bis 80 %)."""
    if r["typ"] == "vielschreiber":
        s = int(r["muster"][0]) * rg.HYSTERESE
        return [e for e in ents if (e["rows_24h"] or 0) >= s]
    if r["typ"] in ("event", "ha_standard"):
        return []
    return optimierung._vorfilter(r, ents)


def _text(x):
    k, l = rg.anzeige(x["kurzzeit"]["wert"]), rg.anzeige(x["langzeit"]["wert"])
    return f"{k} / {l}" + (" 🔒" if x.get("schutz") else "")


def _stempel(x):
    return (x["kurzzeit"]["wert"], x["langzeit"]["wert"], bool(x.get("schutz")))


def diff(alt, neu, regeln, ctx):
    """Unterschiede der wirksamen Werte zwischen zwei Regelwerken – nur dort, wo eine der geänderten Regeln greifen kann."""
    ents, ids = [], set()
    for r in regeln:
        for e in _vorfilter(r, ctx["ents"]):
            if e["entity_id"] not in ids:
                ids.add(e["entity_id"])
                ents.append(e)
    a, n = rg.Aufloeser(alt), rg.Aufloeser(neu)
    geaendert = []
    for e in ents:
        x, y = a.loese_e(e), n.loese_e(e)
        if _stempel(x) != _stempel(y):
            geaendert.append({"entity_id": e["entity_id"], "rows_24h": e["rows_24h"] or 0, "vorher": _text(x), "nachher": _text(y),
                              "regel_vorher": x["kurzzeit"]["regel"] or x["langzeit"]["regel"],
                              "regel_nachher": y["kurzzeit"]["regel"] or y["langzeit"]["regel"]})
    ev = []
    if any(r["typ"] in ("event", "ha_standard") for r in regeln):
        pkd = ctx["pkd"]
        for t in ctx["events"]:
            x, y = rg.event_regel(alt, t["event_type"], pkd), rg.event_regel(neu, t["event_type"], pkd)
            if x["wert"] != y["wert"]:
                ev.append({"event_type": t["event_type"], "pro_tag": t.get("pro_tag") or 0,
                           "vorher": rg.anzeige(x["wert"]), "nachher": rg.anzeige(y["wert"])})
    geaendert.sort(key=lambda g: -g["rows_24h"])
    ev.sort(key=lambda g: -g["pro_tag"])
    return {"anzahl": len(geaendert), "zeilen_tag": sum(g["rows_24h"] for g in geaendert), "beispiele": geaendert[:BEISPIELE],
            "events": len(ev), "ev_beispiele": ev[:BEISPIELE]}


def knopftext(aenderung, bestand):
    """Beschriftung des Knopfs im Dialog – sagt, was die Variante mit den Regeln macht (statt nur „Übernehmen“)."""
    alt = {r["id"]: r for r in bestand["regeln"]}
    weg, setzen = aenderung.get("entfernen") or [], aenderung.get("setzen") or []
    if weg and not setzen:
        return f"Regel {weg[0]} löschen" if len(weg) == 1 else f"Regeln {', '.join(weg)} löschen"
    if weg and setzen:
        return f"Zu {setzen[0]['id']} zusammenlegen"
    r = setzen[0]
    a = alt.get(r["id"])
    if not a:
        return f"Regel {r['id']} anlegen"
    if r["typ"] != a["typ"] or any(_platzhalter(m) for m in r["muster"]) and not any(_platzhalter(m) for m in a["muster"]):
        return f"Platzhalter in {r['id']} setzen"
    if set(r["muster"]) < set(a["muster"]):
        return f"Muster aus {r['id']} entfernen"
    if set(r["muster"]) > set(a["muster"]):
        return f"In {r['id']} aufnehmen"
    if any(r[x] != a[x] for x in rg.WERTE):
        return f"Werte von {r['id']} ändern"
    return "Übernehmen"


def folgen(aenderung, bestand):
    """Was die Variante am Regelwerk ändert – je Regel ausdrücklich: gelöscht, neu oder geändert (mit dem Was)."""
    alt = {r["id"]: r for r in bestand["regeln"]}
    out = [{"art": "geloescht", "id": i, "name": alt[i]["name"], "text": "wird gelöscht"}
           for i in aenderung.get("entfernen") or [] if i in alt]
    for r in aenderung.get("setzen") or []:
        a = alt.get(r["id"])
        if not a:
            out.append({"art": "neu", "id": r["id"], "name": r["name"],
                        "text": f"wird neu angelegt (Typ {r['typ']}, {len(r['muster'])} Muster, {_wertetext(r)})"})
            continue
        t = []
        dazu, weg = [m for m in r["muster"] if m not in a["muster"]], [m for m in a["muster"] if m not in r["muster"]]
        if r["typ"] != a["typ"]:
            t.append(f"Typ {a['typ']} → {r['typ']}")
        if dazu:
            t.append(f"+{len(dazu)} Muster")
        if weg:
            t.append(f"−{len(weg)} Muster" + (f" ({', '.join(weg)})" if len(weg) <= 3 else ""))
        for x in ("kurzzeit", "langzeit"):
            if r[x] != a[x]:
                t.append(f"{'Kurzzeit' if x == 'kurzzeit' else 'Langzeit'} {rg.anzeige(a[x]) if a[x] else '–'} → {rg.anzeige(r[x]) if r[x] else '–'}")
        if r["name"] != a["name"]:
            t.append(f"Name „{r['name']}“")
        out.append({"art": "geaendert", "id": r["id"], "name": a["name"], "text": "wird geändert: " + (", ".join(t) or "–")})
    return out


def variante(titel, aenderung, ctx, erklaerung=""):
    """Eine Variante samt Bewertung: wirkungsneutral oder mit den Unterschieden."""
    bestand = ctx["bestand"]
    neu = _anwenden(bestand, aenderung)
    alt_r = {r["id"]: r for r in bestand["regeln"]}
    betroffen = [alt_r[i] for i in aenderung.get("entfernen") or [] if i in alt_r]
    for r in aenderung.get("setzen") or []:
        betroffen.append(r)
        if r["id"] in alt_r:
            betroffen.append(alt_r[r["id"]])
    d = diff(bestand, neu, betroffen, ctx)
    return {"titel": titel, "erklaerung": erklaerung, "aenderung": aenderung, "diff": d, "knopf": knopftext(aenderung, bestand), "folgen": folgen(aenderung, bestand),
            "neutral": not d["anzahl"] and not d["events"],
            "vorher": _kennzahl(bestand["regeln"]), "nachher": _kennzahl(neu["regeln"])}


def _befund(ctx, art, stufe, regeln, kurz, titel, gruende, varianten=(), **mehr):
    vid = f"{PRAEFIX}{art}:{'+'.join(regeln)}"
    return {"id": vid, "art": art, "stufe": stufe, "regeln": list(regeln), "kurz": kurz, "titel": titel, "gruende": gruende,
            "varianten": [v for v in varianten if v], "ignoriert": vid in ctx["ignoriert"], **mehr}


def _ohne_muster(r, muster):
    rest = [m for m in r["muster"] if m not in muster]
    return {"setzen": [{**r, "muster": rest}]} if rest else {"entfernen": [r["id"]]}


def _wertetext(r):
    if r["typ"] == "schutz":
        return "Schutz"
    teile = [rg.anzeige(r[a]) for a in ("kurzzeit", "langzeit") if r[a]]
    return " / ".join(teile) if r["typ"] != "event" else rg.anzeige(r["kurzzeit"])


# ---------------------------------------------------------------- Vorrang auflösen (Befund „vorrang“)

def _naechste_id(regeln):
    n = max([int(r["id"][1:]) for r in regeln if r["id"][:1] == "R" and r["id"][1:].isdigit()] or [0])
    return f"R{n + 1:02d}"


def _vorrang_varianten(ru, rs, eids, arten, ctx):
    """ru wird bei eids von rs (anderer, ranghöherer Typ) überstimmt. Drei Wege, ru gelten zu lassen bzw. anzugleichen."""
    bestand = ctx["bestand"]
    werte = {a: ru[a] for a in rg.WERTE}
    out = []
    # a) Ausnahme: entity-Regel mit den Werten von ru – eine schon vom Check angelegte Ausnahme für ru wird erweitert
    #    (keine fremde entity-Regel mit zufällig gleichen Werten, sonst landen Entitäten in einer thematisch anderen Regel)
    gleich = next((r for r in bestand["regeln"] if r["aktiv"] and r["typ"] == "entity" and r["name"].startswith(f"Ausnahme: {ru['name']}")
                   and all(r[a] == werte[a] for a in rg.WERTE)), None)
    if gleich:
        out.append(variante(f"{ru['id']} soll gelten: {len(eids)} Entitäten in {gleich['id']} aufnehmen",
                            {"setzen": [{**gleich, "muster": list(dict.fromkeys(gleich["muster"] + eids))}]}, ctx,
                            f"{gleich['id']} (entity) hat dieselben Werte wie {ru['id']} und Vorrang vor {rs['id']} – keine neue Regel nötig."))
    else:
        rid = _naechste_id(bestand["regeln"])
        neu = {"id": rid, "name": f"Ausnahme: {ru['name']} (vor {rs['id']})", "typ": "entity", "muster": eids, "aktiv": True,
               "notiz": f"Regelwerk-Check: {ru['id']} gilt hier statt {rs['id']}", **werte}
        out.append(variante(f"{ru['id']} soll gelten: neue Regel {rid} (entity) mit den Werten von {ru['id']}",
                            {"setzen": [neu]}, ctx, f"entity hat Vorrang vor {rs['typ']} – nur diese {len(eids)} Entitäten ändern sich."))
    # b) und c) Werte angleichen – wirkt auf alle Treffer der geänderten Regel, die Liste zeigt es
    namen = " und ".join({"kurzzeit": "Kurzzeit", "langzeit": "Langzeit"}[a] for a in arten)
    out.append(variante(f"{rs['id']} übernimmt {namen} von {ru['id']}", {"setzen": [{**rs, **{a: ru[a] for a in arten}}]}, ctx,
                        f"ändert {rs['id']} für alle Entitäten, die sie bestimmt."))
    out.append(variante(f"{ru['id']} übernimmt {namen} von {rs['id']}", {"setzen": [{**ru, **{a: rs[a] for a in arten}}]}, ctx,
                        f"dann gibt es keinen Unterschied mehr; ändert {ru['id']} für alle Entitäten, die sie bestimmt."))
    return out


# ---------------------------------------------------------------- Zusammenlegen (auch für die Teilauswahl im Dialog)

def zusammengelegt(gruppe, name=None):
    """Erste Regel der Gruppe mit allen Mustern (Reihenfolge der Liste, ohne Dopplungen) und vereinten Notizen."""
    erste = gruppe[0]
    muster = list(dict.fromkeys(m for r in gruppe for m in r["muster"]))
    if not name:
        if erste["typ"] == "herkunft":
            liste = ", ".join(muster[:4]) + (f" … (+{len(muster) - 4})" if len(muster) > 4 else "")
            name = f"Integrationen {liste} – {_wertetext(erste)}"
        else:
            name = " + ".join(r["name"] for r in gruppe)
            if len(name) > 80:
                name = f"{erste['name']} (+{len(gruppe) - 1} zusammengelegt)"
    notizen = list(dict.fromkeys(r["notiz"] for r in gruppe if r["notiz"]))
    # Punkt 238: Notizen weglassen, die schon in einer anderen stecken („Empfehlung des RS DB HM“ in „… HM: eine Regel …“)
    notiz = " · ".join(n for n in notizen if not any(n != m and n in m for m in notizen))
    return {**erste, "name": name, "muster": muster, "notiz": notiz}


def zusammenlegen_variante(gruppe, ctx, name=None):
    """Variante „zusammenlegen“: zuerst in die erste Regel; ändert das etwas (eine Regel dazwischen mit anderem Wert), in die letzte."""
    ids = [r["id"] for r in gruppe]
    v = variante(f"{len(gruppe)} Regeln zu {ids[0]} zusammenlegen",
                 {"setzen": [zusammengelegt(gruppe, name)], "entfernen": ids[1:]}, ctx,
                 "Gleicher Typ und gleiche Werte – innerhalb eines Typs zählt nur die Reihenfolge.")
    if v["neutral"] or len(gruppe) < 2:
        return v
    hinten = [gruppe[-1], *gruppe[:-1]]
    w = variante(f"{len(gruppe)} Regeln zu {ids[-1]} zusammenlegen",
                 {"setzen": [{**zusammengelegt(hinten, name), "muster": list(dict.fromkeys(m for r in gruppe for m in r["muster"]))}],
                  "entfernen": ids[:-1]}, ctx,
                 "Gleicher Typ und gleiche Werte; an der Stelle der letzten Regel, weil dazwischen eine Regel mit anderem Wert steht.")
    return w if w["neutral"] else v


# ---------------------------------------------------------------- Befunde

def befunde(pl, bestand, ignoriert=()):
    ents = pl.get("entitaeten", [])
    ctx = {"bestand": bestand, "ents": ents, "events": pl.get("events", []), "ignoriert": set(ignoriert),
           "pkd": int((pl.get("stand") or {}).get("purge_keep_days") or 3650),
           "ids": {e["entity_id"] for e in ents}}
    aktiv = _aktiv(bestand)
    nach_id = {r["id"]: r for r in aktiv}
    stufe = {r["id"]: rg.rang(r) for r in aktiv}
    pos = {r["id"]: i for i, r in enumerate(bestand["regeln"])}
    out = []

    # Gewinne und Überstimmungen je Regel aus dem Probelauf (je Entität und Wert)
    gewinnt, ueberstimmt, von = Counter(), Counter(), {}
    konflikt = {}   # (X, Y) → [Beispiele]: X wird von Y (anderer Typ) mit anderem Wert überstimmt
    for e in ents:
        w = e.get("werte") or {}
        sieger = {(w.get(a) or {}).get("regel") for a in ("kurzzeit", "langzeit")} - {None}
        for s in sieger:
            gewinnt[s] += 1
        unterlegen = {}
        for a in ("kurzzeit", "langzeit"):
            x = w.get(a) or {}
            for u in x.get("ueberstimmt") or []:
                if u["regel"] in sieger or not u.get("regel"):
                    continue
                unterlegen.setdefault(u["regel"], []).append((a, x.get("regel"), x.get("anzeige"), u.get("anzeige")))
        for u, l in unterlegen.items():
            ueberstimmt[u] += 1
            for _a, s, _wa, _wu in l:
                von.setdefault(u, Counter())[s] += 1
            # Rangkonflikt: Sieger ist ein anderer Typ (nicht Schutz, nicht E:) und setzt einen anderen Wert
            ru = nach_id.get(u)
            if not ru or ru["typ"] in ("vielschreiber", "ha_standard", "schutz") or u.startswith("E:"):
                continue
            for a, s, wa, wu in l:
                rs = nach_id.get(s)
                # entity-Regeln (auch Ausnahmen aus diesem Check) sind bewusste Einzelentscheidungen – kein Konflikt
                if not rs or rs["typ"] in ("schutz", "ha_standard", "entity") or s.startswith("E:") or rs["typ"] == ru["typ"] or wa == wu:
                    continue
                konflikt.setdefault((u, s), {})[e["entity_id"]] = (a, wa, wu, e["rows_24h"] or 0)
    # Event-Regeln: Gewinne und Überstimmungen je Event-Typ
    for t in ctx["events"]:
        x = rg.event_regel(bestand, t["event_type"], ctx["pkd"])
        if x["regel"]:
            gewinnt[x["regel"]] += 1
        for u in x["ueberstimmt"]:
            if u["regel"] != rg.HA_STANDARD_ID:
                ueberstimmt[u["regel"]] += 1
                von.setdefault(u["regel"], Counter())[x["regel"]] += 1

    def text_von(rid, n=4):
        return ", ".join(f"{s} ({c})" for s, c in von.get(rid, Counter()).most_common(n))

    # gleiche Muster in aktiven Regeln desselben Typs: [(frühere, spätere, muster)]
    doppelt = []
    for t in DOPPELT_TYPEN:
        l = sorted((r for r in aktiv if r["typ"] == t), key=lambda r: pos[r["id"]])
        for i, a in enumerate(l):
            for b in l[i + 1:]:
                gemeinsam = [m for m in b["muster"] if m in a["muster"]]
                if gemeinsam:
                    doppelt.append((a, b, gemeinsam))

    # 1. wirkungslos: trifft etwas, gewinnt nirgends
    wirkungslos = set()
    for r in aktiv:
        if r["typ"] == "ha_standard" or gewinnt[r["id"]] or not ueberstimmt[r["id"]]:
            continue
        wirkungslos.add(r["id"])
        var = []
        for a, b, gem in doppelt:
            if b["id"] == r["id"]:
                var.append(variante(f"{', '.join(gem)} aus {a['id']} entfernen – {r['id']} wirkt dann",
                                    _ohne_muster(a, gem), ctx,
                                    f"{a['id']} steht weiter vorn und enthält dasselbe Muster – deshalb kommt {r['id']} nie zum Zug."))
        var.append(variante(f"{r['id']} entfernen", {"entfernen": [r["id"]]}, ctx, "Die Regel bewirkt heute nichts."))
        einheit = "Event-Typen" if r["typ"] == "event" else "Entitäten"
        out.append(_befund(ctx, "wirkungslos", "rot", [r["id"]], "unwirksam", f"Regel {r['id']}: unwirksam",
                           [f"trifft {ueberstimmt[r['id']]} {einheit}, ist aber bei allen unwirksam – sie setzt nirgends einen Wert",
                            f"Vorrang haben: {text_von(r['id'])}"], var))

    # 2. gleiche Muster in zwei Regeln desselben Typs (soweit nicht schon als wirkungslos gemeldet)
    for a, b, gem in doppelt:
        if b["id"] in wirkungslos:
            continue
        out.append(_befund(ctx, "doppelt", "gelb", [a["id"], b["id"]], "Muster doppelt",
                           f"Regeln {a['id']} und {b['id']}: Muster doppelt",
                           [f"{', '.join(gem)} steht in beiden Regeln (Typ {a['typ']}); für diese Treffer gilt {a['id']} – sie steht weiter vorn"],
                           [variante(f"{', '.join(gem)} aus {b['id']} entfernen", _ohne_muster(b, gem), ctx,
                                     f"Bei {b['id']} wirkt das Muster nur, wo {a['id']} einen Wert nicht setzt."),
                            variante(f"{', '.join(gem)} aus {a['id']} entfernen", _ohne_muster(a, gem), ctx,
                                     f"Dann gelten für diese Treffer die Werte von {b['id']}.")]))

    # 3. Vorrang zwischen Typen mit anderem Wert – auflösen, wie der Nutzer es will (keine Vorauswahl):
    #    Ausnahme (entity-Regel mit den Werten der unterlegenen Regel, bestehende gleichwertige wird erweitert) oder Werte angleichen
    for (u, s), betr in sorted(konflikt.items(), key=lambda kv: -len(kv[1])):
        ru, rs = nach_id[u], nach_id[s]
        bsp = sorted(betr.items(), key=lambda kv: -kv[1][3])
        arten = sorted({a for a, *_r in betr.values()})
        n_ = f"{len(betr)} {'Entität' if len(betr) == 1 else 'Entitäten'}"
        out.append(_befund(ctx, "vorrang", "rot", [u, s], "teilweise unwirksam",
                           f"Regel {u}: bei {n_} unwirksam – {s} hat Vorrang",
                           [f"{u} ist bei {n_} unwirksam: dort gilt {s} mit anderem Wert, weil Typ {rs['typ']} Vorrang vor Typ {ru['typ']} hat "
                            f"(der Typ bestimmt den Vorrang, nicht die Reihenfolge). Bei allen übrigen Entitäten wirkt {u} weiter.",
                            f"Soll {s} dort gelten, „bewusst so“ wählen – sonst einen Vorschlag."],
                           _vorrang_varianten(ru, rs, [eid for eid, _x in bsp], arten, ctx), wahl_pflicht=True,
                           beispiele=[{"entity_id": eid, "wert": {"kurzzeit": "Kurzzeit", "langzeit": "Langzeit"}[a],
                                       "gilt": wa, "statt": wu, "rows_24h": n} for eid, (a, wa, wu, n) in bsp[:BEISPIELE]]))

    # 4. tote Muster: genaue IDs, die es in der Analyse nicht (mehr) gibt
    for r in aktiv:
        if r["typ"] not in ("entity", "schutz"):
            continue
        tot = [m for m in r["muster"] if not _platzhalter(m) and m not in ctx["ids"]]
        if tot:
            out.append(_befund(ctx, "tot", "grau", [r["id"]], "tote Muster", f"Regel {r['id']}: {len(tot)} Muster ohne Entität",
                               [f"{len(tot)} von {len(r['muster'])} Entity-IDs gibt es in der letzten Analyse nicht: {', '.join(tot[:6])}{' …' if len(tot) > 6 else ''}"],
                               [variante(f"{len(tot)} Muster aus {r['id']} entfernen" if len(tot) < len(r["muster"]) else f"{r['id']} entfernen",
                                         _ohne_muster(r, tot), ctx)]))

    # 5. zusammenlegen: gleicher Typ, gleiche Werte
    gruppen = {}
    for r in aktiv:
        if r["typ"] in ZUSAMMEN_TYPEN and not r["id"].startswith(("E:",)) and r["id"] not in wirkungslos and not _ausnahme(r):
            gruppen.setdefault((r["typ"], r["kurzzeit"], r["attribute"], r["langzeit"]), []).append(r)
    for (typ, *_w), l in gruppen.items():
        if len(l) < 2:
            continue
        l.sort(key=lambda r: pos[r["id"]])
        v = zusammenlegen_variante(l, ctx)
        out.append(_befund(ctx, "zusammen", "gelb", [r["id"] for r in l], f"zusammenlegen ({len(l)})",
                           f"Regeln {', '.join(r['id'] for r in l)}: zusammenlegen (Typ {typ}, gleiche Werte {_wertetext(l[0])})",
                           [f"{', '.join(r['id'] for r in l)}: gleicher Typ, gleiche Werte – eine Regel mit allen Mustern genügt",
                            "Im Dialog lässt sich die Auswahl einschränken und der Name ändern."],
                           [v], name_vorschlag=zusammengelegt(l)["name"]))

    # 6. lange Listen genauer IDs → Platzhalter
    for r in aktiv:
        if r["typ"] not in ("entity", "schutz") or r["id"].startswith("E:") or _ausnahme(r) or len(r["muster"]) < MIN_PLATZHALTER:
            continue
        if any(_platzhalter(m) for m in r["muster"]):
            continue
        vd = optimierung.verdichte(r["muster"], pl)
        neu = list(vd["muster"]) + ([m for m in vd["ausserhalb"] if m in ctx["ids"]] if r["typ"] == "schutz" else [])
        if not any(_platzhalter(m) for m in neu) or len(neu) >= len(r["muster"]):
            continue
        typ = "glob" if r["typ"] == "entity" else "schutz"
        gr = [f"{len(r['muster'])} Entity-IDs lassen sich als {len(neu)} Muster schreiben: {', '.join(m for m in neu if _platzhalter(m))}",
              "Ein Platzhalter trifft künftig auch neue Entitäten, die dazu passen" + (" – sie werden dann mitgeschützt." if typ == "schutz" else ".")]
        if typ == "glob":
            gr.append("Aus entity wird glob: Rang 3 statt 2, Zähler und verwaiste Entitäten trifft glob nicht.")
        out.append(_befund(ctx, "platzhalter", "gelb", [r["id"]], "✱ Platzhalter", f"Regel {r['id']}: Liste als Platzhalter",
                           gr, [variante(f"{r['id']}: {len(r['muster'])} IDs → {len(neu)} Muster", {"setzen": [{**r, "typ": typ, "muster": neu}]}, ctx)]))

    # 7. Event-Regel mit langer Liste genauer Namen → „*“
    for r in aktiv:
        if r["typ"] != "event" or len(r["muster"]) < MIN_EVENT_LISTE or any(_platzhalter(m) for m in r["muster"]):
            continue
        out.append(_befund(ctx, "ev_platzhalter", "gelb", [r["id"]], "✱ Platzhalter", f"Regel {r['id']}: alle Event-Typen per *",
                           [f"{len(r['muster'])} Event-Typen einzeln aufgezählt; * erfasst auch alle übrigen und künftige",
                            "Genaue Namen in anderen Event-Regeln behalten ihren Vorrang (genauer Name vor Platzhalter)."],
                           [variante(f"{r['id']}: Muster *", {"setzen": [{**r, "muster": ["*"]}]}, ctx)]))

    # 8. überflüssig: ohne die Regel gälte überall dasselbe – auch ohne Vielschreiber-Regeln (deren Treffer schwanken)
    ohne_viel = {**bestand, "regeln": [r for r in bestand["regeln"] if r["typ"] != "vielschreiber"]}
    for r in aktiv:
        if r["typ"] in ("ha_standard", "schutz", "vielschreiber") or r["id"] in wirkungslos or not gewinnt[r["id"]]:
            continue
        v = variante(f"{r['id']} entfernen", {"entfernen": [r["id"]]}, ctx)
        if not v["neutral"]:
            continue
        d2 = diff(ohne_viel, _anwenden(ohne_viel, {"entfernen": [r["id"]]}), [r], ctx)
        if d2["anzahl"] or d2["events"]:
            continue
        out.append(_befund(ctx, "ueberfluessig", "grau", [r["id"]], "überflüssig", f"Regel {r['id']}: überflüssig",
                           [f"gewinnt bei {gewinnt[r['id']]}, aber ohne sie gälte dort dasselbe (über die nächste Regel bzw. den Standard)"], [v]))

    # 9. meist überstimmt (nur Hinweis)
    for r in aktiv:
        g, u = gewinnt[r["id"]], ueberstimmt[r["id"]]
        if r["typ"] == "ha_standard" or r["id"] in wirkungslos or not g or u < UEBERSTIMMT_MIN or u < UEBERSTIMMT_FAKTOR * g:
            continue
        out.append(_befund(ctx, "ueberstimmt", "grau", [r["id"]], "meist überstimmt", f"Regel {r['id']}: meist überstimmt",
                           [f"gewinnt bei {g}, überstimmt bei {u}", f"Vorrang haben: {text_von(r['id'])}"]))

    out.sort(key=lambda b: (STUFE_RANG[b["stufe"]], min(pos.get(i, 999) for i in b["regeln"])))
    return {"befunde": out, "zusammenfassung": zusammenfassung(out, bestand)}


def zusammenfassung(liste, bestand):
    """Zähler je Stufe und Regeln/Muster, wenn alle nicht ausgeblendeten Vorschläge (erste Variante) übernommen würden –
    nacheinander, Vorschläge zu schon geänderten Regeln entfallen."""
    n = Counter(b["stufe"] for b in liste if not b["ignoriert"])
    daten, beruehrt = bestand, set()
    for b in liste:
        # ohne Befunde, bei denen der Nutzer erst wählen muss (Vorrang) – dort gibt es keine Standard-Variante
        if b["ignoriert"] or not b["varianten"] or b.get("wahl_pflicht") or b["stufe"] == "grau" and b["art"] != "tot":
            continue
        a = b["varianten"][0]["aenderung"]
        ids = set(a.get("entfernen") or []) | {r["id"] for r in a.get("setzen") or []}
        if ids & beruehrt:
            continue
        beruehrt |= ids
        daten = _anwenden(daten, a)
    return {"rot": n["rot"], "gelb": n["gelb"], "grau": n["grau"], "ausgeblendet": sum(1 for b in liste if b["ignoriert"]),
            "vorher": _kennzahl(bestand["regeln"]), "nachher": _kennzahl(daten["regeln"])}


def berechne(pl, bestand, ignoriert, schluessel):
    """befunde() mit Zwischenspeicher je Stand (Analyse, Regeln, Ignorier-Liste)."""
    if _CACHE["schl"] != schluessel:
        _CACHE.update(schl=schluessel, daten=befunde(pl, bestand, ignoriert))
    return _CACHE["daten"]


def finde(daten, vid):
    return next((b for b in daten["befunde"] if b["id"] == vid), None)


def ctx_fuer(pl, bestand):
    ents = pl.get("entitaeten", [])
    return {"bestand": bestand, "ents": ents, "events": pl.get("events", []), "ignoriert": set(),
            "pkd": int((pl.get("stand") or {}).get("purge_keep_days") or 3650), "ids": {e["entity_id"] for e in ents}}


def variante_fuer(pl, bestand, befund, nr=0, auswahl=None, name=None):
    """Variante eines Befunds; bei „zusammen“ für eine Teilauswahl (mindestens zwei Regeln) und mit eigenem Namen."""
    if befund["art"] == "zusammen" and (auswahl or name):
        ctx = ctx_fuer(pl, bestand)
        nach_id = {r["id"]: r for r in bestand["regeln"]}
        ids = [i for i in befund["regeln"] if not auswahl or i in auswahl]
        if len(ids) < 2:
            raise ValueError("Zum Zusammenlegen mindestens zwei Regeln wählen")
        return zusammenlegen_variante([nach_id[i] for i in ids], ctx, (name or "").strip() or None)
    if not befund["varianten"]:
        raise ValueError("Zu diesem Befund gibt es keine Änderung")
    return befund["varianten"][min(max(0, nr), len(befund["varianten"]) - 1)]


def anwenden(bestand, v):
    return _anwenden(bestand, v["aenderung"])
