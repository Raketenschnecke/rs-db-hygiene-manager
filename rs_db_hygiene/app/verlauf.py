"""Punkt 308: Reiter Dashboard › Zeitverlauf – wann ist wie viel dazugekommen, und wer war es?

Rechnet auf den Tageswerten der Zuwachs-Statistik (zuwachs.db, je Entität und Kalendertag) und ordnet die Entitäten über das Ergebnis des
Probelaufs (Analyse-Cache) Integration → Gerät → Entität zu. Die Recorder-DB wird nicht abgefragt.

Zeitachse: Jahr → Monat → Tag (Klick auf einen Balken zoomt hinein). Dazu die Aufschlüsselung des gezeigten Zeitraums nach Kategorie
(Integration → Gerät → Entität); sie ist zugleich der Filter für die Balken. Maß: neue Zeilen oder geschätzter Platz (gemessene Zeilengrößen).
Vorhaltezeit wie im Donut: alle, kurz (States, 5-Min, Attribute), lang (Stundenstatistik). Events gehören keiner Entität und stehen nur in „alle“,
als eigene Serie.
"""
import datetime
import sqlite3
import statistics

import probelauf
import verursacher
import zuwachs

AUFLOESUNGEN = ("jahr", "monat", "woche", "tag")
SPALTEN = {"alle": ("states", "kurz", "lts", "attr", "events"), "kurz": ("states", "kurz", "attr"), "lang": ("lts",)}
TABELLEN = ("states", "kurz", "lts", "attr", "events")
EREIGNIS = "~ev"                  # Schlüssel der Event-Serie
REST = "~rest"                    # Schlüssel der zusammengefassten kleinen Serien
MAX_SERIEN = 7                    # so viele Serien einzeln, der Rest wird „übrige“
MAX_TAGE = 400                    # größte Spanne bei Tagesauflösung
MAX_KNOTEN = 60                   # Zeilen der Aufschlüsselung (Entitäten: mehr)
MAX_ENTITAETEN = 300
AUFFAELLIG_FAKTOR = 2.5           # Tag über dem 2,5-fachen des Tagesmedians
MIN_TAGE_AUFFAELLIG = 7           # unter so vielen Tagen im Bild gibt es keine Markierung
TAG = datetime.date(1970, 1, 1)

_CACHE = {}                       # (Stand der Zuwachsdatei, Stand der Analyse, Parameter) → Antwort
_CACHE_MAX = 32


def _datum(tag):
    return TAG + datetime.timedelta(days=tag)


def _tageszahl(d):
    return (d - TAG).days


def _gewichte():
    """Bytes je Zeile (inkl. Indizes) je Tabelle – zur Laufzeit, damit gemessene Zeilengrößen (probelauf.messwerte_setzen) gelten."""
    return {"states": probelauf.B_STATE, "kurz": probelauf.B_KURZ, "lts": probelauf.B_LTS,
            "attr": probelauf.B_ATTR_FALLBACK * probelauf.B_ATTR_FAKTOR, "events": probelauf.B_EVENT}


def _montag(d):
    return d - datetime.timedelta(days=d.weekday())


def _zeiteimer(von, bis, aufl):
    """Alle Zeiträume zwischen von und bis (Datum, beide inklusive): [(Schlüssel, erster Tag, letzter Tag)]."""
    out = []
    if aufl == "tag":
        d = von
        while d <= bis:
            out.append((d.isoformat(), d, d))
            d += datetime.timedelta(days=1)
    elif aufl == "woche":
        d = _montag(von)
        while d <= bis:
            out.append((d.isoformat(), d, d + datetime.timedelta(days=6)))
            d += datetime.timedelta(days=7)
    elif aufl == "monat":
        j, m = von.year, von.month
        while (j, m) <= (bis.year, bis.month):
            erst = datetime.date(j, m, 1)
            nxt = datetime.date(j + (m == 12), m % 12 + 1, 1)
            out.append((f"{j}-{m:02d}", erst, nxt - datetime.timedelta(days=1)))
            j, m = nxt.year, nxt.month
    else:
        for j in range(von.year, bis.year + 1):
            out.append((str(j), datetime.date(j, 1, 1), datetime.date(j, 12, 31)))
    return out


def _abdeckung(con):
    info = dict(con.execute("SELECT schluessel, wert FROM info"))
    abd = {q: (int(info[f"{q}_von"]), int(info[f"{q}_bis"])) for q in zuwachs.QUELLEN
           if f"{q}_von" in info and f"{q}_bis" in info and int(info[f"{q}_von"]) <= int(info[f"{q}_bis"])}
    return abd, float(info.get("stand") or 0)


def _zuordnung(r):
    """entity_id → (Integration, Gerät) und Gerätenamen aus dem Probelauf; unbekannte Entitäten (inzwischen gelöscht) fallen unter „-“."""
    ziel, namen = {}, {}
    for e in r["entitaeten"]:
        ziel[e["entity_id"]] = (e.get("plattform") or verursacher.OHNE, e.get("device_id") or verursacher.OHNE)
        if e.get("herkunft_art") == "geraet" and e.get("herkunft") and e.get("device_id"):
            namen.setdefault(e["device_id"], e["herkunft"])
    return ziel, namen


def _auto_aufl(von, bis):
    return "jahr" if (bis.year - von.year) * 12 + bis.month - von.month >= 24 else "monat"


def baue(r, zuwachs_pfad, q):
    """Antwort für /api/verlauf. Parameter: von/bis (JJJJ-MM-TT, beide inklusive), aufl=jahr|monat|tag, mass=zeilen|platz,
    daten=alle|kurz|lang, grp=kategorie|tabelle, plattform, geraet (verursacher.OHNE = „-“), sel (Schlüssel eines Zeitraums im Bild:
    die Aufschlüsselung zeigt dann nur ihn). Ohne von/bis: alles, was die Zuwachs-Statistik hat. ValueError bei ungültigen Parametern."""
    daten = q.get("daten") if q.get("daten") in SPALTEN else "alle"
    mass = "platz" if q.get("mass") == "platz" else "zeilen"
    grp = "tabelle" if q.get("grp") == "tabelle" else "kategorie"
    plat, geraet = q.get("plattform"), q.get("geraet")
    if geraet is not None and plat is None:
        raise ValueError("geraet braucht plattform")
    spalten = SPALTEN[daten]

    con = sqlite3.connect(f"file:{zuwachs_pfad}?mode=ro", uri=True, timeout=60)
    try:
        abd, zw_stand = _abdeckung(con)
        quellen = [s for s in spalten if s in abd]
        if not quellen:
            return {"fehler": "Noch keine Zuwachs-Statistik für diese Vorhaltezeit vorhanden", "buckets": []}
        # Die Quellen beginnen an verschiedenen Tagen (Stundenstatistik oft Monate früher als States). Gezeigt wird erst ab dem Tag, an dem alle
        # Quellen der Vorhaltezeit da sind – sonst sähe das Wachstum früher kleiner aus, als es war. Die Attribute beginnen später und zählen nicht mit.
        basis = [abd[s] for s in quellen if s != "attr"] or [abd[s] for s in quellen]
        erster = max(v[0] for v in basis)
        letzter = max(v[1] for v in basis)
        fruehester = min(abd[s][0] for s in quellen)
        von = max(datetime.date.fromisoformat(q["von"]), _datum(erster)) if q.get("von") else _datum(erster)
        bis = min(datetime.date.fromisoformat(q["bis"]), _datum(letzter)) if q.get("bis") else _datum(letzter)
        if bis < von:
            von, bis = _datum(erster), _datum(letzter)
        aufl = q.get("aufl") if q.get("aufl") in AUFLOESUNGEN else _auto_aufl(von, bis)
        if aufl == "tag" and (bis - von).days + 1 > MAX_TAGE:
            raise ValueError("Tagesauflösung nur bis zu %d Tagen" % MAX_TAGE)

        schluessel = (zw_stand, r["stand"]["analyse_zeit"], von, bis, aufl, mass, daten, grp, plat, geraet, q.get("sel"))
        if schluessel in _CACHE:
            return _CACHE[schluessel]

        # Woche: Tageszahl des Montags (der 1.1.1970 war ein Donnerstag → Montag = Tag − (Tag + 3) % 7); ganze Wochen abfragen
        bk = {"jahr": "strftime('%Y', tag * 86400, 'unixepoch')", "monat": "strftime('%Y-%m', tag * 86400, 'unixepoch')", "woche": "(tag - (tag + 3) % 7)", "tag": "tag"}[aufl]
        ab, bisq = (_montag(von), _montag(bis) + datetime.timedelta(days=6)) if aufl == "woche" else (von, bis)
        zeilen = con.execute(
            f"SELECT entity_id, {bk}, SUM(states), SUM(kurz), SUM(lts), SUM(attr), SUM(events) FROM tage "
            "WHERE tag BETWEEN ? AND ? GROUP BY entity_id, 2", (_tageszahl(ab), _tageszahl(bisq))).fetchall()
    finally:
        con.close()

    gew = _gewichte()
    ziel, namen = _zuordnung(r)
    slots = verursacher._farben(r)
    cols = {"states": 2, "kurz": 3, "lts": 4, "attr": 5, "events": 6}

    def teil(z, spalte):
        n = z[cols[spalte]] or 0
        return n * gew[spalte] if mass == "platz" else n

    def ebene_von(plat, geraet):
        return "plattform" if plat is None else ("geraet" if geraet is None else "entitaet")

    ebene = ebene_von(plat, geraet)
    eimer = _zeiteimer(von, bis, aufl)
    sel = q.get("sel") if q.get("sel") in {k for k, _a, _b in eimer} else None

    # je Zeile: Kategorie-Schlüssel auf der aktuellen Ebene (None = gehört nicht zur Auswahl), Name des Elternknotens für die Farbe
    def kategorie(eid):
        if eid.startswith("ev:"):
            return (EREIGNIS, None) if plat is None and daten == "alle" else (None, None)
        p, d = ziel.get(eid, (verursacher.OHNE, verursacher.OHNE))
        if plat is None:
            return p, p
        if p != plat:
            return None, None
        if geraet is None:
            return d, p
        return (eid, p) if d == geraet else (None, None)

    serien_summe, bucket_wert, bucket_summe, liste_summe = {}, {}, {}, {}
    kat_eltern = {}
    for z in zeilen:
        key, eltern = kategorie(z[0])
        if key is None:
            continue
        b = z[1] if aufl in ("jahr", "monat") else _datum(z[1]).isoformat()
        je = {s: teil(z, s) for s in spalten if s in abd}
        gesamt = sum(je.values())
        if not gesamt:
            continue
        kat_eltern[key] = eltern
        reihe = je if grp == "tabelle" else {key: gesamt}
        for sk, v in reihe.items():
            if not v:
                continue
            bucket_wert.setdefault(b, {})
            bucket_wert[b][sk] = bucket_wert[b].get(sk, 0) + v
            serien_summe[sk] = serien_summe.get(sk, 0) + v
        bucket_summe[b] = bucket_summe.get(b, 0) + gesamt
        if sel is None or b == sel:
            liste_summe[key] = liste_summe.get(key, 0) + gesamt

    # Serien: die größten MAX_SERIEN einzeln, der Rest „übrige“
    reihenfolge = [k for k in sorted(serien_summe, key=lambda k: (-serien_summe[k], k))]
    if grp == "tabelle":
        reihenfolge = [t for t in TABELLEN if t in serien_summe]
    einzeln = reihenfolge[:MAX_SERIEN]
    rest = reihenfolge[MAX_SERIEN:]

    def name(k):
        if k == EREIGNIS:
            return "Events"
        if grp == "tabelle":
            return {"states": "States", "kurz": "5-Min-Statistik", "lts": "Stundenstatistik", "attr": "Attribute", "events": "Events"}[k]
        if ebene == "plattform":
            return "(ohne Integration)" if k == verursacher.OHNE else k
        if ebene == "geraet":
            return "(ohne Gerät)" if k == verursacher.OHNE else (namen.get(k) or k[:8])
        return k

    serien = [{"key": k, "name": name(k), "summe": round(serien_summe[k]),
               "slot": (slots.get(kat_eltern.get(k) if ebene != "plattform" else k) or 0) if grp == "kategorie" else 0} for k in einzeln]
    if rest:
        serien.append({"key": REST, "name": f"übrige ({len(rest)})", "summe": round(sum(serien_summe[k] for k in rest)), "slot": 0})

    # „voll“ = der Zeitraum liegt ganz in der Abdeckung (die Attribute beginnen später und zählen dafür nicht mit); der laufende Monat und der
    # älteste, vom Recorder angeschnittene Monat sind es nicht und werden blasser gezeichnet
    cov_von, cov_bis = _datum(erster), _datum(min(v[1] for v in basis))
    buckets = []
    for key, d0, d1 in eimer:
        werte = bucket_wert.get(key, {})
        w = {k: round(werte[k]) for k in einzeln if werte.get(k)}
        if rest and any(werte.get(k) for k in rest):
            w[REST] = round(sum(werte.get(k, 0) for k in rest))
        buckets.append({"key": key, "von": d0.isoformat(), "bis": d1.isoformat(), "voll": d0 >= cov_von and d1 <= cov_bis,
                        "summe": round(bucket_summe.get(key, 0)), "werte": w})

    # Auffällige Tage: deutlich über dem Median der vollen Tage im Bild
    median = None
    if aufl == "tag":
        vorhanden = [b["summe"] for b in buckets if b["voll"] and b["summe"] > 0]
        if len(vorhanden) >= MIN_TAGE_AUFFAELLIG:
            median = statistics.median(vorhanden)
            for b in buckets:
                b["auffaellig"] = bool(b["voll"] and median and b["summe"] > AUFFAELLIG_FAKTOR * median)

    # Aufschlüsselung des gezeigten Zeitraums (oder nur des gewählten Zeitraums im Bild)
    gesamt_liste = sum(liste_summe.values()) or 1
    knoten = []
    if ebene == "plattform":
        for k, v in liste_summe.items():
            knoten.append({"key": k, "name": name(k) if grp == "kategorie" else ("Events" if k == EREIGNIS else ("(ohne Integration)" if k == verursacher.OHNE else k)),
                           "wert": round(v), "slot": slots.get(k) or 0, "tiefer": k != EREIGNIS})
    elif ebene == "geraet":
        for k, v in liste_summe.items():
            knoten.append({"key": k, "name": "(ohne Gerät)" if k == verursacher.OHNE else (namen.get(k) or k[:8]), "wert": round(v), "slot": slots.get(plat) or 0, "tiefer": True})
    else:
        for k, v in liste_summe.items():
            knoten.append({"key": k, "name": k, "wert": round(v), "slot": slots.get(plat) or 0, "tiefer": False})
    knoten.sort(key=lambda k: (-k["wert"], k["name"].lower()))
    insgesamt = len(knoten)
    for k in knoten:
        k["anteil"] = round(100 * k["wert"] / gesamt_liste, 1)
    knoten = knoten[:MAX_ENTITAETEN if ebene == "entitaet" else MAX_KNOTEN]

    pfad = {}
    if plat is not None:
        pfad["plattform"] = "(ohne Integration)" if plat == verursacher.OHNE else plat
    if geraet is not None:
        pfad["geraet"] = "(ohne Gerät)" if geraet == verursacher.OHNE else (namen.get(geraet) or geraet[:8])

    antwort = {"stand": r["stand"]["analyse_zeit"], "zuwachs_stand": zw_stand, "aufl": aufl, "von": von.isoformat(), "bis": bis.isoformat(),
               "mass": mass, "daten": daten, "grp": grp, "ebene": ebene, "pfad": pfad, "sel": sel,
               "buckets": buckets, "serien": serien, "median": median,
               "aufschluesselung": {"summe": round(sum(liste_summe.values())), "knoten": knoten, "knoten_gesamt": insgesamt},
               "abdeckung": {s: {"von": _datum(v[0]).isoformat(), "bis": _datum(v[1]).isoformat()} for s, v in abd.items()},
               "erster": _datum(erster).isoformat(), "letzter": _datum(letzter).isoformat(), "fruehester": _datum(fruehester).isoformat()}
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[schluessel] = antwort
    return antwort
