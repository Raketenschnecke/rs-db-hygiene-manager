# RS DB Hygiene Manager

Vorhaltezeiten pro Entität für die Recorder-Datenbank von Home Assistant.

![Übersicht der App: Kacheln zu Istzustand, Löschmenge sofort und Prognose, darunter die Reiter](https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/uebersicht.png)

Das Repository besteht aus zwei Teilen mit gemeinsamer Versionsnummer:

- **App** `rs_db_hygiene/`: Web-Oberfläche (Ingress) mit Analyse, Regeln, Probelauf und Löschlauf.
- **Integration** `custom_components/rs_db_hygiene/`: Gerät „RS DB Hygiene“ mit Sensoren, Knöpfen und Diensten. Sie führt die Löschung von Statistik und Events über die Recorder-Warteschlange aus.

Voraussetzung: Home Assistant OS oder Supervised ab 2026.10, Recorder mit SQLite.

## Installation

1. **App:** Einstellungen → Apps → App-Store → ⋮ → Repositories → `https://github.com/Raketenschnecke/rs-db-hygiene-manager` eintragen. Danach die App „RS DB Hygiene Manager“ installieren und starten.

   [![App-Repository in Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FRaketenschnecke%2Frs-db-hygiene-manager)
2. **Integration:** HACS → ⋮ → Benutzerdefinierte Repositories → dieselbe URL, Typ „Integration“. „RS DB Hygiene“ herunterladen und Home Assistant neu starten. Sobald die App läuft, meldet sie sich per Discovery; die Integration unter Einstellungen → Geräte & Dienste bestätigen.

   [![Repository in HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Raketenschnecke&repository=rs-db-hygiene-manager&category=integration)

Die blauen Knöpfe öffnen über [My Home Assistant](https://my.home-assistant.io/) die passende Seite in der eigenen HA-Installation (beim ersten Mal fragt My Home Assistant nach deren Adresse).

## Schnelleinstieg

**Manueller Modus:**
Home-Assistant-Datenbank vom Hygiene Manager analysieren lassen → Analyseergebnisse in den Reitern „Entitäten“ und „Events“ sichten und Löschregeln ableiten → im Reiter „Prognosen je Regel“ die prognostizierten Löschergebnisse beurteilen → im Reiter „Löschen“ den manuellen Löschvorgang starten → fertig.

**Automatik:**
In HA eine Automation anlegen, die über die Integration RS DB Hygiene einen Löschlauf startet (Aktion `rs_db_hygiene.loeschlauf_starten`) → fertig.

Gelöscht wird nur, wenn in der App-Konfiguration „Löschen freigegeben“ eingeschaltet ist; für die Automatik zusätzlich „Automatik freigegeben“.

## Updates

- **App:** erscheint als Update im App-Store bzw. unter Einstellungen → Updates.
- **Integration:** erscheint in HACS bzw. unter Einstellungen → Updates; danach Home Assistant neu starten.
- Änderungen stehen in [`rs_db_hygiene/CHANGELOG.md`](rs_db_hygiene/CHANGELOG.md).

## Hinweise

- Löschläufe nur mit Hauptschalter in der App-Konfiguration. Ohne ihn löscht die App nichts.
- Der Wert `purge_keep_days` in der `configuration.yaml` bestimmt die Obergrenze. Wenn du `auto_purge` abschaltest, übernimmt der Manager bzw. eine Automation das Löschen.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
