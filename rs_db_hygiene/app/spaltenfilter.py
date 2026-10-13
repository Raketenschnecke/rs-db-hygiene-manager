"""Spaltenfilter für die Tabelle Entitäten – nur Standardbibliothek.

Parameter f_<spalte> aus der Abfrage:
  Text   f_entity_id                       enthält; mit * als Platzhalter (fnmatch)
  Status f_status                          aktiv | deaktiviert | verwaist (beide Arten) | verwaist_geloescht
  Zahl   f_rows f_rows_24h f_lts_rows f_kurz_rows f_loeschbar f_attr_avg f_attr_anteil, in MB: f_attr_bytes f_attr_zuwachs_tag f_attr_loesch
                                           >1000  >=5  <10  <=10  =0  0  100-500  0,5-2  (Tausenderpunkt, Dezimalkomma)
  Datum  f_first_ts f_last_ts              <1.9.2026  >=8.10.2026  8.10.2026  1.9.2026-30.9.2026
  Wert   f_kurzzeit f_attribute f_langzeit regel:R14 | std | wert:30 Tage
  Herkunft f_herkunft (Text in Name/Datei und Tooltip), f_herkunft_art (geraet, yaml, …), f_device_id (genau)
  Labels f_labels                          enthält bzw. * und ? in der Liste „A, B“ (Labels an Entität und Gerät, ab 0.4.10)
  Klasse f_device_class                    genau (z. B. power) | __ohne (keine device_class), ab 0.5.4 (Punkt 167)
Ungültige Eingaben werden ignoriert und als Fehler je Spalte zurückgegeben.
"""
import datetime
import fnmatch
import re
import time

ZAHL_SPALTEN = ("rows", "rows_24h", "lts_rows", "kurz_rows", "loeschbar", "attr_avg", "attr_anteil",
                "loesch_summe", "loesch_states", "loesch_kurz", "loesch_lts", "loesch_attr_zeilen",
                "attr_bytes", "attr_zuwachs_tag", "attr_loesch")
# Attribut-Größen werden in MB gefiltert (Eingabe z. B. >50 oder 0,5-2), gespeichert sind Bytes
MB_SPALTEN = ("attr_bytes", "attr_zuwachs_tag", "attr_loesch")
DATUM_SPALTEN = ("first_ts", "last_ts")
WERT_SPALTEN = ("kurzzeit", "attribute", "langzeit")

_OP = r"(<=|>=|<|>|=)?"
_Z = r"(\d[\d.]*(?:,\d+)?)"              # Tausenderpunkt erlaubt, Dezimalkomma
_ZAHL = re.compile(rf"^{_OP}\s*{_Z}$")
_ZAHL_BEREICH = re.compile(rf"^{_Z}\s*-\s*{_Z}$")
_D = r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})"
_DATUM = re.compile(rf"^{_OP}\s*{_D}$")
_DATUM_BEREICH = re.compile(rf"^{_D}\s*-\s*{_D}$")
TAG = 86400


class FilterFehler(ValueError):
    pass


def _zahl(s):
    return float(s.replace(".", "").replace(",", "."))


def _vergleich(op, x):
    return {
        "<": lambda v: v < x, "<=": lambda v: v <= x, ">": lambda v: v > x,
        ">=": lambda v: v >= x, "=": lambda v: v == x,
    }[op or "="]


def zahl_filter(ausdruck):
    a = ausdruck.strip()
    m = _ZAHL_BEREICH.match(a)
    if m:
        lo, hi = sorted((_zahl(m.group(1)), _zahl(m.group(2))))
        return lambda v: v is not None and lo <= v <= hi
    m = _ZAHL.match(a)
    if not m:
        raise FilterFehler("Zahl erwartet, z. B. >1000, <=10, 100-500")
    f = _vergleich(m.group(1), _zahl(m.group(2)))
    return lambda v: v is not None and f(v)


def _tagesbeginn(t, mo, j):
    j = int(j)
    j = j + 2000 if j < 100 else j
    try:
        datetime.date(j, int(mo), int(t))   # mktime würde 32.13. still in den Folgemonat umrechnen
        return time.mktime((j, int(mo), int(t), 0, 0, 0, 0, 0, -1))
    except (OverflowError, ValueError) as e:
        raise FilterFehler(f"ungültiges Datum: {t}.{mo}.{j}") from e


def datum_filter(ausdruck):
    """Vergleicht Zeitstempel (Sekunden) tagesgenau in der lokalen Zeitzone des Add-ons."""
    a = ausdruck.strip()
    m = _DATUM_BEREICH.match(a)
    if m:
        von = _tagesbeginn(*m.group(1, 2, 3))
        bis = _tagesbeginn(*m.group(4, 5, 6)) + TAG
        von, bis = min(von, bis - TAG), max(bis, von + TAG)
        return lambda ts: ts is not None and von <= ts < bis
    m = _DATUM.match(a)
    if not m:
        raise FilterFehler("Datum erwartet, z. B. <1.9.2026, >=8.10.2026, 1.9.2026-30.9.2026")
    op, beginn = m.group(1), _tagesbeginn(*m.group(2, 3, 4))
    ende = beginn + TAG
    f = {
        "<": lambda ts: ts < beginn, "<=": lambda ts: ts < ende, ">": lambda ts: ts >= ende,
        ">=": lambda ts: ts >= beginn, "=": lambda ts: beginn <= ts < ende, None: lambda ts: beginn <= ts < ende,
    }[op]
    return lambda ts: ts is not None and f(ts)


def text_filter(ausdruck):
    a = ausdruck.strip().lower()
    if "*" in a or "?" in a:
        return lambda s: fnmatch.fnmatchcase((s or "").lower(), a)
    return lambda s: a in (s or "").lower()


def status_filter(ausdruck):
    a = ausdruck.strip()
    if a == "aktiv":
        return lambda s: s == "aktiv"
    if a == "verwaist":
        return lambda s: s in ("verwaist", "verwaist_geloescht")     # Punkt 186: ohne die Deaktivierten
    if a == "deaktiviert":
        return lambda s: s == "deaktiviert"
    if a == "verwaist_geloescht":
        return lambda s: s == "verwaist_geloescht"
    raise FilterFehler("aktiv, deaktiviert, verwaist oder verwaist_geloescht erwartet")


def wert_filter(ausdruck):
    a = ausdruck.strip()
    if a == "std":
        return lambda w: w is not None and not w.get("regel")
    if a.startswith("regel:"):
        rid = a[6:]
        return lambda w: w is not None and w.get("regel") == rid
    if a.startswith("wert:"):
        anz = a[5:]
        return lambda w: w is not None and w.get("anzeige") == anz
    raise FilterFehler("std, regel:<ID> oder wert:<Anzeige> erwartet")


def gleich_filter(ausdruck):
    a = ausdruck.strip()
    return lambda v: (v or "") == a


def klasse_filter(ausdruck):
    a = ausdruck.strip()
    return gleich_filter("" if a == "__ohne" else a)


def _wert_der_spalte(e, spalte):
    if spalte == "herkunft":
        return f"{e.get('herkunft') or ''} {e.get('herkunft_info') or ''}"
    if spalte == "loeschbar":
        return (e.get("loeschbar") or {}).get("states")
    if spalte in MB_SPALTEN:
        v = e.get(spalte)
        return None if v is None else v / 1e6
    if spalte in WERT_SPALTEN:
        return (e.get("werte") or {}).get(spalte)
    return e.get(spalte)


def baue(parameter):
    """parameter: Mapping (z. B. request.query). Liefert (prädikat, fehler je Spalte, aktive Filter)."""
    tests, fehler, aktiv = [], {}, {}
    for schluessel, roh in parameter.items():
        if not schluessel.startswith("f_") or not str(roh).strip():
            continue
        spalte = schluessel[2:]
        try:
            if spalte in ("entity_id", "herkunft", "labels"):
                f = text_filter(roh)
            elif spalte in ("herkunft_art", "device_id"):
                f = gleich_filter(roh)
            elif spalte == "device_class":
                f = klasse_filter(roh)
            elif spalte == "status":
                f = status_filter(roh)
            elif spalte in ZAHL_SPALTEN:
                f = zahl_filter(roh)
            elif spalte in DATUM_SPALTEN:
                f = datum_filter(roh)
            elif spalte in WERT_SPALTEN:
                f = wert_filter(roh)
            else:
                continue
        except FilterFehler as x:
            fehler[spalte] = str(x)
            continue
        aktiv[spalte] = roh
        tests.append((spalte, f))

    def praedikat(e):
        return all(f(_wert_der_spalte(e, sp)) for sp, f in tests)

    return praedikat, fehler, aktiv


def optionen(entitaeten):
    """Auswahlwerte für die Wertspalten: vorkommende Regel-IDs und Anzeigewerte."""
    out = {}
    for art in WERT_SPALTEN:
        regeln, werte = set(), set()
        for e in entitaeten:
            w = (e.get("werte") or {}).get(art)
            if not w:
                continue
            if w.get("regel"):
                regeln.add(w["regel"])
            werte.add(w.get("anzeige"))
        out[art] = {"regeln": sorted(regeln, key=lambda r: (r.startswith("E:"), r)), "werte": sorted(werte, key=_sortwert)}
    # Punkt 167: vorkommende Klassen (device_class) für die Auswahl im Spaltenfilter
    out["device_class"] = sorted({e.get("device_class") for e in entitaeten if e.get("device_class")})
    return out


def _sortwert(anzeige):
    """Sortiert '7 Tage' < '30 Tage' < '1 Jahr' < Sonderwerte."""
    m = re.match(r"^(\d+) (Tag|Tage|Woche|Wochen|Monat|Monate|Jahr|Jahre)$", anzeige or "")
    if not m:
        return (1, anzeige or "")
    faktor = {"Tag": 1, "Tage": 1, "Woche": 7, "Wochen": 7, "Monat": 30, "Monate": 30, "Jahr": 365, "Jahre": 365}[m.group(2)]
    return (0, int(m.group(1)) * faktor)
