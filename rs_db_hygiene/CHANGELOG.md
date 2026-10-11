# Changelog

Kurzfassung je Version.
Alle 0.1.x-Versionen: nur Analyse und Probelauf – es wird nichts gelöscht.
Ab 0.2.0: Löschen der Kurzzeit-States per recorder.purge_entities, nur auf Knopfdruck und mit Hauptschalter.

## 0.5.2
- **Regeln verwalten (Punkte 123, 124):** neuer Knopf im Reiter Regeln. Darüber lassen sich die Regeln als JSON exportieren und importieren (mit Prüfung, Vorschau und Rückfrage), auf die Startregeln zurücksetzen und frühere Stände wiederherstellen. Vor jedem Ersetzen sichert die App die bisherigen Regeln; die letzten 10 Sicherungen bleiben. Solange ein Löschlauf läuft, ist das Ersetzen gesperrt
- **Knopf „Analyse starten“ (Punkt 125):** doppelt so breit wie hoch. Der Stand-Text daneben entfällt; der Stand steht jetzt im Tooltip und in der Rückfrage. Ist eine Analyse veraltet, zeigt ein gelbes Ausrufezeichen am Knopf das an
- Hilfe: zwei neue FAQ (Regeln von vorn, Regeln mitnehmen)
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer; der HA-Neustart für ihr HACS-Update kann bis zum nächsten geplanten Neustart warten

## 0.5.1
- **Häkchen und Scrollbalken im App-Design (Punkt 122):** Checkboxen zeigen das Blau der App, Scrollbalken den Grauton der Reiterränder; im dunklen Modus sind beide dunkel statt weiß
- Nur die Web-GUI ändert sich. Die Integration bekommt nur die neue Versionsnummer; ihr Update in HACS ist inhaltlich gleich, der dafür gemeldete HA-Neustart kann bis zum nächsten geplanten Neustart warten

## 0.5.0
- **Neue Heimat GitHub:** App über das App-Store-Repository, Integration über HACS – Updates kommen ab jetzt über HA bzw. HACS
- App und Integration haben ab jetzt dieselbe Versionsnummer
- Funktional unverändert gegenüber App 0.4.12 und Integration 0.4.5
- **Umstieg von der lokalen App:** neue App-Kennung, deshalb einmalig Regeln und Löschprotokoll übernehmen; die Integration findet die neue App selbst

## 0.4.12
- **Löschen nach vollen Kalendertagen (Punkt 121):** Die Grenze ist jetzt immer Mitternacht (Ortszeit) statt „jetzt minus N × 24 Stunden“. Vorhaltung 14 Tage = heute plus 14 volle Tage; Kurzzeit „keine“ = alles vor heute 0:00. Gilt für States, 5-Min-Statistik, Langzeit und Events
- **Braucht die Integration RS DB Hygiene 0.4.5 und danach einen HA-Neustart.** Bis dahin löscht die App wie bisher („Grenze: jetzt − keep_days“ im Tab Löschen)
- Analyse und Plan zählen in lokalen Kalendertagen statt in UTC-Tagen; nach dem Update läuft einmal automatisch eine Analyse

## 0.4.11
- **„erster“ und „letzter“ über alle Datenarten (Punkte 118, 120):** Die Spalten im Tab Entitäten heißen jetzt „erster/letzter (States, 5-Min, Langzeit)“ und zeigen den frühesten bzw. spätesten Zeitstempel aus States, 5-Min-Statistik und Langzeit (bisher nur States); der Tooltip der Zelle zeigt die Zeiten je Datenart, das CSV hat sie als eigene Spalten. Nach dem Update läuft einmal automatisch eine Analyse (neuer Cache-Aufbau)
- **Hilfe (Punkt 119):** Verweise im Text springen direkt zum Thema (Abschnitt öffnet sich, Ziel wird kurz hervorgehoben); „Häufige Fragen“ heißt jetzt „FAQ“

## 0.4.10
- **Spalte „Labels“ im Tab Entitäten (Punkt 115):** HA-Labels der Entität und ihres Geräts, Tooltip „an der Entität“ / „am Gerät“; Klick auf ein Label filtert danach, sortierbar, im CSV – so lassen sich z. B. alle Präsenzmelder auf einmal auswählen
- **Löschtabelle (Punkte 113, 114):** Tooltip je Zelle „Vorhaltung · in diesem Lauf“ erklärt die Kürzel mit den Werten der Zeile; Spalte „Status“ heißt jetzt „Entität“ und zeigt „verwaist (gelöscht)“
- **Backup-Pause im Löschprotokoll (Punkt 116):** neue Spalte „davon Backup-Pause“; auch in den Einzelheiten und im Tab Löschen beim letzten Lauf
- **Ruhigeres App-Protokoll (Punkt 117):** langsame Schritte erst ab 10 s als Warnung, darunter als Info
- **Hilfe:** neue Fragen zu „passiv“ in der Löschtabelle und zu Backups während eines Laufs; Backup-Zeile berichtigt

## 0.4.9
- **Reiter „Hilfe“ (Punkt 112):** rechts in der Reiterleiste – Einstieg, Datenarten, Werte einer Vorhaltezeit, Regeln und Rangfolge, Löschlauf, Sicherheit, häufige Fragen
- **Regeltyp „Schutz“ (Punkt 111):** Entitäten nie kürzen – höchster Rang (auch vor E:-Regeln), Kurzzeit global und Langzeit unbegrenzt fest, Platzhalter erlaubt, gilt auch für Verwaiste und Zähler; 🔒 in den Tabellen; „Schutz aus Auswahl anlegen“ im Tab Entitäten
- **Backups (Punkt 110):** Läuft ein Backup, wartet ein Löschlauf (bis 2 h), statt nach 10 min mit „Stillstand“ abzubrechen; ein neuer Lauf startet erst danach
- **Gesprächigeres App-Protokoll (Punkt 109):** Löschlauf jede Minute mit Fortschritt, Analysen je Schritt und alle 30 s ein Lebenszeichen
- **Status für HA hängt nicht mehr (Punkt 108):** Die App antwortet sofort aus dem letzten Stand und rechnet im Hintergrund nach; langsame Schritte stehen im Protokoll
- **Platzhalter bei Typ entity (Punkt 106):** Rückfrage beim Speichern („auf glob ändern?“) und Hinweis in der Regeltabelle
- **Knopf „Analyse starten“ rot (Punkt 107)**, lange IDs in Dialogen brechen um (Punkt 105)
- **Integration 0.4.4:** einzelne Aussetzer der App machen die Entitäten nicht mehr sofort „nicht verfügbar“ (erst nach 3 Fehlschlägen), Fehlermeldungen mit Typ – **HA-Neustart nötig**

## 0.4.8
- **Werte ändern in der Mehrfachauswahl (Punkt 104):** Tab Regeln, „Werte ändern …“ setzt Kurzzeit und/oder Langzeit aller ausgewählten Regeln in einer Maske („unverändert lassen“, „nicht setzen“ oder ein Wert), mit Rückfrage und Warnungen je Regel
- **„wirksam“ und Löschmenge sofort mit purge_keep_days (Punkt 102):** Bisher rechneten sie mit dem Prognosezeitraum (z. B. „wirksam 365“ bei Events trotz purge_keep_days 7300). Jetzt gilt überall purge_keep_days wie im Löschlauf; nur die Prognose rechnet höchstens mit dem Prognosezeitraum
- **Untergrenze 1 Tag (Punkt 100):** Kurzzeit „keine“ zählt in Kacheln, Prognosen und Entitäten wie im Löschlauf erst ab 1 Tag; der Tab Löschen nennt Zeilen, die deshalb noch warten
- **configuration.yaml hat Vorrang (Punkt 103):** Die App-Option purge_keep_days wirkt nur noch, wenn sie kleiner ist; ein größerer Wert wird ignoriert (Hinweis im Banner). Neuer Hilfetext, empfohlen ist 0
- **Prognosen je Regel (Punkt 101):** Spalten heißen „Entitäten mit Kurzzeit/Langzeit aus dieser Regel“
- Integration unverändert (0.4.3) – kein HA-Neustart nötig

## 0.4.7
- **Regeln direkt öffnen (Punkt 97):** Im Tab Löschen öffnet ein Klick auf einen Regel-Chip die Regel zum Bearbeiten, in „Prognosen je Regel“ ein Klick auf die Zeile bzw. den Chip
- **Kopfzeile (Punkt 98):** 10 px mehr Abstand zum Kachelblock; „Analyse starten“ als quadratischer Knopf (zweizeilig) in der Höhe der Überschrift, alles senkrecht mittig
- **Reiter (Punkt 99):** Schrift größer (16,5 px)
- Integration unverändert (0.4.3) – kein HA-Neustart nötig

## 0.4.6
- **Regeln entfernen (Punkt 93):** in der Mehrfachauswahl im Tab Regeln der Knopf „Entfernen“, mit Rückfrage; es ändert sich nur die Regeldatei, keine Daten
- **Tabellenköpfe (Punkt 94):** Titel etwas größer (14,5 px) und immer oben ausgerichtet
- **Reiter (Punkt 95):** Text aller Reiter fett
- **Kopfzeile (Punkt 96):** „Analyse starten“ ganz rechts, der Stand links davor in derselben Zeile („Analyse Stand … · vor … · **Laufzeit**“), mittig zwischen Seitenrand und Kachelblock. Bei schmalen Fenstern bleibt alles rechts; Überschrift und Stand werden gekürzt, der volle Text steht im Tooltip
- Integration unverändert (0.4.3) – kein HA-Neustart nötig

## 0.4.5
- **Ein Knopf „Analyse starten“ (Punkt 90):** rechts in der Kopfzeile; führt die Analyse und direkt danach die Attribut-Analyse aus (Fortschritt „Schritt 1/2“, „2/2“). Darunter der Stand der zuletzt gelaufenen Analyse und fett die Laufzeit beider zusammen; orange, wenn die Attribut-Analyse älter als 7 Tage ist. Überschrift jetzt „RS DB Hygiene Manager“. Die Knöpfe der Integration in HA bleiben getrennt
- **Massenauswahl in Entitäten und Events (Punkt 91):** Spalte „Auswahl“ mit Kästchen; im Kopf wählt es bei Entitäten die angezeigte Seite, in der Leiste gibt es „alle N gefilterten auswählen“. Aktionen: „Regel aus Auswahl anlegen“ und „Auswahl als CSV“ (Entitäten), „Event-Regel aus Auswahl anlegen“ (Events). Im Tab Regeln entfällt „Auswahl aufheben“
- **Tabellenköpfe (Punkt 92):** Titel in allen Tabellen größer, fett und in Textfarbe; ein Klammerteil bleibt klein und grau
- Integration unverändert (0.4.3) – kein HA-Neustart nötig

## 0.4.4
- **Fehler behoben (Punkt 87):** Die Konfiguration ließ sich nach dem Update auf 0.4.3 nicht speichern („Missing option loeschen_max_zeilen_b“). Die Option ist jetzt optional; ohne Wert gilt 2.000.000
- **Banner (Punkte 88, 89):** je Hinweis ein eigenes Banner, der Titel fett („Löschen gesperrt:“, „Automatik gesperrt:“, „purge_keep_days:“, „Regeln länger als purge_keep_days:“); richtige Fälle im Text („mit 7.300 Tagen“)
- **Reiterleiste ohne senkrechten Scrollbalken (Punkt 80)**; bei schmalen Fenstern bleibt das waagerechte Blättern
- **Lange Spaltenköpfe** brechen in allen Tabellen um, statt abgeschnitten zu werden (Punkt 78)
- **Tab Löschen (Punkt 75):** „Dieser Lauf“, Rückfrage und Sensor „Geplante Zeilen“ nennen je Tabelle nur, was in diesem Lauf gelöscht wird; dazu „Insgesamt fällig (alle Tabellen)“
- **Monatssumme (Punkt 76):** Bei Paketen mit genau einer Entität bzw. einem Event-Typ zählt die tatsächlich gelöschte Zahl (gilt für neue Läufe)
- **Texte (Punkt 77):** Startmeldung, Beschreibung und Hauptschalter nennen die Läufe aus HA (Automatik)
- Die Integration bleibt 0.4.3 – kein HA-Neustart nötig

## 0.4.3
- **Kachelraster oben (Punkt 86):** 3 Zeilen (Istzustand, Löschmenge sofort, Prognose) × 7 Spalten (DB-Größe, Datenzeilen gesamt, States/5-Min, Langzeit, Events, Attribute, Verwaist) mit dezent rot/gelb/grün abgestuften Rahmen. Kein waagerechtes Scrollen: Bei schmalen Fenstern werden Zahlen und Köpfe gekürzt, der volle Wert steht im Tooltip
- **Prüfung purge_keep_days (Punkt 85):** Der Infotext „HA Recordereinstellung …“ im Kopf entfällt. Ist purge_keep_days in der configuration.yaml kleiner als die App-Option oder als die Kurzzeit bzw. Event-Vorhaltezeit aktiver Regeln, erscheint ein Hinweis im gelben Banner mit Lösungsweg (auch beim Start im App-Log)
- **Statistik und Events mit eigener Obergrenze (Punkt 82):** neue Option „Höchstens gelöschte Zeilen pro Lauf (Statistik und Events)“, Standard 2.000.000. Stufe B läuft zuerst und kommt so auch bei großem States-Rückstand an die Reihe
- **Frischer Plan (Punkt 83):** Läufe aus HA analysieren vorher neu, wenn die Analyse älter als 6 h ist oder sich purge_keep_days geändert hat. Darf der Lauf danach nicht starten, steht „nicht gestartet“ im Löschprotokoll (die Automation bekommt trotzdem ihr Ende-Event). Im Tab Löschen sperrt ein geänderter purge_keep_days bis zur nächsten Analyse
- **Verwaiste, die wieder auftauchen (Punkt 84):** Der Lauf prüft vor jedem Paket, ob verwaiste Entitäten inzwischen wieder in HA existieren (z. B. Blitz- oder Waldbrand-Meldungen), und überspringt sie; das Protokoll nennt sie
- **Integration 0.4.3 (Punkt 81):** Sensor „Prognose DB Größe (Vorhaltezeit)“ statt „(1 Jahr)“ mit den Attributen tage und jahre. Für die Integration ist ein HA-Neustart nötig

## 0.4.2
- **20 Jahre Vorhaltung (Punkt 79):** Kurzzeit, 5-Min-Statistik, Langzeit und Events lassen sich jetzt bis 7300 Tage löschen (bisher States höchstens 365, Statistik und Events höchstens 3650 Tage). Damit wirken z. B. Regeln „5 Jahre“, wenn purge_keep_days 7300 ist
- Tab Regeln: neuer Hinweis, wie viele aktive Entitäten keine eigene Kurzzeit haben, also „global“ = purge_keep_days, und was sich davon in dieser Zeit ansammelt
- Tage ab einem Jahr mit Jahresangabe, z. B. „7.300 Tage (20 Jahre)“; Prognosezeitraum bis 20 Jahre; App-Option purge_keep_days bis 7300
- Für die Integration RS DB Hygiene 0.4.2 (keep_days bis 7300) ist ein HA-Neustart nötig

## 0.4.1
- **Mehrfachsortierung** in allen Tabellen: Klick sortiert nach einer Spalte, Umschalt+Klick fügt eine Stufe hinzu (nochmals: umkehren, ein drittes Mal: entfernen); Anzeige „↓1“, „↑2“. Die Sortierung bleibt je Tabelle gemerkt, „Filter zurücksetzen“ setzt sie zurück
- Löschprotokoll: Zeilenauswahl für „Läufe“ (10/25/50/250) und die Monatssumme (50/250/1000/2500); „Einzelheiten je Lauf“ mit Filtern und Summen
- Prognosezeitraum bleibt im Browser gemerkt
- Gelber Hinweis oben nur noch, wenn „Löschen freigegeben“ oder „Automatik freigegeben“ aus ist; Tab „Löschen“ mit Chip „Automatik an/aus“ und neuer Erläuterung; leerer Plan zeigt „nichts zu löschen“
- Einheitliche Beschriftung: Kurzzeit (States, 5-Min, Attribute), Langzeit (Stundenstatistik, 1 h), Vorhaltezeit (Events)
- Kopfzeile: „HA Recordereinstellung: 365 Tage (configuration.yaml/recorder/purge_keep_days)“
- Sperrtexte nennen den Ort der Schalter; ein Neustart der App ist nach dem Umschalten nicht nötig
- Für die Integration RS DB Hygiene 0.4.1: Attribut-Analyse aus HA starten (Knopf, Dienst), Sensor „Letzte Attribut-Analyse“ – dafür ist ein HA-Neustart nötig

## 0.4.0
- **Automatisierung:** Die App meldet sich bei HA (Discovery). Die Integration RS DB Hygiene 0.4.0 stellt das Gerät „RS DB Hygiene“ bereit: Knöpfe (Löschlauf starten, Analyse starten, Abbrechen), Sensoren (Status, Fortschritt, geplante Zeilen, letzter Lauf, DB-Größe, Prognose), Binärsensor „Läuft“, Dienst rs_db_hygiene.loeschlauf_starten und Event rs_db_hygiene_lauf_beendet
- Neue Option „Automatik freigegeben“ (ab Werk aus): erlaubt Läufe aus HA heraus, zusätzlich zu „Löschen freigegeben“
- Beispiel-Automation für den Nachtlauf mit anschließendem Purge und wöchentlichem Repack (Projektordner)
- Alle Tabellen mit Löschmengen im gleichen Aufbau: gelöschte Zeilen | davon States | 5-Min | Langzeit | Events | Attributzeilen (mit den States, nicht in der Summe)
- Tab „Löschen“: eine Zeile je Entität bzw. Event-Typ; Rückfrage mit Attributzeilen
- Löschprotokoll: Attributzeilen im Lauf gemessen, Spalte „Auslöser“ (manuell/automatik)
- Prognosen je Regel: Attribut-Ersparnis wieder bei der Kurzzeit-Regel
- Für die neue Integration ist ein HA-Neustart nötig

## 0.3.4
- Attribute haben keine eigene Vorhaltezeit mehr: Sie werden immer mit den Kurzzeit-States gelöscht (Eingabe „Attribute“ und die Spalte „Attribute“ entfallen; ein gesetzter Wert wird beim Laden verworfen)
- Angaben zu den Attribut-Datenmengen bleiben: Kacheln, Spalten im Tab Entitäten (Ansicht „Attribute“), „davon Attribute“ in Prognosen je Regel
- Texte und Hinweise angepasst („inkl. Attribute – sie werden mit den States gelöscht“)

## 0.3.3
- Rückfrage „Löschlauf starten“ mit Knopf „Starten“; sie nennt je Tabelle nur, was wirklich gelöscht wird
- Neue Event-Regeln bekommen die ID EV:<Event-Typ>; Warnung, wenn eine andere Event-Regel dasselbe Muster hat
- Löschprotokoll: „geplant (Schätzung)“ und neue Spalte „gezählt“ als Vergleich zu „gelöscht“

## 0.3.2
- Neuer Reiter „Events“ (Übersicht je Event-Typ, Knopf „Neue Event-Regel“); im Tab Regeln Schnellfilter „nur Event-Regeln“
- Warnungen vor dem Speichern bzw. Aktivschalten einer Regel: Energie-Dashboard und Zähler (Langzeit), Kurzzeit unter 7 Tagen, Quellen von Helfern mit Verlauf (history_stats, statistics, filter, trend), breite Event-Muster
- Tab „Löschen“: „Risiken dieses Laufs“
- Obergrenze: Entitäten, die genau gezählt nicht mehr passen, werden mit größerem keep_days nachgezogen statt übersprungen; ist schon der älteste Tag größer als die Grenze, kann sie für einen Lauf einmalig überschritten werden
- Integration unverändert, kein HA-Neustart nötig

## 0.3.1
- Events in der Regelliste: neuer Regeltyp „event“ (Muster = Event-Typen, auch mit *, Wert = Vorhaltezeit); aktiv/passiv, Treffer, Vorschau und Prognosen je Regel wie bei anderen Regeln
- Die bisherige Event-Einstellung wird automatisch zur Regel EV:call_service (7 Tage)
- „Events je Typ“ zeigt die gültige Regel; Klick öffnet sie oder legt eine an
- Ohne aktive Integration zählt Stufe B nicht mehr in die geplante Menge („übersprungen“)
- Integration unverändert, kein HA-Neustart nötig

## 0.3.0
- **Stufe B:** löscht zusätzlich 5-Min-Statistik, Langzeitstatistik und Events, über die neue Integration „RS DB Hygiene“ (Dienst rs_db_hygiene.purge, läuft in der Warteschlange des Recorders)
- Integration einmalig einrichten: nach dem HA-Neustart unter Einstellungen → Geräte & Dienste → „RS DB Hygiene“ hinzufügen; ohne sie überspringt die App Stufe B
- Events: Vorhaltezeit je Event-Typ im Tab Regeln („Events je Typ“), Startwert call_service 7 Tage, sonst global; neue Kachel „Löschmenge sofort: Events“
- Langzeit „keine“: alle Stundenwerte löschen (bei Verwaisten samt Meta)
- Tab „Löschen“ mit Spalte Tabelle und Umfang „Events“; Obergrenze gilt für alles zusammen
- Löschprotokoll und Monatssumme getrennt nach States / 5-Min / Langzeit / Events
- Erster Start analysiert neu (Events je Typ, ca. 50 s)

## 0.2.1
- Neuer Reiter „Löschprotokoll“: alle Läufe mit Filtern und CSV, dazu „Gelöscht je Entität und Monat“
- Aufbewahrung des Protokolls einstellbar (App-Option protokoll_tage, Standard 365 Tage); die Monatssumme bleibt immer erhalten
- Reiter deutlicher abgesetzt; „Löschen“ und „Löschprotokoll“ mit roter Linie
- Tab „Löschen“: Hinweis auf verwaiste Entitäten, die eine glob-Regel nicht erfasst, mit Knopf „Regel ‚verwaist' anlegen“; Zeile „Nicht in diesem Lauf“ (Langzeit, 5-Min-Statistik, Attribute, Events)
- Bei Attribute und Langzeit: „wird noch nicht gelöscht (Stufe B)“
- Laufhinweis zählt gelöschte Zeilen live und zeigt die genau gezählte Menge
- Kopfzeile: purge_keep_days „laut configuration.yaml“; Sperrgründe mit Klarnamen

## 0.2.0
- **Löschfunktion Stufe A:** neuer Tab „Löschen“ mit Plan, „Jetzt ausführen“ (alle aktiven Regeln oder eine Regel) und Protokoll der letzten Läufe
- Gelöscht werden nur Kurzzeit-States über den HA-Dienst recorder.purge_entities; Statistik, Events und Recorder-Einstellungen bleiben unberührt, die App schreibt nicht selbst in die DB
- Hauptschalter „Löschen freigegeben“ in der App-Konfiguration (ab Werk aus), Obergrenze 5 Mio. Zeilen pro Lauf (einstellbar)
- Nur aktive Regeln, nur Kurzzeit unter purge_keep_days, Untergrenze 1 Tag; vor jedem Paket wird nachgezählt
- Laufhinweis mit Fortschritt und Abbruch; danach startet automatisch eine Analyse
- Kein Nachtlauf zum Löschen – die nächtliche Analyse bleibt nur lesend

## 0.1.7
- Eingabe der Vorhaltezeiten aufgeräumt: Einstellung in Zeile 1, Zahl und Einheit nebeneinander in Zeile 2, alle drei Spalten auf gleicher Höhe
- Eigene Vorhaltezeiten (E:-Regeln) haben immer Vorrang – unabhängig von ihrer Position; Warnung, wenn eine weitere Entitätsregel dieselbe Entität nennt
- Nachvollziehbar: Der Entitäts-Dialog zeigt je Wert die gewinnende und die überstimmten Regeln; die Regel-Vorschau zeigt, wo eine Regel überstimmt wird
- E:-Regeln gelten weiter, wenn die Entität verwaist (Hinweis „Entität verwaist“ an der Regel)
- Erstmals mit Changelog im Update-Dialog

## 0.1.6
- Begriffe: „Löschmenge sofort“ (Bestand) und „Ersparnis (Dauerbetrieb)“ (Zufluss); Tab heißt „Prognosen je Regel“, mit ⓘ-Erklärung
- Attribute separat: Kachelgruppe „Attribute“, fünf Attributspalten in der Tabelle Entitäten, Ersparnis aufgeteilt in States / Attribute / Langzeit
- Spaltenauswahl „Spalten …“ in allen Tabellen (Ansichten Standard / Attribute / Alle)
- Prognosezeitraum in Jahren (Standard 1 Jahr); Langzeitstatistik rechnet über denselben Zeitraum
- Neue Regel: ID-Vorschlag (nächste R-Nummer)
- Neuer Regeltyp „verwaist“ (alle / gelöscht / ohne Spur, Vorlage 30 Tage; Kurzzeit „keine“ nur mit Rückfrage)

## 0.1.5
- Tab Regeln: Schalter aktiv/passiv, Massenauswahl mit „Aktiv setzen / Passiv setzen“
- Alle Tabellen mit Sortierung, Spaltenfiltern, Summen, Spaltenbreiten und CSV
- Begriffe: „passiv“ statt „vorgemerkt“, „Löschpotenzial“ statt „löschbar“

## 0.1.4
- Spaltenbreiten ziehbar, Summenzeile im Tabellenkopf, Zebramuster, Kalender für Datumsfilter
- Neue Spalte „Gerät / Herkunft“ (Gerät, Integration, UI-Helfer, YAML-Datei:Zeile, gelöscht …); Klick auf ein Gerät filtert seine Entitäten
- Tooltips für Prognosezeitraum und passive Regeln

## 0.1.3
- Kacheln gruppiert, mit Tooltips und ⓘ; Stand beider Analysen am jeweiligen Knopf
- Zeilenauswahl 100 / 500 / 1000 / 5000 / alle, fixierter Tabellenkopf, Spaltenfilter
- Fehler behoben: Laufhinweis blieb nach Ende der Analyse stehen

## 0.1.2
- Prognose: Entitäten, die nicht mehr schreiben, bekommen keinen Attribut-Zuwachs mehr (vorher deutlich zu hoch)

## 0.1.1
- Hinweis bei laufender Analyse mit Fortschritt; eigene Rückfragen statt Browser-Dialogen (die im HA-Fenster unterdrückt wurden)

## 0.1.0
- Erste Version: Analyse, Attribut-Analyse, Regeln mit Kurzzeit / Attribute / Langzeit, Probelauf mit Größenprognose
