"""Punkt 305: Reiter „Analyse“ – wer treibt die Datenmenge? Integration → Gerät → Entität mit Pareto und Erstauftreten.

Rechnet nur auf dem Ergebnis des Probelaufs (Analyse-Cache); die Recorder-DB wird nicht abgefragt.
Sicht „daten“: alle (Standard), kurz = States + 5-Min-Statistik + Attribute (Kurzzeit-Vorhaltezeit), lang = Stundenstatistik (Langzeit-Vorhaltezeit).
Maß „zuwachs“ = Zeilen je Tag der Sicht (States: rows_24h = Ø der letzten 7 vollen Kalendertage, geteilt durch die vorhandenen Tage; Statistik:
feste Rate je Tag), Maß „bestand“ = Zeilen heute. Erstauftreten (Filter und Kohorten) = lts_first_ts (erste Langzeitstatistik);
Entitäten ohne Langzeitstatistik haben keinen Wert („unbekannt“).
"""
from datetime import date, datetime, timedelta

import probelauf

OHNE = "-"                      # Schlüssel für „(ohne Integration)“ bzw. „(ohne Gerät)“
MAX_ENTITAETEN = 500            # Entitäten je Gerät in einer Antwort
MARKEN = (50, 80, 90)           # Prozent für die Pareto-Marken
SICHTEN = ("alle", "kurz", "lang")


def _tag(ts):
    return datetime.fromtimestamp(ts).date()


def _montag(d):
    return d - timedelta(days=d.weekday())


def _hat_statistik(e):
    return bool(e.get("lts_first_ts") or e.get("lts_rows") or e.get("kurz_rows"))


def _kurz_erst(e):
    """Erste noch vorhandene Zeile der Kurzzeit-Daten (States und 5-Min-Statistik): das frühere von beiden, None ohne Zeilen."""
    t = [x for x in (e.get("st_first_ts"), e.get("kurz_first_ts")) if x]
    return min(t) if t else None


def werte(e, daten):
    """Zuwachs je Tag und Bestand der Entität in der Sicht: z/b in Zeilen, zb/bb in Bytes samt Indizes (gemessene Zeilengrößen aus probelauf).
    Die Statistik schreibt bei aktiven Entitäten mit Statistik eine feste Rate je Tag (5-Min: 288, Stunde: 24), unabhängig von den States."""
    rows, tag = e.get("rows") or 0, e.get("rows_24h") or 0
    kurz, lang = e.get("kurz_rows") or 0, e.get("lts_rows") or 0
    stat = e.get("status") == "aktiv" and _hat_statistik(e)
    b_attr = probelauf.B_ATTR_FALLBACK * probelauf.B_ATTR_FAKTOR
    attr_best = e["attr_bytes"] if e.get("attr_bytes") is not None else rows * b_attr
    attr_zu = e["attr_zuwachs_tag"] if e.get("attr_zuwachs_tag") is not None else tag * b_attr
    kurz_zb = tag * probelauf.B_STATE + attr_zu + (probelauf.KURZ_PRO_TAG * probelauf.B_KURZ if stat else 0)
    kurz_bb = rows * probelauf.B_STATE + kurz * probelauf.B_KURZ + attr_best
    lang_zb = probelauf.LTS_PRO_TAG * probelauf.B_LTS if stat else 0
    lang_bb = lang * probelauf.B_LTS
    if daten == "kurz":
        return {"z": tag + (probelauf.KURZ_PRO_TAG if stat else 0), "b": rows + kurz, "zb": kurz_zb, "bb": kurz_bb}
    if daten == "lang":
        return {"z": probelauf.LTS_PRO_TAG if stat else 0, "b": lang, "zb": lang_zb, "bb": lang_bb}
    return {"z": tag, "b": rows, "zb": kurz_zb + lang_zb, "bb": kurz_bb + lang_bb}


def _in_sicht(e, daten):
    """Entitäten ohne Daten in der Sicht bleiben draußen (z. B. ohne Langzeitstatistik in der Sicht „lang“)."""
    if daten == "kurz":
        return bool(e.get("rows") or e.get("kurz_rows"))
    if daten == "lang":
        return bool(e.get("lts_rows") or _hat_statistik(e))
    return True


def _geraetename(liste):
    for e, _w in liste:
        if e.get("herkunft_art") == "geraet" and e.get("herkunft"):
            return e["herkunft"]
    return None


def _monat_plus(d):
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def _kohorten(paare, mass):
    """Erstauftreten nach Alter abgestuft: diese Woche je Tag, bis 8 Wochen zurück je Kalenderwoche, bis ein Jahr zurück je Kalendermonat, davor je Jahr;
    nur Zeiträume mit Entitäten, dazu „unbekannt“. Die Grenzen der Zeiträume überlappen nicht (von inklusive, bis exklusive), damit ein Klick
    genau diese Entitäten filtert; ein Zeitraum an einer Stufengrenze wird dafür gekürzt. Jeweils mit Anteil am Maß."""
    heute = date.today()
    ein_tag = timedelta(days=1)
    # Stufengrenzen an Kalendergrenzen: Tage ab Montag dieser Woche, volle Wochen bis 8 Wochen zurück, volle Monate bis ein Jahr zurück
    l0, l1 = _montag(heute), _montag(heute - timedelta(days=56))
    j = heute - timedelta(days=365)
    l2 = date(j.year, j.month, 1)
    gruppen = {}
    for e, w in paare:
        ts = e.get("lts_first_ts")
        if not ts:
            schl, art, von, bis = "unbekannt", "unbekannt", None, None
        else:
            d = _tag(ts)
            if d >= l0:
                art, von, bis = "tag", d, d + ein_tag
            elif d >= l1:
                m = _montag(d)
                art, von, bis = "woche", max(m, l1), min(m + timedelta(days=7), l0)
            elif d >= l2:
                m1 = d.replace(day=1)
                art, von, bis = "monat", max(m1, l2), min(_monat_plus(m1), l1)
            else:
                art, von, bis = "jahr", date(d.year, 1, 1), min(date(d.year + 1, 1, 1), l2)
            schl = f"{von.isoformat()}_{bis.isoformat()}"
        g = gruppen.setdefault(schl, {"schluessel": schl, "art": art, "von": von.isoformat() if von else None, "bis": bis.isoformat() if bis else None,
                                      "bis_inkl": (bis - ein_tag).isoformat() if bis else None, "entitaeten": 0, "zuwachs": 0, "bestand": 0})
        g["entitaeten"] += 1
        g["zuwachs"] += w["z"]
        g["bestand"] += w["b"]
    gesamt = sum(g[mass] for g in gruppen.values()) or 1
    reihe = [gruppen[k] for k in sorted(k for k in gruppen if k != "unbekannt")] + ([gruppen["unbekannt"]] if "unbekannt" in gruppen else [])
    for g in reihe:
        g["anteil"] = round(100 * g[mass] / gesamt, 1)
    return reihe


def _pareto(paare, mass):
    """Kurve über alle Entitäten, absteigend nach dem Maß: Stützpunkte [Rang, kumulierter Anteil %] und die Ränge der Marken."""
    wert = sorted((w["z"] if mass == "zuwachs" else w["b"] for _e, w in paare), reverse=True)
    summe = sum(wert)
    if not summe:
        return {"punkte": [], "marken": {}, "mit_wert": 0, "summe": 0}
    kum, lauf, marken = [], 0, {}
    for i, x in enumerate(wert, 1):
        lauf += x
        kum.append(lauf)
        for m in MARKEN:
            if m not in marken and lauf * 100 >= summe * m:
                marken[m] = i
    # Stützpunkte: die ersten 20 Ränge einzeln, danach geometrisch (≈ 8 % Schritte), Ende immer dabei
    idx, i = set(range(1, min(20, len(wert)) + 1)), 20.0
    while i < len(wert):
        i *= 1.08
        idx.add(min(len(wert), int(round(i))))
    idx.add(len(wert))
    return {"punkte": [[i, round(100 * kum[i - 1] / summe, 2)] for i in sorted(idx)], "marken": {str(m): r for m, r in marken.items()},
            "mit_wert": sum(1 for x in wert if x), "summe": summe}


def _knoten(schluessel, name, paare, mass, gesamt):
    ts = [e["lts_first_ts"] for e, _w in paare if e.get("lts_first_ts")]
    ks = [t for t in (_kurz_erst(e) for e, _w in paare) if t]
    z, b = sum(w["z"] for _e, w in paare), sum(w["b"] for _e, w in paare)
    m = z if mass == "zuwachs" else b
    return {"schluessel": schluessel, "name": name, "entitaeten": len(paare), "zuwachs": z, "bestand": b,
            "zuwachs_bytes": round(sum(w["zb"] for _e, w in paare)), "bestand_bytes": round(sum(w["bb"] for _e, w in paare)),
            "anteil": round(100 * m / gesamt, 2) if gesamt else 0, "_m": m,
            "mit_regel": sum(1 for e, _w in paare if any(v.get("regel") for v in (e.get("werte") or {}).values())),
            "geschuetzt": sum(1 for e, _w in paare if e.get("geschuetzt")),
            "erst": min(ts) if ts else None, "juengst": max(ts) if ts else None,
            "unbekannt": sum(1 for e, _w in paare if not e.get("lts_first_ts")),
            "erst_k": min(ks) if ks else None, "juengst_k": max(ks) if ks else None, "unbekannt_k": len(paare) - len(ks)}


def _sortiert(knoten):
    knoten.sort(key=lambda k: (-k["_m"], k["name"].lower()))
    for k in knoten:
        del k["_m"]
    return knoten


def _zeile(e, w, mass, gesamt):
    m = w["z"] if mass == "zuwachs" else w["b"]
    k = (e.get("werte") or {}).get("kurzzeit") or {}
    return {"entity_id": e["entity_id"], "status": e["status"], "zuwachs": w["z"], "bestand": w["b"],
            "zuwachs_bytes": round(w["zb"]), "bestand_bytes": round(w["bb"]), "anteil": round(100 * m / gesamt, 2) if gesamt else 0, "_m": m,
            "lts_first_ts": e.get("lts_first_ts"), "kurz_first_ts": _kurz_erst(e), "kurzzeit": k.get("anzeige") or "", "regel": k.get("regel") or "",
            "geschuetzt": bool(e.get("geschuetzt")), "klasse": e.get("device_class") or ""}


def baue(r, q):
    """Antwort für /api/verursacher. Parameter: daten=alle|kurz|lang, mass=zuwachs|bestand, plattform, geraet (OHNE = „-“),
    von/bis (JJJJ-MM-TT, bis exklusiv), kohorte=unbekannt. Ohne plattform (= Integration der Entitäts-Registry) kommen die Integrationen, mit plattform die Geräte,
    mit geraet die Entitäten."""
    daten = q.get("daten") if q.get("daten") in SICHTEN else "alle"
    mass = "bestand" if q.get("mass") == "bestand" else "zuwachs"
    von = date.fromisoformat(q["von"]) if q.get("von") else None
    bis = date.fromisoformat(q["bis"]) if q.get("bis") else None
    nur_unbekannt = q.get("kohorte") == "unbekannt"
    alle = [(e, werte(e, daten)) for e in r["entitaeten"] if _in_sicht(e, daten)]

    def im_fenster(e):
        ts = e.get("lts_first_ts")
        if nur_unbekannt:
            return not ts
        if von is None and bis is None:
            return True
        if not ts:
            return False
        d = _tag(ts)
        return (von is None or d >= von) and (bis is None or d < bis)

    paare = [(e, w) for e, w in alle if im_fenster(e)]
    gesamt = sum(w["z"] if mass == "zuwachs" else w["b"] for _e, w in paare)
    kopf = _knoten("", "alle", paare, mass, gesamt)
    for k in ("_m", "schluessel", "name"):
        kopf.pop(k)

    plat, geraet = q.get("plattform"), q.get("geraet")
    pfad = {}
    if plat is None:
        ebene = "plattform"
        gruppen = {}
        for p in paare:
            gruppen.setdefault(p[0].get("plattform") or OHNE, []).append(p)
        knoten = [_knoten(k, "(ohne Integration)" if k == OHNE else k, v, mass, gesamt) for k, v in gruppen.items()]
    else:
        pfad["plattform"] = "(ohne Integration)" if plat == OHNE else plat
        in_plat = [p for p in paare if (p[0].get("plattform") or OHNE) == plat]
        if geraet is None:
            ebene = "geraet"
            gruppen = {}
            for p in in_plat:
                gruppen.setdefault(p[0].get("device_id") or OHNE, []).append(p)
            knoten = [_knoten(k, "(ohne Gerät)" if k == OHNE else (_geraetename(v) or k[:8]), v, mass, gesamt) for k, v in gruppen.items()]
        else:
            ebene = "entitaet"
            liste = [p for p in in_plat if (p[0].get("device_id") or OHNE) == geraet]
            pfad["geraet"] = "(ohne Gerät)" if geraet == OHNE else (_geraetename(liste) or geraet[:8])
            knoten = [_zeile(e, w, mass, gesamt) for e, w in liste]
    insgesamt = len(knoten)
    if ebene == "entitaet":
        knoten.sort(key=lambda k: (-k["_m"], k["entity_id"]))
        for k in knoten:
            del k["_m"]
        knoten = knoten[:MAX_ENTITAETEN]
    else:
        knoten = _sortiert(knoten)
    return {"stand": r["stand"]["analyse_zeit"], "daten": daten, "mass": mass, "ebene": ebene, "pfad": pfad, "knoten": knoten,
            "knoten_gesamt": insgesamt, "gesamt": kopf, "pareto": _pareto(paare, mass), "kohorten": _kohorten(alle, mass),
            "filter": {"von": q.get("von") or None, "bis": q.get("bis") or None, "unbekannt": nur_unbekannt},
            "statistik_ab": min((e["lts_first_ts"] for e, _w in alle if e.get("lts_first_ts")), default=None),
            "zeilengroessen": r["stand"].get("zeilengroessen")}
