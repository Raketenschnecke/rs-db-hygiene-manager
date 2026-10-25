# Risikoanalyse RS DB Hygiene Manager 0.5.6

Stand: 25.10.2026 · Gegenstand: App (Add-on) `rs_db_hygiene` und Integration `custom_components/rs_db_hygiene`, jeweils Version 0.5.6.

Die Analyse beruht auf einer Durchsicht des Quellcodes durch den Autor (mit Unterstützung von Claude). Sie ist **kein unabhängiges Audit** und kein Penetrationstest. Das Frontend (`index.html`, rund 6800 Zeilen) wurde per Suchmuster und Stichproben geprüft, nicht Zeile für Zeile.

Sicherheitslücken bitte vertraulich melden: GitHub → Security → „Report a vulnerability“ im Repository.

## 1. Zusammenfassung

- Die Software baut **keine Verbindung ins Internet** auf. Es gibt keine Telemetrie und keine Update-Abfrage.
- Die App erreicht nur den Home-Assistant-Supervisor und den HA-Core (intern). Die Integration spricht nur die App an.
- In der geprüften Version wurden **keine kritischen oder hohen Schwachstellen** gefunden.
- Das größte Restrisiko liegt in den Rechten, die ein Add-on mit HA-API-Zugriff hat, und in der Wirkung von Löschläufen bei Fehlkonfiguration, nicht in einer konkreten Lücke.

## 2. Was die Software tut und welche Rechte sie hat

| Recht | Wirkung | Begründung |
|---|---|---|
| `homeassistant_api: true` | Der Supervisor-Token der App erlaubt Aufrufe der HA-Core-API, auch beliebiger Dienste | Nötig, um `recorder.purge_entities` und die Dienste der Integration aufzurufen |
| `hassio_api: true` | Lesen der eigenen App-Infos, Discovery melden | Anmeldung der Integration |
| `map: homeassistant_config:ro` | Der Konfigurationsordner ist **nur lesend** eingebunden, einschließlich `secrets.yaml` und `.storage` | Lesen der Entity-/Geräte-Registry, `configuration.yaml`, Recorder-Datenbank |
| Ingress, kein veröffentlichter Port | Die Oberfläche ist nur über HA erreichbar | – |
| Integration | Läuft im HA-Core mit dessen Rechten, löscht per SQL im Recorder | Einzige Möglichkeit, Statistik und Events zu kürzen |

Die App liest `secrets.yaml` und `.storage/auth` **nicht**. Das Dateisystem erlaubt es ihr aber, weil sich der Mount nicht auf Unterordner beschränken lässt.

## 3. Fremdsoftware

| Komponente | Quelle | Anmerkung |
|---|---|---|
| Basis-Image `ghcr.io/home-assistant/amd64-base:3.24` | Home Assistant (Alpine) | Version über Tag, nicht über Digest festgelegt |
| `python3`, `py3-aiohttp` | Alpine-Paketquellen beim Bau des Images | Keine Versionsbindung; kein pip, keine Kompilierung |
| Python-Standardbibliothek | Image | `sqlite3`, `urllib`, `hmac`, `secrets` u. a. |
| Frontend | eigener Code | Keine Bibliotheken, kein CDN, keine externen Schriften oder Skripte |
| Integration | HA-eigene Module | `requirements: []`; keine zusätzlichen Pakete |
| CI (nur GitHub) | `actions/checkout@v4`, `home-assistant/actions/hassfest@master`, `hacs/action@main` | Ohne Secrets; zwei Actions auf beweglichem Zweig |

## 4. Kommunikation

**Ins Internet: keine.**

| Von → Nach | Zweck | Schutz |
|---|---|---|
| App → `http://supervisor/…` | Backup-Status, eigene Infos, Discovery | Supervisor-Token, internes Docker-Netz |
| App → `http://supervisor/core/api/…` | Zustände lesen, Löschdienste aufrufen | Supervisor-Token |
| Integration → `http://<App>:8099/api/auto/…` | Status, Analyse und Löschlauf anstoßen | Kopf `X-DBH-Schluessel`, konstantzeitiger Vergleich |
| Browser → HA-Ingress → App | Oberfläche | HA-Anmeldung, zusätzlich Prüfung der Quell-IP (nur Ingress und lokal) |
| App → Datei | Recorder-Datenbank lesend (`mode=ro`, `PRAGMA query_only`), eigene Daten unter `/data` | Mount `:ro` |

Innerhalb des Docker-Netzes läuft die Kommunikation unverschlüsselt (HA-Standard für Supervisor-Dienste).

## 5. Angriffsflächen und Bewertung

| Nr. | Vektor | Bewertung | Maßnahme im Code |
|---|---|---|---|
| A1 | Zugriff auf Port 8099 aus dem LAN | niedrig | Kein Port veröffentlicht; Middleware lässt nur Ingress-IP und `127.0.0.1` zu |
| A2 | Anderes (kompromittiertes) Add-on ruft die App auf | niedrig | `/api/auto/*` nur mit 256-Bit-Schlüssel; übrige Pfade durch IP-Prüfung gesperrt |
| A3 | Schlüssel gelangt in falsche Hände (Backup, Config-Eintrag) | mittel | Datei `chmod 600`. Folge: Analyse starten/abbrechen; Löschlauf nur bei `automatik_freigegeben` |
| A4 | SQL-Injektion | niedrig | IDs durchgängig über `int()`, Werte als gebundene Parameter, Tabellen-/Spaltennamen aus Konstanten |
| A5 | Pfad-Traversal (Regel-Sicherungen) | niedrig | Dateiname gegen feste Regex geprüft |
| A6 | Befehlsausführung | nicht vorhanden | Kein `subprocess`, `os.system`, `eval`, `exec`, `pickle`, `yaml.load` |
| A7 | ReDoS / Regex-Eingaben | niedrig | Regelmuster über `fnmatch`; Event-Muster per Regex auf Zeichensatz und 64 Zeichen begrenzt |
| A8 | Eingaben der Integrationsdienste | niedrig | `voluptuous`-Schemas mit Wertebereichen und fester Tabellenliste |
| A9 | XSS über Entitätsnamen, Labels, Attribute | niedrig bis mittel | Ausgabe durchgängig über `esc()`; Dialoge mit `textContent`. Siehe Restpunkte |
| A10 | CSRF gegen die Oberfläche | niedrig | Kein eigener Schutz in der App (kein Origin-Check, Content-Type wird nicht verlangt); Schutz hängt an Ingress (Sitzungs-Cookie, zufälliger Pfad) |
| A11 | Große Anfragen | niedrig | Begrenzung auf 4 MB |
| A12 | Lieferkette (Alpine-Pakete, Basis-Image, CI) | niedrig | Nur Alpine/HA-Quellen; Pakete und Actions nicht an feste Versionen gebunden |

## 6. Risiken durch Fehlbedienung und Datenverlust

Die App löscht Daten in der Recorder-Datenbank. Das ist ihr Zweck und zugleich das wirksamste Risiko.

| Risiko | Schutz |
|---|---|
| Ungewollter Löschlauf | Standard **aus**: `loeschen_freigegeben: false`, `automatik_freigegeben: false`. Läufe aus HA brauchen beide Schalter |
| Zu große Löschmenge | Obergrenzen je Lauf (`loeschen_max_zeilen*`), Plan und Probelauf vor dem Löschen |
| Löschen auf veralteter Analyse | Analyse wird bei Alter über 6 h oder geänderter `purge_keep_days` vor dem Lauf erneuert |
| Löschen während eines Backups | Der Lauf prüft den Backup-Status über den Supervisor |
| Falsches Recorder-Schema | Die Integration arbeitet nur mit SQLite und dem getesteten Schema und bricht sonst ab |
| Fehlerhafte Regeln | Regelprüfung und Warnungen in der Oberfläche; Regeln werden vor Änderungen gesichert (letzte 10) |
| Beschädigte Datenbank bei Abbruch | Löschen in Paketen, Abbruch jederzeit möglich; die Datenbank selbst wird nicht verändert, außer über HAs Recorder-Task bzw. die Integration |

**Empfehlung für Anwender:** Vor dem ersten Löschlauf ein HA-Backup anlegen, mit dem Probelauf beginnen und `automatik_freigegeben` erst einschalten, wenn die Regeln geprüft sind.

## 7. Restpunkte und Maßnahmen

Keiner dieser Punkte ist ein bekannter ausnutzbarer Fehler; es sind Härtungen.

1. **Frontend-Escaping:** `esc()` maskiert das einfache Anführungszeichen nicht. Eine Stelle, an der das ausnutzbar wäre, wurde nicht gefunden. Rund 100 `innerHTML`-Stellen wurden nicht einzeln geprüft.
2. **Origin-/Content-Type-Prüfung** für schreibende Aufrufe ergänzen.
3. **CSV-Export:** Zellen, die mit `=`, `+`, `-` oder `@` beginnen, nicht maskiert (relevant nur bei Öffnen in Tabellenkalkulationen).
4. **Fehlermeldungen:** Teilweise geht `str(e)` an den Browser (nur angemeldete HA-Benutzer).
5. **Lieferkette:** Basis-Image per Digest, Alpine-Pakete mit Version, CI-Actions auf Commit-SHA festlegen.
6. **Test-Hooks:** `DBH_ERLAUBT` (zusätzliche Client-IPs) und `states_test.json` dienen nur Testumgebungen. Sie sind im Produktivbetrieb nicht gesetzt. `DBH_ERLAUBT` hebt den Ingress-Schutz bewusst auf und darf nur in abgeschotteten Testumgebungen verwendet werden.

## 8. Nicht geprüft

- Das tatsächliche Verhalten der Cookies des HA-Ingress (SameSite) in der jeweils eingesetzten HA-Version.
- Konfiguration und Härtung des Hosts, des Supervisors und anderer Add-ons.
- Rechtliche Aspekte der gespeicherten Daten (Entitätsnamen und Verlauf können personenbezogen sein; sie verlassen das System nicht).

## 9. Gesamtbewertung

Für den Betrieb im geschützten Heimnetz mit Standardeinstellungen ist das Risiko **niedrig**. Die App benötigt weitreichende Rechte, nutzt sie aber eng, spricht nicht ins Internet und löscht erst nach bewusster Freigabe. Bei aktivierter Automatik steigt das Risiko durch Fehlkonfiguration, nicht durch Angriffe von außen.
