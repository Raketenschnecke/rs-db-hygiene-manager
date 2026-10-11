"""Löschkern der Mini-Integration – reines SQL, ohne Abhängigkeit von Home Assistant.

Läuft in HA in der Warteschlange des Recorders (siehe __init__.py) und ist außerhalb von HA mit sqlite3 testbar.
`db` ist ein Adapter mit:
    query(sql, params=None)   -> Liste von Tupeln
    execute(sql, params=None) -> Anzahl betroffener Zeilen
    commit()

Tabellen:
    statistics, statistics_short_term   je statistic_id (Langzeit bzw. 5-Min-Statistik)
    events                              je event_type, danach nicht mehr benutzte event_data
Gelöscht wird paketweise (BATCH Zeilen je DELETE, Commit je Paket) bis höchstens max_zeilen.
"""
BATCH = 5000
IN_CHUNK = 900          # Größe der IN-Listen beim Prüfen der event_data
TABELLEN = ("statistics", "statistics_short_term", "events")


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
