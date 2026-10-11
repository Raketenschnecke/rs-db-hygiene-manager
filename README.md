# RS DB Hygiene Manager

Vorhaltezeiten pro Entität für die Recorder-Datenbank von Home Assistant.

![Übersicht der App: Kacheln zu Istzustand, Löschmenge sofort und Prognose, darunter die Reiter](https://raw.githubusercontent.com/Raketenschnecke/rs-db-hygiene-manager/main/docs/uebersicht.png)

Das Repository besteht aus zwei Teilen mit gemeinsamer Versionsnummer:

- **App** `rs_db_hygiene/`: Web-Oberfläche (Ingress) mit Analyse, Regeln, Probelauf und Löschlauf.
- **Integration** `custom_components/rs_db_hygiene/`: Gerät „RS DB Hygiene“ mit Sensoren, Knöpfen und Diensten. Sie führt die Löschung von Statistik und Events über die Recorder-Warteschlange aus.

Voraussetzung: Home Assistant OS oder Supervised ab 2026.10, Recorder mit SQLite.

## Installation

1. **App:** Einstellungen → Apps → App-Store → ⋮ → Repositories → `https://github.com/Raketenschnecke/rs-db-hygiene-manager` eintragen. Danach die App „RS DB Hygiene Manager“ installieren und starten.
2. **Integration:** HACS → ⋮ → Benutzerdefinierte Repositories → dieselbe URL, Typ „Integration“. „RS DB Hygiene“ herunterladen und Home Assistant neu starten. Sobald die App läuft, meldet sie sich per Discovery; die Integration unter Einstellungen → Geräte & Dienste bestätigen.

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
