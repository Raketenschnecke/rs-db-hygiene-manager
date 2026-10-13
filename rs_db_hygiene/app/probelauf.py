"""Probelauf: wendet die Regeln auf den Analyse-Cache an – rechnet nur, löscht nichts.

Pro Entität und Wert (kurzzeit, attribute, langzeit):
  - wirksame Vorhaltezeit samt Herkunft (Regel, Muster, Standard)
  - Löschpotenzial: heute löschbare Zeilen bzw. Bytes (aus den Tageshistogrammen, tagesgenau)
Interne Namen (loeschbar_*, mit_vorgemerkten) bleiben für API und Links unverändert; in der GUI heißt es
„Löschpotenzial“ bzw. „passive Regeln“.
  - geschätzte Größe im eingeschwungenen Zustand über den Prognosezeitraum (= angenommenes purge_keep_days)
"""
import os
import sqlite3

import regeln as rg

# Messwerte vom 10.10.2026 (dbstat bzw. Tabellengröße / Zeilen), siehe README
B_STATE = 181          # states-Zeile inkl. Indizes
B_ATTR_FAKTOR = 1.15   # state_attributes inkl. Index / Nutzdaten
B_ATTR_FALLBACK = 59   # Ø Attributanteil je State-Zeile, solange keine Attribut-Analyse vorliegt
B_KURZ = 82            # statistics_short_term-Zeile inkl. Indizes
B_LTS = 79             # statistics-Zeile inkl. Indizes
B_EVENT = 145          # events + event_data inkl. Indizes
KURZ_PRO_TAG = 288     # 5-Min-Statistik
LTS_PRO_TAG = 24       # Stundenstatistik
MIN_KEEP_DAYS = 0      # Punkt 100/121: Kurzzeit „keine“ = alles vor heute 0:00 (ab 0.4.12; bis 0.4.11 Untergrenze 1 Tag wie loeschen.MIN_KEEP_DAYS)


def _histogramm(con, sql):
    h = {}
    for k, tag, n in con.execute(sql):
        h.setdefault(k, []).append((tag, n))
    return h


def _aelter(h, schluessel, grenze_tag):
    return sum(n for tag, n in h.get(schluessel, ()) if tag < grenze_tag)


def _lade(cache_pfad, attr_pfad):
    """Analyse-Cache und Attribut-Analyse einlesen (gemeinsam für rechne und verlauf)."""
    con = sqlite3.connect(f"file:{cache_pfad}?mode=ro", uri=True)
    info = dict(con.execute("SELECT schluessel, wert FROM info"))
    heute = int(info["heute_tag"])
    pkd = int(info["purge_keep_days"])
    st = _histogramm(con, "SELECT metadata_id, tag, n FROM st_tage")
    lts = _histogramm(con, "SELECT lts_id, tag, n FROM lts_tage")
    kurz = _histogramm(con, "SELECT lts_id, tag, n FROM kurz_tage")
    spalten = [d[0] for d in con.execute("SELECT * FROM entitaet LIMIT 0").description]
    ents = [dict(zip(spalten, z)) for z in con.execute("SELECT * FROM entitaet")]
    try:   # ab Cache-Version 3 (0.3.0): Events je Typ und Tag
        ev = _histogramm(con, "SELECT event_type, tag, n FROM ev_tage")
    except sqlite3.OperationalError:
        ev = None
    con.close()

    attr_letzt, attr_zuwachs, attr_zuwachs_z, attr_stand = {}, {}, {}, None
    if attr_pfad and os.path.exists(attr_pfad):
        ac = sqlite3.connect(f"file:{attr_pfad}?mode=ro", uri=True)
        attr_stand = dict(ac.execute("SELECT schluessel, wert FROM info")).get("analyse_zeit")
        for mid, tag, art, z, b in ac.execute("SELECT metadata_id, tag, art, zeilen, bytes FROM attr_tage"):
            if art == "letzt":
                attr_letzt.setdefault(mid, []).append((tag, b, z))
            elif heute - 8 <= tag < heute - 1:      # 7 volle Tage
                attr_zuwachs[mid] = attr_zuwachs.get(mid, 0) + b / 7
                attr_zuwachs_z[mid] = attr_zuwachs_z.get(mid, 0) + z / 7    # Punkt 86: Attributzeilen je Tag
        ac.close()
    return {"info": info, "heute": heute, "pkd": pkd, "st": st, "lts": lts, "kurz": kurz, "ents": ents, "ev": ev,
            "attr_letzt": attr_letzt, "attr_zuwachs": attr_zuwachs, "attr_zuwachs_z": attr_zuwachs_z, "attr_stand": attr_stand}


def rechne(cache_pfad, attr_pfad, regel_daten, ziel_tage=None, mit_vorgemerkten=False):
    d = _lade(cache_pfad, attr_pfad)
    info, heute, pkd, st, lts, kurz, ents, ev = (d[k] for k in ("info", "heute", "pkd", "st", "lts", "kurz", "ents", "ev"))
    attr_letzt, attr_zuwachs, attr_zuwachs_z, attr_stand = (d[k] for k in ("attr_letzt", "attr_zuwachs", "attr_zuwachs_z", "attr_stand"))
    ziel = int(ziel_tage or pkd)
    zb = min(pkd, ziel)    # Punkt 102: ohne Regeln bleibt höchstens purge_keep_days, im Prognosezeitraum höchstens ziel
    mit_attr = bool(attr_letzt)

    aufl = rg.Aufloeser(regel_daten, mit_vorgemerkten)
    regel_namen = {r["id"]: r["name"] for r in regel_daten["regeln"]}
    entity_regeln = {r["id"] for r in regel_daten["regeln"] if r["typ"] == "entity"}
    rg_name = {"kurzzeit": "Kurzzeit", "attribute": "Attribute", "langzeit": "Langzeit"}.get
    zeilen, summe, je_regel = [], {}, {}

    def add(d, k, v):
        d[k] = d.get(k, 0) + v

    for e in ents:
        verwaist = e["status"] != "aktiv"     # schreibt nicht mehr: verwaist oder deaktiviert (Punkt 186)
        erg = aufl.loese(e["entity_id"], e["state_class"], e["device_class"], e["status"])
        # Punkt 102 (0.4.8): „wirksam“ = Regel-Tage, höchstens purge_keep_days („global“ = purge_keep_days). Damit rechnen
        # die Spalten „wirksam“, die Hinweise und die Löschmenge sofort – wie der Löschplan. Bis 0.4.7 stand hier der
        # Prognosezeitraum (ziel), der nur für die Prognose gilt: kp/ap = höchstens der Prognosezeitraum.
        tage, hinweise = rg.wirksame_tage(erg, pkd)
        mid, lid = e["metadata_id"], e["lts_id"]
        k, a, lz = tage["kurzzeit"], tage["attribute"], tage["langzeit"]
        kp, ap = min(k, ziel), min(a, ziel)
        # Punkt 100 (0.4.8) / 121 (0.4.12): Kurzzeit „keine“ (0 Tage) löscht alles vor heute 0:00 (MIN_KEEP_DAYS 0)
        kl, al = max(k, MIN_KEEP_DAYS), max(a, MIN_KEEP_DAYS)

        loesch = {
            "states": _aelter(st, mid, heute - kl),
            "kurz": _aelter(kurz, lid, heute - kl) if lid else 0,
            # Attribute inkl. Indexanteil, damit alle Attributwerte (Bestand, Zuwachs, Löschmenge) vergleichbar sind
            "attr_bytes": _aelter_bytes(attr_letzt, mid, heute - al) * B_ATTR_FAKTOR if mit_attr else None,
            # Punkt 63: Anzahl Attributzeilen, die mit den States wegfallen (zuletzt vor der Grenze benutzt)
            "attr_zeilen": _aelter_zeilen(attr_letzt, mid, heute - al) if mit_attr else None,
            "lts": (_aelter(lts, lid, heute - lz) if lz is not None else 0) if lid else 0,
        }
        # Wer seit 24 h nichts schreibt (z. B. vom Recorder ausgeschlossen), erzeugt auch keine neuen Attribute –
        # sonst würde der 7-Tage-Zuwachs von vor dem Ausschluss für den ganzen Prognosezeitraum hochgerechnet.
        if not e["rows_24h"]:
            zuwachs_attr = zuwachs_attr_z = 0
        else:
            zuwachs_attr = attr_zuwachs.get(mid, 0) if mit_attr else e["rows_24h"] * B_ATTR_FALLBACK
            zuwachs_attr_z = attr_zuwachs_z.get(mid, 0) if mit_attr else 0
        lts_jetzt = e["lts_rows"] if lid else 0
        # Punkt 86: Zeilen je Tabelle im Bestand (Kachelraster, Zeile „Istzustand“)
        add(summe, "bestand_states", e["rows"] or 0)
        add(summe, "bestand_kurz", (e["kurz_rows"] or 0) if lid else 0)
        add(summe, "bestand_lts", lts_jetzt or 0)
        if verwaist:
            # Verwaiste schreiben nicht mehr: Rohdaten verschwinden nach purge_keep_days von selbst,
            # übrig bleibt dauerhaft nur die Langzeitstatistik (HA löscht sie nie).
            behalten_lts = lts_jetzt if lz is None else (sum(n for t_, n in lts.get(lid, ()) if t_ >= heute - lz) if lid else 0)
            prog = {"states": 0, "attribute": 0, "kurz": 0, "lts": behalten_lts * B_LTS}
            basis = {"states": 0, "attribute": 0, "kurz": 0, "lts": lts_jetzt * B_LTS}
            prog_z = {"states": 0, "attribute": 0, "kurz": 0, "lts": behalten_lts or 0}
            # Punkt 186: Verwaiste und Deaktivierte getrennt zählen (beide schreiben nicht mehr)
            wer = "verwaist" if rg.ist_verwaist(e["status"]) else "deaktiviert"
            add(summe, wer + "_entitaeten", 1)
            add(summe, wer + "_states", e["rows"])
            add(summe, wer + "_loesch_states", loesch["states"])
            add(summe, wer + "_prognose_lts", behalten_lts or 0)
        else:
            prog = {
                "states": e["rows_24h"] * B_STATE * kp,
                "attribute": zuwachs_attr * B_ATTR_FAKTOR * ap,
                "kurz": KURZ_PRO_TAG * B_KURZ * kp if lid else 0,
                # Langzeit über den Prognosezeitraum (ab 0.1.6; vorher fest 1 Jahr)
                "lts": ((lts_jetzt + LTS_PRO_TAG * ziel) if lz is None else LTS_PRO_TAG * lz) * B_LTS if lid else 0,
            }
            # Punkt 86: dieselbe Prognose in Zeilen (Kachelraster, Zeile „Prognose“)
            prog_z = {
                "states": e["rows_24h"] * kp,
                "attribute": zuwachs_attr_z * ap,
                "kurz": KURZ_PRO_TAG * kp if lid else 0,
                "lts": ((lts_jetzt + LTS_PRO_TAG * ziel) if lz is None else LTS_PRO_TAG * lz) if lid else 0,
            }
            basis = {  # nur HA-Standard (purge_keep_days) über den Prognosezeitraum, ohne Regeln
                "states": e["rows_24h"] * B_STATE * zb,
                "attribute": zuwachs_attr * B_ATTR_FAKTOR * zb,
                "kurz": KURZ_PRO_TAG * B_KURZ * zb if lid else 0,
                "lts": (lts_jetzt + LTS_PRO_TAG * ziel) * B_LTS if lid else 0,
            }
        if not verwaist and erg["kurzzeit"]["wert"] == "global":
            # Punkt 79: Kurzzeit „global“ = purge_keep_days (bei 7300 also 20 Jahre) – Zufluss je Tag für die Anzeige
            add(summe, "global_entitaeten", 1)
            add(summe, "global_states", e["rows"] or 0)
            add(summe, "global_zuwachs_tag", e["rows_24h"] or 0)
            add(summe, "global_bytes_tag", e["rows_24h"] * B_STATE + (KURZ_PRO_TAG * B_KURZ if lid else 0) + zuwachs_attr * B_ATTR_FAKTOR)
        for t_, v in prog.items():
            add(summe, f"prognose_{t_}", v)
        for t_, v in prog_z.items():
            add(summe, f"prognose_zeilen_{t_}", v)
        for t_, v in basis.items():
            add(summe, f"basis_{t_}", v)
        for t_ in ("states", "kurz", "lts"):
            add(summe, f"loeschbar_{t_}", loesch[t_])
        if loesch["attr_bytes"]:
            add(summe, "loeschbar_attr_bytes", loesch["attr_bytes"])
        if loesch["attr_zeilen"]:
            add(summe, "loeschbar_attr_zeilen", loesch["attr_zeilen"])

        # Attribute je Entität (Punkt 23)
        attr = {"attr_bytes": None, "attr_zeilen": None, "attr_zuwachs_tag": None, "attr_avg": None,
                "attr_loesch": loesch["attr_bytes"], "attr_anteil": None}
        if mit_attr:
            nutz = sum(b for _t, b, _z in attr_letzt.get(mid, ()))
            anz = sum(z for _t, _b, z in attr_letzt.get(mid, ()))
            bestand = nutz * B_ATTR_FAKTOR
            speicher_states = (e["rows"] or 0) * B_STATE
            attr.update({"attr_bytes": bestand, "attr_zeilen": anz, "attr_zuwachs_tag": zuwachs_attr * B_ATTR_FAKTOR,
                         "attr_avg": round(nutz / anz) if anz else None,
                         "attr_anteil": round(100 * bestand / (bestand + speicher_states), 1) if bestand + speicher_states else None})
            add(summe, "attr_bestand_bytes", bestand)
            add(summe, "attr_zuwachs_tag_bytes", zuwachs_attr * B_ATTR_FAKTOR)
            add(summe, "bestand_attr_zeilen", anz)

        def eintrag(rid):
            return je_regel.setdefault(rid, {"regel": rid, "name": regel_namen.get(rid, rid), "entitaeten": set(),
                                             "kurzzeit": 0, "attribute": 0, "langzeit": 0,
                                             "states": 0, "kurz": 0, "attr_bytes": 0, "lts": 0, "ersparnis_bytes": 0, "events": 0, "attr_zeilen": 0, "ersparnis_events": 0,
                                             "ersparnis_states": 0, "ersparnis_attr": 0, "ersparnis_lts": 0,
                                             "ueberstimmt": set(), "verwaist": set()})

        # Überstimmte Regeln (Punkt 29) und überstimmte Entitätsregeln als Hinweis (Punkt 28)
        for art in rg.WERTE:
            for u in erg[art]["ueberstimmt"]:
                eintrag(u["regel"])["ueberstimmt"].add(e["entity_id"])
                if u["regel"] in entity_regeln:
                    hinweise.append(f"{rg_name(art)}: Entitätsregel {u['regel']} überstimmt durch {erg[art]['regel']}")

        for art in rg.WERTE:
            rid = erg[art]["regel"]
            if not rid:
                continue
            r = eintrag(rid)
            r["entitaeten"].add(e["entity_id"])
            if rg.ist_verwaist(e["status"]):
                r["verwaist"].add(e["entity_id"])
            r[art] += 1
            if art == "kurzzeit":
                r["states"] += loesch["states"]
                r["kurz"] += loesch["kurz"]
                r["attr_zeilen"] += loesch["attr_zeilen"] or 0     # Attribute folgen der Kurzzeit
                d = (basis["states"] - prog["states"]) + (basis["kurz"] - prog["kurz"])
                r["ersparnis_states"] += d
                # ab 0.3.4 kein eigener Attribut-Wert: Attribut-Löschmenge und -Ersparnis gehören zur Kurzzeit-Regel
                r["attr_bytes"] += loesch["attr_bytes"] or 0
                da = basis["attribute"] - prog["attribute"]
                r["ersparnis_attr"] += da
                d += da
            elif art == "attribute":
                r["attr_bytes"] += loesch["attr_bytes"] or 0
                d = basis["attribute"] - prog["attribute"]
                r["ersparnis_attr"] += d
            else:
                r["lts"] += loesch["lts"]
                d = basis["lts"] - prog["lts"]
                r["ersparnis_lts"] += d
            r["ersparnis_bytes"] += d

        zeilen.append({**_basis(e), **attr, "aktion": ("verwaist" if rg.ist_verwaist(e["status"]) else "deaktiviert") if verwaist else "regeln",
                       "werte": {art: {"wert": erg[art]["wert"], "anzeige": rg.anzeige(erg[art]["wert"]),
                                       "tage": tage[art], "regel": erg[art]["regel"], "muster": erg[art]["muster"],
                                       "ueberstimmt": [{"regel": u["regel"], "anzeige": rg.anzeige(u["wert"])}
                                                       for u in erg[art]["ueberstimmt"]]}
                                 for art in rg.WERTE},
                       "loeschbar": loesch, "prognose_bytes": sum(prog.values()), "prognose_teile": prog,
                       # Punkt 63: Block „Löschmenge sofort“ als flache Felder (Server-Filter, Sortierung, Summen)
                       "loesch_summe": loesch["states"] + loesch["kurz"] + loesch["lts"], "loesch_states": loesch["states"],
                       "loesch_kurz": loesch["kurz"], "loesch_lts": loesch["lts"], "loesch_attr_zeilen": loesch["attr_zeilen"],
                       "hinweise": hinweise, "geschuetzt": erg["geschuetzt"], "schutz": erg.get("schutz")})
        if erg.get("schutz"):
            add(summe, "schutz_entitaeten", 1)    # Punkt 111

    events_liste, ev_prog, ev_basis = [], None, None
    if ev is not None:
        ev_prog = ev_basis = 0
        for typ, h in ev.items():
            er = rg.event_regel(regel_daten, typ, pkd, mit_vorgemerkten)    # ab 0.3.1 über Regeln vom Typ event
            k = er["tage"]           # Punkt 102: wirksam (höchstens purge_keep_days), nicht der Prognosezeitraum
            kp = min(k, ziel)
            pro_tag = sum(n for t_, n in h if heute - 8 <= t_ < heute - 1) / 7      # 7 volle Tage
            loesch = _aelter(ev, typ, heute - max(k, MIN_KEEP_DAYS))
            p, b = pro_tag * B_EVENT * kp, pro_tag * B_EVENT * zb
            ev_prog += p
            ev_basis += b
            add(summe, "loeschbar_events", loesch)
            add(summe, "bestand_events", sum(n for _t, n in h))           # Punkt 86
            add(summe, "prognose_zeilen_events", pro_tag * kp)
            w = er["wert"] if er["regel"] else None
            if er["regel"]:   # Prognosen je Regel und Spalte „Treffer“ (Event-Typen statt Entitäten)
                rr = je_regel.setdefault(er["regel"], {"regel": er["regel"], "name": regel_namen.get(er["regel"], er["regel"]),
                                                       "entitaeten": set(), "kurzzeit": 0, "attribute": 0, "langzeit": 0,
                                                       "states": 0, "kurz": 0, "attr_bytes": 0, "lts": 0, "ersparnis_bytes": 0, "events": 0, "attr_zeilen": 0, "ersparnis_events": 0,
                                                       "ersparnis_states": 0, "ersparnis_attr": 0, "ersparnis_lts": 0,
                                                       "ueberstimmt": set(), "verwaist": set()})
                rr["entitaeten"].add(typ)
                rr["kurzzeit"] += 1
                rr["events"] = rr.get("events", 0) + loesch
                rr["ersparnis_events"] = rr.get("ersparnis_events", 0) + (b - p)
                rr["ersparnis_bytes"] += b - p
            for u in er["ueberstimmt"]:
                if u["regel"] in je_regel:
                    je_regel[u["regel"]]["ueberstimmt"].add(typ)
            events_liste.append({"event_type": typ, "wert": w or "global", "anzeige": rg.anzeige(w) if w else "global",
                                 "regel": er["regel"], "muster": er["muster"], "ueberstimmt": er["ueberstimmt"],
                                 "tage": k, "zeilen": sum(n for _t, n in h), "pro_tag": round(pro_tag),
                                 "zeilen_24h": sum(n for t_, n in h if t_ >= heute - 1), "erster_tag": min(t_ for t_, _n in h),
                                 "loeschbar": loesch, "prognose_bytes": p, "ersparnis_bytes": b - p})
        events_liste.sort(key=lambda x: -x["zeilen"])
    events = int(info.get("events_pro_tag", 0)) * B_EVENT
    regeln_liste = []
    for r in je_regel.values():
        r["entitaeten"] = len(r["entitaeten"])
        r["ueberstimmt"] = len(r["ueberstimmt"])
        r["verwaist"] = len(r["verwaist"])
        regeln_liste.append(r)
    regeln_liste.sort(key=lambda r: r["regel"])
    ev_p = events * zb if ev_prog is None else ev_prog
    ev_b = events * zb if ev_basis is None else ev_basis
    prognose = sum(v for k_, v in summe.items() if k_.startswith("prognose_") and not k_.startswith("prognose_zeilen_")) + ev_p
    basis_summe = sum(v for k_, v in summe.items() if k_.startswith("basis_")) + ev_b
    # Punkt 86: Platz, den die Löschmenge sofort belegt (wird nach einem Repack in der Datei frei)
    summe["loesch_bytes"] = (summe.get("loeschbar_states", 0) * B_STATE + summe.get("loeschbar_kurz", 0) * B_KURZ
                             + summe.get("loeschbar_lts", 0) * B_LTS + summe.get("loeschbar_events", 0) * B_EVENT
                             + summe.get("loeschbar_attr_bytes", 0))
    return {
        "stand": {"analyse_zeit": float(info["analyse_zeit"]), "attribut_analyse": float(attr_stand) if attr_stand else None,
                  "db_bytes": int(info["db_bytes"]), "purge_keep_days": pkd, "ziel_tage": ziel,
                  "mit_vorgemerkten": mit_vorgemerkten},
        "summe": {**summe, "events_bytes": ev_p, "events_basis_bytes": ev_b, "prognose_bytes": prognose, "basis_bytes": basis_summe},
        "regeln": regeln_liste,
        "events": events_liste,
        "entitaeten": zeilen,
    }


def _aelter_zeilen(h, mid, grenze_tag):
    return sum(z for tag, _b, z in h.get(mid, ()) if tag < grenze_tag)


def _aelter_bytes(h, mid, grenze_tag):
    return sum(b for tag, b, *_ in h.get(mid, ()) if tag < grenze_tag)


def _basis(e):
    b = {k: e[k] for k in ("entity_id", "domain", "status", "rows", "first_ts", "last_ts", "rows_24h",
                           "state_class", "device_class", "unit", "lts_rows", "lts_first_ts", "kurz_rows")}
    # Herkunft gibt es ab Cache-Version 2 (0.1.4); ältere Caches liefern leere Felder bis zur nächsten Analyse
    for k in ("herkunft_art", "herkunft", "herkunft_info", "device_id"):
        b[k] = e.get(k) or ""
    # Punkte 118/120 (0.4.11): „erster“/„letzter“ über States, 5-Min-Statistik und Langzeit; die Werte je Datenart bleiben
    # einzeln erhalten (st_*, kurz_*, lts_*). Caches vor Version 4 haben keine 5-Min-Zeitstempel und nur tagesgenaue Langzeit.
    b.update(st_first_ts=e["first_ts"], st_last_ts=e["last_ts"], kurz_first_ts=e.get("kurz_first_ts"),
             kurz_last_ts=e.get("kurz_last_ts"), lts_last_ts=e.get("lts_last_ts"))
    anf = [t for t in (b["st_first_ts"], b["kurz_first_ts"], b["lts_first_ts"]) if t is not None]
    end = [t for t in (b["st_last_ts"], b["kurz_last_ts"], b["lts_last_ts"]) if t is not None]
    b["first_ts"], b["last_ts"] = (min(anf) if anf else None), (max(end) if end else None)
    return b


MONAT = 365 / 12   # Tage je Monat für den Verlauf (1 Jahr = 365 Tage wie bei den Regeln)


def verlauf(cache_pfad, attr_pfad, regel_daten, ziel_tage=None, mit_passiven=False):
    """Prognose-Verlauf der DB-Größe in Monatsschritten über den Prognosezeitraum, vier Varianten.

    Anders als die Kachel-Prognose (eingeschwungener Zustand) startet der Verlauf beim heutigen Bestand: Je Monat
    bleibt vom Bestand, was nach der jeweiligen Vorhaltezeit noch nicht fällig ist, dazu kommt der Zufluss
    (Schreibrate × höchstens Vorhaltezeit). Fällige Daten fallen im ersten Monat weg (wie ein Löschlauf).
    Abgleich mit der Datei: Die Differenz Dateigröße − geschätzter Bestand (andere Tabellen, freier Platz, Abweichung der
    Zeilengrößen; kann negativ sein) bleibt als fester Betrag stehen, damit der Startwert der Dateigröße entspricht.
    Varianten: ohne = keine Regeln (alles purge_keep_days, Langzeit unbegrenzt), ha = nur Regel HA:standard,
    aktiv = aktive Regeln (passive nur mit mit_passiven, wie Haken „passive Regeln mitrechnen“), alle = aktive und passive Regeln.
    Punkt 191 (ersetzt Punkt 180): aktiv und alle mit der Regel HA:standard in ihrer eingestellten Kurzzeit – aktiv nur, wenn
    sie aktiv ist (oder mit mit_passiven), alle immer; ha bleibt fest bei 10 Tagen (Vergleich mit HA in der Grundeinstellung).
    """
    from bisect import bisect_right
    from math import ceil
    d = _lade(cache_pfad, attr_pfad)
    info, heute, pkd, st, lts, kurz, ents, ev = (d[k] for k in ("info", "heute", "pkd", "st", "lts", "kurz", "ents", "ev"))
    attr_letzt, attr_zuwachs = d["attr_letzt"], d["attr_zuwachs"]
    mit_attr = bool(attr_letzt)
    ziel = int(ziel_tage or pkd)
    monate = max(1, ceil(ziel / MONAT - 1e-9))
    tage = [0] + [min(round(m * MONAT), ziel) for m in range(1, monate + 1)]
    ohne = {"standard": dict(rg.STANDARD), "regeln": []}
    varianten = {"ohne": (ohne, False), "ha": ({**ohne, "regeln": [{**rg.HA_STANDARD, "aktiv": True}]}, False),
                 "aktiv": (regel_daten, mit_passiven), "alle": (regel_daten, True)}
    ha_regel = next((r for r in regel_daten["regeln"] if r["typ"] == "ha_standard"), rg.HA_STANDARD)
    werte, bestand = {}, 0
    for name, (daten, passiv) in varianten.items():
        aufl = rg.Aufloeser(daten, passiv)
        abgang = [0.0] * (monate + 2)     # Bytes, die im Monat m fällig werden (Index monate+1 = nie im Zeitraum)
        rate = {}                         # Vorhaltezeit (None = unbegrenzt) → Zufluss in Bytes je Tag
        best = 0.0

        def bestand_add(h, b, keep):
            nonlocal best
            for tag, n, *_ in h:
                v = n * b
                best += v
                if keep is not None:
                    abgang[bisect_right(tage, tag + keep - heute, 1)] += v

        def zufluss(keep, v):
            if v:
                rate[keep] = rate.get(keep, 0) + v

        for e in ents:
            verwaist = e["status"] != "aktiv"     # schreibt nicht mehr: verwaist oder deaktiviert (Punkt 186)
            t_, _h = rg.wirksame_tage(aufl.loese(e["entity_id"], e["state_class"], e["device_class"], e["status"]), pkd)
            k, a, lz = t_["kurzzeit"], t_["attribute"], t_["langzeit"]
            kl, al = max(k, MIN_KEEP_DAYS), max(a, MIN_KEEP_DAYS)
            mid, lid = e["metadata_id"], e["lts_id"]
            bestand_add(st.get(mid, ()), B_STATE, kl)
            if mit_attr:
                bestand_add([(t, b) for t, b, _z in attr_letzt.get(mid, ())], B_ATTR_FAKTOR, al)
            if lid:
                bestand_add(kurz.get(lid, ()), B_KURZ, kl)
                bestand_add(lts.get(lid, ()), B_LTS, lz)
            if verwaist:
                continue
            zufluss(k, (e["rows_24h"] or 0) * B_STATE)
            if e["rows_24h"]:
                zufluss(a, (attr_zuwachs.get(mid, 0) if mit_attr else e["rows_24h"] * B_ATTR_FALLBACK) * B_ATTR_FAKTOR)
            if lid:
                zufluss(k, KURZ_PRO_TAG * B_KURZ)
                zufluss(lz, LTS_PRO_TAG * B_LTS)
        if ev is not None:
            for typ, h in ev.items():
                k = rg.event_regel(daten, typ, pkd, passiv)["tage"]
                bestand_add(h, B_EVENT, max(k, MIN_KEEP_DAYS))
                zufluss(k, sum(n for t, n in h if heute - 8 <= t < heute - 1) / 7 * B_EVENT)
        else:
            zufluss(pkd, int(info.get("events_pro_tag", 0)) * B_EVENT)
        bestand = best
        reihe, weg = [], 0.0
        for m, t in enumerate(tage):
            weg += abgang[m] if m else 0
            reihe.append(best - weg + sum(v * (t if kp is None else min(t, kp)) for kp, v in rate.items()))
        werte[name] = reihe
    rest = int(info["db_bytes"]) - bestand
    return {"stand": {"analyse_zeit": float(info["analyse_zeit"]), "db_bytes": int(info["db_bytes"]), "purge_keep_days": pkd,
                      "ziel_tage": ziel, "mit_passiven": mit_passiven,
                      "ha_kurzzeit": ha_regel["kurzzeit"], "ha_aktiv": bool(ha_regel.get("aktiv")), "mit_attribute": mit_attr, "rest_bytes": rest, "daten_bytes": bestand},
            "tage": tage, "werte": {k: [round(rest + v) for v in r] for k, r in werte.items()}}
