"""Löschkern der Mini-Integration – reines SQL, ohne Abhängigkeit von Home Assistant.

Läuft in HA in der Warteschlange des Recorders (siehe __init__.py) und ist außerhalb von HA mit sqlite3 testbar.
`db` ist ein Adapter mit:
    query(sql, params=None)   -> Liste von Tupeln
    execute(sql, params=None) -> Anzahl betroffener Zeilen
    commit()

Tabellen:
    statistics, statistics_short_term   je statistic_id (Langzeit bzw. 5-Min-Statistik)
    events                              je event_type, danach nicht mehr benutzte event_data
    states_attribute                    (Punkt 254) States bleiben, ihr Verweis auf die Attribute wird getrennt
Gelöscht wird paketweise (BATCH Zeilen je DELETE, Commit je Paket) bis höchstens max_zeilen.
"""
BATCH = 5000
IN_CHUNK = 900          # Größe der IN-Listen beim Prüfen der event_data
TABELLEN = ("statistics", "statistics_short_term", "events", "states_attribute")


class KernFehler(Exception):
    pass


def _ids_text(ids):
    """IN-Liste aus Ganzzahlen; die Werte stammen aus der DB selbst (keine Benutzereingabe im SQL)."""
    return ",".join(str(int(i)) for i in ids)


def _bloecke(liste, n):
    for i in range(0, len(liste), n):
        yield liste[i:i + n]


def loesche(db, tabelle, namen, grenze_ts=None, max_zeilen=100_000, nur_zaehlen=False, alle=False):
    """Löscht Zeilen älter als grenze_ts (oder alle) für die genannten statistic_ids bzw. event_types.

    Rückgabe: {vorher, geloescht, rest, unbekannt, event_data_geloescht, freie_data_ids}
    """
    if tabelle not in TABELLEN:
        raise KernFehler(f"unbekannte Tabelle {tabelle}")
    if tabelle == "states_attribute":
        if alle or grenze_ts is None:
            raise KernFehler("states_attribute braucht grenze_ts und kein „alle“")
        return kuerze_attribute(db, namen, grenze_ts, max_zeilen, nur_zaehlen)
    if not namen:
        raise KernFehler("keine statistic_ids bzw. event_types – ein Aufruf ohne Liste ist nicht erlaubt")
    if alle and tabelle == "events":
        raise KernFehler("„alle“ ist nur für Statistik erlaubt")
    if not alle and grenze_ts is None:
        raise KernFehler("grenze_ts fehlt")
    if max_zeilen < 1:
        raise KernFehler("max_zeilen muss mindestens 1 sein")

    if tabelle == "events":
        bekannt = {name: i for i, name in db.query("SELECT event_type_id, event_type FROM event_types")}
        schluessel, zeit, pk = "event_type_id", "time_fired_ts", "event_id"
    else:
        bekannt = {name: i for i, name in db.query("SELECT id, statistic_id FROM statistics_meta")}
        schluessel, zeit, pk = "metadata_id", "start_ts", "id"
    unbekannt = sorted(n for n in set(namen) if n not in bekannt)
    ids = sorted({bekannt[n] for n in namen if n in bekannt})
    erg = {"vorher": 0, "geloescht": 0, "rest": 0, "unbekannt": unbekannt, "event_data_geloescht": 0, "freie_data_ids": []}
    if not ids:
        return erg

    bed = f"{schluessel} IN ({_ids_text(ids)})" + ("" if alle else f" AND {zeit} < :grenze")
    par = None if alle else {"grenze": float(grenze_ts)}
    erg["vorher"] = db.query(f"SELECT COUNT(*) FROM {tabelle} WHERE {bed}", par)[0][0]
    if nur_zaehlen or not erg["vorher"]:
        erg["rest"] = erg["vorher"]
        return erg

    data_ids = set()
    while erg["geloescht"] < max_zeilen:
        n = min(BATCH, max_zeilen - erg["geloescht"])
        if tabelle == "events":
            zeilen = db.query(f"SELECT event_id, data_id FROM events WHERE {bed} LIMIT {int(n)}", par)
            if not zeilen:
                break
            data_ids |= {d for _e, d in zeilen if d is not None}
            k = db.execute(f"DELETE FROM events WHERE event_id IN ({_ids_text(e for e, _d in zeilen)})")
        else:
            k = db.execute(f"DELETE FROM {tabelle} WHERE {pk} IN (SELECT {pk} FROM {tabelle} WHERE {bed} LIMIT {int(n)})", par)
        if not k:
            break
        erg["geloescht"] += k
        db.commit()

    if data_ids:
        # event_data wird von mehreren Events geteilt: nur löschen, was kein Event mehr benutzt
        benutzt = set()
        kandidaten = sorted(data_ids)
        for teil in _bloecke(kandidaten, IN_CHUNK):
            benutzt |= {d for (d,) in db.query(f"SELECT DISTINCT data_id FROM events WHERE data_id IN ({_ids_text(teil)})")}
        frei = sorted(data_ids - benutzt)
        for teil in _bloecke(frei, IN_CHUNK):
            erg["event_data_geloescht"] += db.execute(f"DELETE FROM event_data WHERE data_id IN ({_ids_text(teil)})")
        db.commit()
        erg["freie_data_ids"] = frei
    erg["rest"] = erg["vorher"] - erg["geloescht"]
    return erg


def _wegfallende_attr(db, bed, par):
    """Punkt 295: attributes_id der States unter `bed`, die kein anderer State benutzt – nur diese werden nach dem
    Abtrennen frei. Gleiche Attribute zweier Entitäten bleiben so lange, bis HAs Purge die alten States entfernt."""
    kand = sorted(a for (a,) in db.query(f"SELECT DISTINCT attributes_id FROM states WHERE {bed}", par))
    frei = []
    for teil in _bloecke(kand, IN_CHUNK):
        # EXISTS hört beim ersten fremden State auf; COALESCE: ein State ohne Zeitstempel zählt als „benutzt“
        benutzt = {a for (a,) in db.query(
            f"SELECT sa.attributes_id FROM state_attributes sa WHERE sa.attributes_id IN ({_ids_text(teil)}) AND EXISTS "
            f"(SELECT 1 FROM states WHERE states.attributes_id = sa.attributes_id AND NOT COALESCE(({bed}), 0))", par)}
        frei += [a for a in teil if a not in benutzt]
    return frei


def kuerze_attribute(db, namen, grenze_ts, max_zeilen=100_000, nur_zaehlen=False):
    """Punkt 254: Attribute alter States abtrennen. Bei den genannten Entitäten (entity_id) wird für States mit
    last_updated_ts < grenze_ts der Verweis states.attributes_id auf NULL gesetzt; die States (Werte, Zeiten) bleiben.
    Der jeweils neueste State jeder Entität behält seine Attribute. Danach werden die state_attributes-Zeilen gelöscht,
    die kein State mehr benutzt (wie HAs Purge).
    Punkt 295 (0.5.6b29): abgetrennt werden nur States, deren Attributzeile danach frei wird; die übrigen alten States
    behalten ihre (noch gebrauchten) Attribute.

    Rückgabe: {vorher, geloescht, rest, behalten, unbekannt, event_data_geloescht, freie_data_ids, attr_zeilen_geloescht, freie_attr_ids}
    vorher/geloescht/rest zählen States (die Arbeit), behalten die alten States, die verbunden bleiben,
    attr_zeilen_geloescht die entfernten Attributzeilen.
    """
    if not namen:
        raise KernFehler("keine entity_ids – ein Aufruf ohne Liste ist nicht erlaubt")
    if max_zeilen < 1:
        raise KernFehler("max_zeilen muss mindestens 1 sein")
    bekannt = {e: m for e, m in db.query("SELECT entity_id, metadata_id FROM states_meta")}
    unbekannt = sorted(n for n in set(namen) if n not in bekannt)
    mids = sorted({bekannt[n] for n in namen if n in bekannt})
    erg = {"vorher": 0, "geloescht": 0, "rest": 0, "unbekannt": unbekannt, "event_data_geloescht": 0, "freie_data_ids": [],
           "attr_zeilen_geloescht": 0, "freie_attr_ids": []}
    if not mids:
        return erg
    neueste = [s for (s,) in db.query(f"SELECT MAX(state_id) FROM states WHERE metadata_id IN ({_ids_text(mids)}) GROUP BY metadata_id")]
    bed = f"metadata_id IN ({_ids_text(mids)}) AND last_updated_ts < :grenze AND attributes_id IS NOT NULL"
    if neueste:
        bed += f" AND state_id NOT IN ({_ids_text(neueste)})"
    par = {"grenze": float(grenze_ts)}
    alle_alten = db.query(f"SELECT COUNT(*) FROM states WHERE {bed}", par)[0][0]
    # Punkt 295: nur States abtrennen, deren Attributzeile danach frei wird. Eine Zeile, die neuere States (oder andere
    # Entitäten) weiter benutzen, bliebe ohnehin – sie abzutrennen kostet nur Schreibarbeit (z. B. 213.000 States für 1 Zeile).
    frei_kand = _wegfallende_attr(db, bed, par) if alle_alten else []
    teile = [f"{bed} AND attributes_id IN ({_ids_text(t)})" for t in _bloecke(frei_kand, IN_CHUNK)]
    erg["vorher"] = sum(db.query(f"SELECT COUNT(*) FROM states WHERE {b}", par)[0][0] for b in teile)
    erg["behalten"] = alle_alten - erg["vorher"]      # alte States, deren Attributzeile noch gebraucht wird – bleiben verbunden
    if nur_zaehlen or not erg["vorher"]:
        erg["rest"] = erg["vorher"]
        return erg

    attr_ids = set()
    for b in teile:
        while erg["geloescht"] < max_zeilen:
            n = min(BATCH, max_zeilen - erg["geloescht"])
            zeilen = db.query(f"SELECT state_id, attributes_id FROM states WHERE {b} LIMIT {int(n)}", par)
            if not zeilen:
                break
            attr_ids |= {a for _s, a in zeilen}
            k = db.execute(f"UPDATE states SET attributes_id = NULL WHERE state_id IN ({_ids_text(s for s, _a in zeilen)})")
            if not k:
                break
            erg["geloescht"] += k
            db.commit()

    if attr_ids:
        # Attribute werden von vielen States geteilt: nur löschen, was kein State mehr benutzt
        benutzt = set()
        kandidaten = sorted(attr_ids)
        for teil in _bloecke(kandidaten, IN_CHUNK):
            benutzt |= {a for (a,) in db.query(f"SELECT DISTINCT attributes_id FROM states WHERE attributes_id IN ({_ids_text(teil)})")}
        frei = sorted(attr_ids - benutzt)
        for teil in _bloecke(frei, IN_CHUNK):
            erg["attr_zeilen_geloescht"] += db.execute(f"DELETE FROM state_attributes WHERE attributes_id IN ({_ids_text(teil)})")
        db.commit()
        erg["freie_attr_ids"] = frei
    erg["rest"] = erg["vorher"] - erg["geloescht"]
    return erg
