# RS DB Hygiene Manager

Vorhaltezeiten pro Entität für die Recorder-Datenbank von Home Assistant.

![Übersicht der App: Kacheln zu Istzustand, Löschmenge sofort und Prognose, darunter die Reiter](https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/uebersicht.png)

Das Repository besteht aus zwei Teilen mit gemeinsamer Versionsnummer:

- **App** `rs_db_hygiene/`: Web-Oberfläche (Ingress) mit Analyse, Regeln, Probelauf und Löschlauf.
- **Integration** `custom_components/rs_db_hygiene/`: Gerät „RS DB Hygiene“ mit Sensoren, Knöpfen und Diensten. Sie führt die Löschung von Statistik und Events über die Recorder-Warteschlange aus.

Voraussetzung: Home Assistant OS oder Supervised ab 2026.10, Recorder mit SQLite.

## Installation

1. **App:** Einstellungen → Apps → App-Store → ⋮ → Repositories → `https://github.com/Raketenschnecke/rs-db-hygiene-manager` eintragen. Danach die App „RS DB Hygiene Manager“ installieren und starten.

Alternativ hier:

   [![App-Repository in Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FRaketenschnecke%2Frs-db-hygiene-manager)

3. **Integration:** HACS → ⋮ → Benutzerdefinierte Repositories → dieselbe URL, Typ „Integration“. „RS DB Hygiene“ herunterladen und Home Assistant neu starten. Sobald die App läuft, meldet sie sich per Discovery; die Integration unter Einstellungen → Geräte & Dienste bestätigen.

Alternativ hier:

   [![Repository in HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Raketenschnecke&repository=rs-db-hygiene-manager&category=integration)


## Schnelleinstieg

**Manueller Modus:**
Home-Assistant-Datenbank vom Hygiene Manager analysieren lassen → Analyseergebnisse in den Reitern „Entitäten“ und „Events“ sichten und Löschregeln ableiten → im Reiter „Prognosen je Regel“ die prognostizierten Löschergebnisse beurteilen → im Reiter „Löschen“ den manuellen Löschvorgang starten → fertig.

**Automatik:**
In HA eine Automation anlegen, die über die Integration RS DB Hygiene einen Löschlauf startet (Aktion `rs_db_hygiene.loeschlauf_starten`) → fertig.

Gelöscht wird nur, wenn in der App-Konfiguration „Löschen freigegeben“ eingeschaltet ist; für die Steuerung via Automation muss zusätzlich „Automatik freigegeben“ aktiviert sein.

## Empfohlene Einrichtung

Die Idee: Home Assistant bewahrt grundsätzlich alles lange auf, der Hygiene Manager kürzt gezielt, was du nicht so lange brauchst. Das geht in drei Schritten.

### 1. Recorder in der `configuration.yaml`

```yaml
recorder:
  purge_keep_days: 3650   # Beispiel und Empfehlung: 10 Jahre als Obergrenze für alles ohne eigene Regel (bis 7300 möglich)
  auto_purge: false       # HAs nächtliches Aufräumen übernimmt die Automation „Purge und Repack“
  auto_repack: false      # das monatliche Verdichten ebenfalls
```

- **`purge_keep_days`** ist die längste Vorhaltezeit; 3650 Tage (10 Jahre) sind ein Beispiel und unsere Empfehlung als Startwert. Alles ohne eigene Regel („global“) bleibt so lange in der DB. Regeln können nur kürzen; eine Regel mit längerer Zeit wird auf diesen Wert gedeckelt.
- **`auto_purge: false`** schaltet HAs eigenes Löschen um 04:12 ab. So löscht nachts zuerst der Hygiene Manager nach deinen Regeln und danach HA alles, was älter als `purge_keep_days` ist.
- **`auto_repack: false`** schaltet HAs eigenes Verdichten (2. Sonntag im Monat) ab. Das Repack macht die Automation „Purge und Repack“ zum selben Termin.
- Wichtig: `auto_purge` und `auto_repack` erst abschalten, wenn die Automation „Purge und Repack“ angelegt ist. Sonst räumt HA gar nicht mehr auf, und die Datei wird nie kleiner. Nach der Änderung Konfiguration prüfen und HA neu starten.

### 2. Automation „Löschlauf“

Startet jede Nacht einen Löschlauf nach den aktiven Regeln. Ist die Analyse älter als 6 Stunden, analysiert die App vorher neu. Ist nichts zu löschen oder ein Schalter aus, passiert nichts.

```yaml
alias: RS DB Hygiene – Löschlauf
mode: single
triggers:
  - trigger: time
    at: "03:00:00"
conditions:
  - condition: state
    entity_id: sensor.rs_db_hygiene_status
    state: bereit
actions:
  - action: rs_db_hygiene.loeschlauf_starten
  - wait_for_trigger:
      - trigger: event
        event_type: rs_db_hygiene_lauf_beendet
    timeout: "03:00:00"
    continue_on_timeout: true
  - action: persistent_notification.create
    data:
      title: RS DB Hygiene
      message: >-
        {% if wait.trigger %}Löschlauf {{ wait.trigger.event.data.status }}:
        {{ wait.trigger.event.data.geloescht }} Zeilen in {{ wait.trigger.event.data.dauer_s }} s.
        {% else %}Löschlauf nach 3 h nicht beendet.{% endif %}
```

Die Benachrichtigung am Ende ist optional. Das Event `rs_db_hygiene_lauf_beendet` liefert außerdem `attributzeilen`, `umfang` und `fehler`.

### 3. Automation „Purge und Repack“ (ersetzt `auto_purge` und `auto_repack`)

Löscht jede Nacht nach dem Löschlauf alles, was älter als `purge_keep_days` ist (auch Daten, die keine Regel erfasst, z. B. Laufprotokolle des Recorders). Am 2. Sonntag im Monat verdichtet sie die Datenbank zusätzlich (Repack). Erst dadurch wird die Datei kleiner; vorher wird der freie Platz nur für neue Daten wiederverwendet.

```yaml
alias: RS DB Hygiene – Purge und Repack
mode: single
triggers:
  - trigger: time
    at: "05:00:00"
actions:
  # läuft noch ein Löschlauf, erst sein Ende abwarten
  - wait_template: "{{ not is_state('sensor.rs_db_hygiene_status', 'loeschlauf') }}"
    timeout: "03:00:00"
  - action: recorder.purge
    data:
      repack: "{{ now().weekday() == 6 and 8 <= now().day <= 14 }}"
```

- `recorder.purge` ohne `keep_days` nimmt den Wert `purge_keep_days` aus der `configuration.yaml`, beide passen also immer zusammen.
- Ein Repack sperrt den Recorder für einige Minuten (bei großen Datenbanken länger) und braucht vorübergehend freien Platz in Größe der Datenbank. Neue Werte puffert der Recorder in dieser Zeit und schreibt sie danach.

## Updates

- **App:** erscheint als Update im App-Store bzw. unter Einstellungen → Updates.
- **Integration:** erscheint in HACS bzw. unter Einstellungen → Updates; danach Home Assistant neu starten.
- Änderungen stehen in [`rs_db_hygiene/CHANGELOG.md`](rs_db_hygiene/CHANGELOG.md).

## Hinweise

- Löschläufe sind nur mit aktiviertem Hauptschalter in der App-Konfiguration möglich. Ohne ihn löscht die App nichts.
- Der Wert `purge_keep_days` in der `configuration.yaml` bestimmt die max. Vorhaltedauer der State- und 5Min Werte in der DB. Wenn du `auto_purge` abschaltest, übernimmt der Manager bzw. eine Automation das Management der HA SQLite Daten.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
