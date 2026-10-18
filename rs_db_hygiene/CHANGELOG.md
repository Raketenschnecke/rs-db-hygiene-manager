# Changelog

Kurzfassung je Version.
Alle 0.1.x-Versionen: nur Analyse und Probelauf – es wird nichts gelöscht.
Ab 0.2.0: Löschen der Kurzzeit-States per recorder.purge_entities, nur auf Knopfdruck und mit Hauptschalter.

## 0.5.6b10 (Beta)
- **Löschplan und Protokoll (Punkt 256):** Attribut-Aufträge (Attribute von den States trennen) zählen nicht mehr in „geplant“, Überhang und „Läufe nötig“ – sie trennen ab und löschen nicht, deshalb stand bisher dauerhaft die Zahl der Attributzeilen als Differenz zwischen „geplant“ und „gelöscht“. Der Plan führt sie getrennt (`attr_getrennt`); im Tab Löschen nennen Kasten und Rückfrage „Attribute … werden von den States getrennt (die States bleiben)“, auch wenn sonst nichts gelöscht wird
- **Integration zu alt (Punkt 256):** kennt die Integration die neue Tabelle nicht, bricht der Lauf die Attribut-Aufträge nach dem ersten Fehler ab und schreibt einen Sammelhinweis („– N weitere Attribut-Aufträge übersprungen“) statt hunderter Einträge; die übrigen Aufträge laufen weiter
- Die Integration ändert sich nur in der Versionsnummer (der Dienst für die Attribute kam mit 0.5.6b9)

## 0.5.6b9 (Beta)
- **Attribut-Standard und Attribut-Wert je Regel (Punkt 254):** Attribute haben wieder eine eigene Frist. Im Reiter Regeln gibt es unter der Tabelle den Kasten „Attribut-Standard“ (Voreinstellung „wie Kurzzeit“, z. B. 30 Tage einstellbar); jede Regel kann einen eigenen Attribut-Wert setzen (leer = Standard, nie länger als die Kurzzeit). Nach der Frist trennt der Löschlauf die Attribute von den States (die States bleiben bis zu ihrer Kurzzeit) und entfernt die danach unbenutzten Attributzeilen. Schutz-Regeln haben die Option „Attribute mitschützen“ (Voreinstellung an, wie bisher)
- **Neue Empfehlung „Attribute“ (Punkt 254):** Sammelregel nur mit Attribut-Wert 14 Tage für Entitäten mit großem Attribut-Zuwachs (ab 100 MB im Prognosezeitraum je Entität); die Kurzzeit bleibt. Ersetzt den bisherigen Grund „große Attribute“ bei den Kurzzeit-Vorschlägen
- **Regelwerk-Check und Empfehlungen** rechnen die Attribute mit (Gewinn und Überstimmung, wirksame Tage, Ersparnis und Löschmenge der Attribute getrennt ausgewiesen); Spalte „Attribute“ in den Tabellen Entitäten und Regeln, Teil „A“ im Löschplan, Namenssuffix „– A 14T“ bei reinen Attribut-Regeln
- **Integration:** neue Tabelle `states_attribute` im Dienst `rs_db_hygiene.purge` (setzt `states.attributes_id` alter States auf NULL, löscht unbenutzte `state_attributes`-Zeilen, bereinigt den Cache des Recorders). **Die Integration muss mit aktualisiert werden** (HACS, danach HA-Neustart); ohne sie meldet der Löschlauf bei Attribut-Aufträgen „Integration zu alt“ und lässt die übrigen Aufträge laufen

## 0.5.6b8 (Beta)
- **„Ansehen“ bei mehreren Entitäten (Punkt 251):** in Empfehlungen mit mehr als einer Entität (z. B. Langzeit nirgends verwendeter Entitäten, Sammelregeln) zeigt „Ansehen“ jetzt nur die betroffenen Entitäten; Chip „Empfehlung: …“ mit ✕ über der Tabelle
- **Langzeit-Empfehlung (Punkt 252):** der Vorschlagswert für die Langzeit nirgends verwendeter Entitäten ist 1 Jahr statt 90 Tage
- **Ersparnis in MB und getrennt (Punkt 253):** die Vorschau „Neue Regel“ zeigt die Ersparnis ab unter 1 GB in MB und getrennt nach Kurzzeit und Langzeit; ebenso die Ersparnis im Kasten „Wer schreibt am meisten?“

## 0.5.6b7 (Beta)
- **Langzeit begrenzen (Punkt 249):** die Regel HA-Standard hat jetzt auch eine einstellbare Langzeit (Dauer oder unbegrenzt) und wird damit zum „Lumpensammler“ für alle Entitäten ohne eigene Langzeit-Regel. Zähler behalten ihre Langzeit immer. Neue Empfehlung der Art „Langzeit“: Sammelregel mit 90 Tagen Langzeit für alle nirgends verwendeten Entitäten (ohne Kurzzeit); Spalte „Wirkt auf“ in den Empfehlungen
- **Ersparnis mit Langzeit (Punkt 248):** die Vorschau „Neue Regel“ und die Empfehlungen rechnen die Langzeit jetzt in die Ersparnis ein (bisher nur in „sofort löschbar“)
- **Tabelle Empfehlungen (Punkt 249):** Spalte „Zuwachs“ entfernt (bei Langzeit-Vorschlägen irreführend), Spaltenköpfe wie im Reiter Regeln, Summe bei „Treffer“ entfällt; Tooltips von Zeilen, Speicher und Ersparnis nennen die Langzeit; Hilfe um die FAQ „Wie begrenze ich die Langzeitstatistik?“ ergänzt
- **Tooltips (Punkt 250):** nach einem Klick, einer Taste oder dem Mausrad erscheint nicht mehr der Browser-Tooltip, bis der Zeiger das Element verlässt

## 0.5.6b6 (Beta)
- **Löschtabellen einheitlich (Punkt 243):** Spaltenfolge in allen Löschtabellen Summe | States | 5-Min | Attribute | Events | Langzeit (Plan, Läufe, Lauf-Details, Monatssumme, Entitäten; bei Prognosen je Regel Summe | States | Attribute | Events | Langzeit). Die Summe enthält jetzt immer die Attribute; im Plan keine „≤“-Kennzeichnung mehr, in den Lauf-Details entfällt „Rest“. Läufe aus früheren Versionen bleiben unverändert (Summe ohne Attribute)
- **Vorhaltedauer im Löschprotokoll (Punkt 242):** die Spalte „keep_days“ heißt „Vorhaltedauer“ und zeigt „K 7T/L 1J“ (K = States, 5-Min, Attribute, Events; L = Langzeit; Einheit wie in der Regel gewählt, „alle“ bei vollständigem Löschen); ältere Läufe zeigen Tage
- **„integration“ statt „herkunft“ (Punkt 246):** der Regeltyp heißt in der Anzeige „integration“ (Typ-Spalte, Filter, Regelmaske, Rang-Texte, Empfehlungen, Regelwerk-Check); der interne Schlüssel „herkunft“ bleibt, keine Migration
- **Integration (Punkt 244):** der Sensor „DB-Größe“ übernimmt neue Werte höchstens alle 30 Minuten

## 0.5.6b5 (Beta)
- **Hilfe überarbeitet (Punkt 247):** Reiter „Hilfe“ auf den aktuellen Stand gebracht, u. a. FAQ „In welcher Reihenfolge lege ich Regeln an?“ (6 Schritte), neues Kapitel „Regelwerk-Check“, Sprungziele jetzt unter der klebenden Leiste sichtbar; Erwähnungen von purge_keep_days und configuration.yaml verlinken auf „Home Assistant vorbereiten“; Versionsangaben aus dem Hilfetext entfernt
- Die Integration ändert sich nur in der Versionsnummer

## 0.5.6b4 (Beta)
- **Korrektur Löschprotokolle:** die Tabelle „Läufe“ war seit 0.5.6b3 auf ihre Mindesthöhe geschrumpft und zeigte keine Zeilen; die Höhenbegrenzung aus Punkt 240 gilt jetzt nur für die letzte Tabelle eines Reiters
- Die Integration ändert sich nur in der Versionsnummer

## 0.5.6b3 (Beta)
- **Regelnamen (Punkt 241):** beim Speichern bekommt der Name das Suffix der Vorhaltedauer „– 30T/2J“ (Kurzzeit/Langzeit, ∞ = unbegrenzt, Event-Regeln „– 7T“); ein altes Suffix wird ersetzt. Gilt für alle Regeln, auch Schutz und HA-Standard („HA-Standard – 90T/∞“), und für alle Namen aus Empfehlungen und Regelwerk-Check
- **Tab Löschen (Punkt 229):** Umfang-Auswahl und „Plan neu laden“ stehen direkt neben „Jetzt ausführen“, der Umfang-Text daneben entfällt
- **5-Min-Statistik (Punkt 230):** wird im Löschlauf immer mit demselben keep_days wie die States der Entität gelöscht (auch bei Obergrenze); die Spalte Vorhaltung zeigt nur noch K und L
- **Löschprotokolle (Punkt 228):** nach einem neuen Lauf stehen Sortierung und Filter der Tabellen wieder im Ausgangszustand – auch nach dem Nachtlauf
- **Empfehlungen:** Regel für verwaiste Entitäten erscheint immer, solange keine aktive verwaist-Regel existiert (Punkt 239); Tabellenkopf bleibt beim Scrollen sichtbar, nach dem Speichern springt die Seite nicht mehr nach oben (Punkt 240)
- Die Integration ändert sich nur in der Versionsnummer

## 0.5.6b2 (Beta)
- **Erweitern statt neu (Punkt 232):** passt ein Vorschlag in eine bestehende Regel gleichen Typs, bietet der Reiter Empfehlungen „In Rxx aufnehmen …“ an (⚠, wenn die Zielregel die Langzeit kürzt); bei der Massenauswahl fragt die App, ob aufgenommen oder neu angelegt wird
- **Sammelregel (Punkte 233/234):** ist eine Klassen- oder Integrationsregel nicht sicher (zu viele der Entitäten werden verwendet, oder sie verlängert die Kurzzeit anderer), schlagen die Empfehlungen eine Regel mit allen unbenutzten Entitäten der Klasse bzw. Integration vor
- **Integrationsregeln:** Kurzzeit wie bei den Einzelvorschlägen statt fest 30 Tage, Verwendungsprüfung wie bei Klassen, schon ab 3 Einzelvorschlägen
- **Wenig schreibende unbenutzte Entitäten (Punkt 236):** ab 100 Zeilen je Tag zählen sie für Klassen-, Integrations- und Sammelregeln mit; eigene Karten weiter erst ab 500
- **Diagnosewerte (Punkt 237):** Gruppen auch aus der HA-Kategorie „Diagnose“ (nur unbenutzte, Muster trifft nur solche), deutsche Endungen wie `_signalstarke` oder `_paket_id`
- **Regelwerk-Check (Punkt 238):** beim Zusammenlegen keine doppelte Notiz mehr
- Die Integration ändert sich nur in der Versionsnummer

## 0.5.6b1 (Beta)
- **Regelwerk-Check im Reiter Regeln (Punkt 232):** prüft das bestehende Regelwerk – Gegenstück zum Reiter Empfehlungen, der nur neue Regeln vorschlägt. Kasten über der Tabelle mit Zählern und „Regeln vorher → nachher“, neue Spalte „Empfehlung“ mit einem Chip je Befund
  - rot: Regel unwirksam (setzt nirgends einen Wert) oder teilweise unwirksam (ein anderer Regeltyp hat Vorrang und setzt einen anderen Wert)
  - gelb: Muster doppelt, Regeln gleichen Typs mit gleichen Werten zusammenlegen, lange ID-Listen als Platzhalter, Event-Liste als `*`
  - grau: Muster ohne Entität, überflüssige Regel, meist überstimmt
- **Dialog je Befund:** Begründung, Vorschläge zur Wahl (bei „teilweise unwirksam“ ohne Vorauswahl: Ausnahme-Regel oder Werte angleichen), Liste „Änderungen am Regelwerk“ (gelöschte, neue, geänderte Regeln), Wirkung – „wirkungsneutral“ oder die betroffenen Entitäten mit Werten vorher und nachher. Der Knopf heißt wie die Aktion, z. B. „Regel R28 löschen“. Vor jeder Übernahme wird das Regelwerk gesichert; „Bewusst so“ blendet einen Befund aus
- Die Integration ändert sich nur in der Versionsnummer

## 0.5.5
Zusammenfassung der Testversionen 0.5.5b1 bis 0.5.5b14 (Einzelheiten darunter). Nach dem Update startet einmal eine neue Analyse (neuer Aufbau des Analyse-Speichers).
- **Neue Regeltypen:** `herkunft` (alles einer Integration, z. B. `unifi`) und `vielschreiber` (ab einer Schwelle in Zeilen je Tag, mit Hysterese – wer dazugehört, bleibt bis unter 80 % der Schwelle)
- **HA-Standard als Auffangregel:** Kurzzeit einstellbar; Hinweis im Reiter Empfehlungen nur bei der Grundeinstellung 10 Tage
- **Empfehlungen:** Vorschläge je Integration und je Geräteklasse statt vieler Einzelregeln; Kasten „Wer schreibt am meisten?“; Mehrfachauswahl mit „als passive Regeln anlegen“, „als aktive Regeln anlegen …“ (mit Warnprüfung) und „zu einer Regel zusammenfassen …“; Ersparnis unter 1 GB in MB; FAQ „In welcher Reihenfolge lege ich Regeln an?“
- **Prognose-Chart** an den DB-Größe-Kacheln, mit Vergangenheit und HA-Standard
- **Rangnummer der Regeln** (Stufe.Position) und einheitlich „X hat Vorrang vor: Y – Wert“
- **Tab Löschen:** Rückstand in Läufen, Spalte „Überhang (weitere Läufe)“ statt „fällig“ (zählte die States nicht), „Summe (excl. Attribute)“, Status als farbige Chips, Erklärung „Integration fehlt“
- **Reiter Events:** eine Spalte „Vorhaltezeit“ (Wert, Regel, wirksame Tage); Hinweis auf Event-Typen ohne eigene Event-Regel
- **Tooltips:** eigene Anzeige nach dem Schema „**Thema:** Inhalt“, kontextbezogen, Bedienhinweise je in eigener Zeile
- Zuwachs je Tag aus den 7 vollen Tagen vor heute; deaktivierte Entitäten mit eigenem Status; Spalte „Speicher frei (MB)“
- Die Integration ändert sich nur in der Versionsnummer; ihr Update über HACS ist optional

## 0.5.5b14 (Beta)
- **„hat Vorrang vor“ (Punkt 225):** einheitlich statt „überstimmt“ bzw. „gewinnt vor“, mit dem Wert der unterlegenen Regel – z. B. „EV:alle hat Vorrang vor: HA:standard – 90 Tage“; in der Regelmaske „R07 hat Vorrang (bei 12 Entitäten)“
- **Reiter Events (Punkte 226/227):** Regel, Vorhaltezeit und wirksame Tage in einer Spalte „Vorhaltezeit“ wie im Tab Entitäten, mit gemeinsamem Filter; Spalte Prognose entfällt
- **Reiter Prognosen je Regel (Punkt 224):** Block „Löschmenge sofort“ entfällt
- **Tooltips (Punkt 223):** Bedienhinweise wie „Klick:“ oder „Umschalt+Klick:“ stehen je in eigener Zeile, die Aktion fett

## 0.5.5b13 (Beta)
- **Löschplan: Überhang statt „fällig“ (Punkte 219, 222):** Die Spalte zählte die States nicht mit (Fehler seit 0.5.0). Jetzt „Überhang (weitere Läufe)“ – was dieser Lauf wegen der Obergrenzen nicht schafft; als eigene rote Spaltengruppe. Übersprungenes (Integration fehlt) zählt nicht dazu
- **Löschplan: Summe und Attribute (Punkt 221):** „Summe (excl. Attribute)“, „Attributzeilen (zus. zu Summe)“ direkt hinter der 5-Min-Statistik
- **„Integration fehlt“ erklärt (Punkt 222):** Tooltip – 5-Min-Statistik, Langzeit und Events löscht die App nur über die installierte und betriebsbereite Integration RS DB Hygiene
- **Event-Typen ohne eigene Regel (Punkt 204):** Hinweis im Reiter Events, unabhängig von der Menge, mit Knopf „Event-Regel für diese anlegen …“
- Tooltip-Thema auch unter fixierten Spalten richtig (Punkt 220)

## 0.5.5b12 (Beta)
- **Vielschreiber ohne Pendeln (Punkt 201):** Wer zu einer vielschreiber-Regel gehört, bleibt dabei, bis sein Zuwachs unter 80 % der Schwelle fällt (Hysterese, Stand je Analyse)
- **Empfehlungen per Massenauswahl (Punkte 212/213):** „als aktive Regeln anlegen …“ mit Warnprüfung vorab; „zu einer Regel zusammenfassen …“ – gleiche Regeltypen werden eine Regel, entity und glob eine glob-Regel, bei verschiedenen Kurzzeiten die längste oder je Kurzzeit eine Regel; ergibt sich genau eine Regel, öffnet sich die Regelmaske
- **Rückstand in Läufen (Punkt 200):** Tab Löschen schätzt, wie viele Läufe der Rückstand braucht; Tabelle Läufe mit Spalte „Rest danach (≈ Läufe)“
- **Tooltips (Punkte 215–218):** eigene Tooltip-Anzeige, überall nach dem Schema „**Thema:** Inhalt“ und kontextbezogen (Status-Chip im Löschplan, Kacheln nur ihre Zeile); Status im Löschplan als farbige Chips (grün vollständig, gelb teilweise, rot übersprungen)
- **Kleinigkeiten:** Spalten „Speicher frei (MB)“ / „Speicher (MB)“ statt „Platz“ (Punkt 209); Notiz der Empfehlungen „Empfehlung des RS DB HM“, ausgeschrieben im Tooltip (Punkt 211); neue FAQ „In welcher Reihenfolge lege ich Regeln an?“, verlinkt neben „Wer schreibt am meisten?“ (Punkt 214)

## 0.5.5b11 (Beta)
- **Empfehlungen je Integration (Punkt 205):** Ab 5 Einzelvorschlägen einer Integration (z. B. proxmoxve, unifi) kommt eine herkunft-Regel mit 30 Tagen statt vieler einzelner Regeln; nicht für Sammel-Integrationen wie mqtt, esphome oder template
- **Empfehlungen je Klasse (Punkt 208):** Mehrere Vorschläge derselben Geräteklasse (z. B. `sensor|frequency`) werden zu einer klasse-Regel, wenn die übrigen Entitäten der Klasse kaum verwendet werden; Uhrzeit-Sensoren (`time_date`) mit 1 Tag. Vorschläge für Integration oder Klasse entfallen, wenn sie eine heute kürzere Kurzzeit verlängern würden
- **„Nirgends verwendet“ nicht mehr für Automationen und Skripte (Punkt 206)**
- **Ersparnis in MB (Punkt 207):** unter 1 GB in MB, auch in der Summenzeile; „Ansehen“ filtert im Tab Entitäten nach Integration bzw. Klasse
- **Hinweis „HA-Standard aktiv“ (Punkt 210):** nur noch bei aktiver Regel mit 10 Tagen
- HACS-Prüfung (Validate) prüft wieder die Brand-Bilder

## 0.5.5b10 (Beta)
- **Rangnummer der Regeln (Punkt 199):** Jede Regel trägt ihren Rang als Stufe.Position – 0 Schutz, 1 E:, 2 entity, 3 glob, 4 klasse, 5 herkunft, 6 vielschreiber, 7 verwaist, 8 event, 99 HA-Standard. Zu sehen in der Löschtabelle (Spalte „Regel (Rang)“, Tooltip: gegen welche Regeln sie je Wert gewonnen hat) und im Reiter Regeln (Sortierung nach Rang = tatsächliche Vorrangfolge; ▲ verschiebt innerhalb der Stufe)
- **Statistik ohne Entität erklärt (Punkt 198):** Hinweis oben im Reiter – nur Anzeige, nie gelöscht, zählt nicht in Löschmenge und Prognose; endgültig entfernen in HA unter Entwicklerwerkzeuge → Statistiken

## 0.5.5b9 (Beta)
- **Regeltyp vielschreiber (Punkt 193):** Muster ist eine Schwelle in Zeilen je Tag (z. B. `1000`), optional mit Entity-Mustern als Einschränkung. Trifft aktive Entitäten ab dieser Schreibrate (ohne Zähler) – dynamisch nach jeder Analyse. Rang nach herkunft, vor verwaist
- **Regeltyp herkunft (Punkt 196):** alles einer Integration (z. B. `unifi`, `mobile_app`), Auswahl als Liste wie bei klasse; ohne Zähler und verwaiste, Rang nach klasse. Der Analyse-Cache kennt dafür die Integration je Entität (neuer Aufbau → nach dem Update einmal neue Analyse)
- **Wer schreibt am meisten? (Punkt 194):** Kasten im Reiter Empfehlungen – Top 10/25/50/100/250 mit Anteil am Zuwachs, Schwelle und Ersparnis bei wählbarer Kurzzeit; je Stufe „als Vielschreiber-Regel“ oder „als entity-Regel“
- **Empfehlungen mit Mehrfachauswahl (Punkt 195):** Auswahlspalte und Banner „als passive Regeln anlegen“ / „ignorieren“
- **Zuwachs je Tag aus 7 vollen Tagen (Punkt 192):** Spalte Zuwachs, Prognose und Empfehlungen rechnen mit dem Mittel der 7 vollen Kalendertage vor heute (fest ÷ 7) statt mit den letzten 24 Stunden; Events ebenso
- **Spalte „Platz frei (MB)“ (Punkt 197):** ersetzt im Tab Entitäten die Spalte „Attribute (MB)“ – Platz der ganzen Löschmenge sofort (States, 5-Min, Langzeit, Attribute), direkt neben der Summe

## 0.5.5b8 (Beta)
- **Prognose-Chart mit HA-Standard (Punkt 191, ersetzt Punkt 180):** Gelb und Grün rechnen die Regel HA:standard in ihrer eingestellten Kurzzeit mit – Grün immer, Gelb nur, wenn sie aktiv ist (oder mit Haken „passive Regeln mitrechnen“). Grau („nur HA-Standard“) bleibt fest bei 10 Tagen als Vergleich mit HA in der Grundeinstellung

## 0.5.5b7 (Beta)
- **HA-Standard als Auffangregel (Punkt 190):** Die Kurzzeit der Regel HA:standard ist einstellbar (Klick auf die Regel, nur Dauer; gilt auch für die Events, Langzeit bleibt unbegrenzt). Mit z. B. 90 Tagen fängt sie alles ohne eigene Regel auf – mit niedrigstem Rang und auch für Zähler, anders als eine glob-Regel `*`, die alle klasse-Regeln überstimmt. Die Linie „nur HA-Standard“ im Prognose-Verlauf rechnet weiter mit 10 Tagen
- **GUI hängt nach Wechsel des Prognosezeitraums (Punkt 189):** Gleichzeitige Anfragen rechnen denselben Probelauf nicht mehr mehrfach parallel; Rechnungen laufen nacheinander, wartende Anfragen übernehmen das Ergebnis

## 0.5.5b6 (Beta)
- **Prognose-Chart mit Vergangenheit (Punkt 188):** Links von „heute“ zeigt eine rote, durchgezogene Linie die bisherige DB-Größe aus der Langzeitstatistik des Integrations-Sensors „DB-Größe“ (Tagesmittel, höchstens 2 Jahre); eigener Legendeneintrag, per Klick ausblendbar, Tooltip je Tag. Die Prognoselinien sind gestrichelt und 1 px dick, „ohne Regeln“ ist violett statt rot

## 0.5.5b5 (Beta)
- **Deaktivierte Entitäten (Punkte 185/186):** Eigener Entitäts-Status „deaktiviert“ (in der Registry abgeschaltet, ohne Zustand) statt „aktiv“; Filter, Summenzeile und Hilfe kennen ihn. Die Regelart `deaktiviert` im Typ verwaist trifft sie (`alle` gilt nur für echte Verwaiste). Ihr Statistik-Eintrag bleibt auch bei Langzeit „keine“ erhalten; die Prognose rechnet keinen Zuwachs mehr für sie
- **Klasse und Einheit ohne Zustand (185):** Bei Entitäten ohne Zustand (deaktivierte) kommen device_class, state_class und Einheit aus der Registry – die Spalte Klasse ist gefüllt, klasse-Regeln und der Zähler-Schutz greifen. Die erste Analyse nach dem Update läuft automatisch (Cache-Aufbau 6)

## 0.5.5b4 (Beta)
- **Logo:** Der Schriftzug „DB Hygiene Manager“ im Kopf ist im dunklen Modus jetzt kräftig dunkelrot statt hellrot

## 0.5.5b3 (Beta)
- **Prognose-Chart:** Die Linien „eigene Regeln“ (gelb, grün) rechnen ohne die Regel HA-Standard – sie zeigen, was die eigenen Regeln allein bewirken
- **Empfehlungen:** Hinweis, solange HA-Standard aktiv ist – dann entfallen alle Vorschläge mit mehr als 10 Tagen; neue FAQ dazu im Reiter Hilfe
- **Regeln verwalten:** Stände mit genau dem Inhalt der Startregeln tragen den Vermerk „= Startregeln“ (aktuell und in der Sicherungsliste)
- **Regelmaske:** Beim Typ entity zeigt die Vorschlagsliste bei Platzhaltern „trifft keine Entität“ mit Knopf „Typ auf glob ändern“; neuer Knopf „alle … als Entity-IDs übernehmen“ übernimmt alle Treffer eines Musters als genaue IDs – auch Zähler

## 0.5.5b2 (Beta)
- **Prognose-Chart:** Klick auf eine Kachel der Spalte „DB-Größe“ zeigt die erwartete Entwicklung der DB in Monatsschritten über den Prognosezeitraum – ohne Regeln, nur HA-Standard, eigene aktive Regeln (passive mit Haken „passive Regeln mitrechnen“) und eigene Regeln aktiv + passiv; Monatswerte beim Überfahren, Linien per Legende ausblendbar

## 0.5.5b1 (Beta)
- **Beta-Version, nur auf dem Branch `beta`:** Stand wie 0.5.4, Grundlage für die nächsten Testversionen (noch keine neuen Punkte)

## 0.5.4
Zusammenfassung der Testversionen 0.5.4b1 bis 0.5.4b7 und des Nachtrags (Einzelheiten darunter).
- **Neuer Reiter „Empfehlungen“:** Regelvorschläge aus der letzten Analyse – gleichartige Entitäten als ein Muster, Vielschreiber, große Attribute, nirgends verwendete und deaktivierte Entitäten, verwaiste ohne Regel, Event-Typen ohne Event-Regel; je mit Treffern, Löschmenge sofort und Ersparnis, Hinweis auf passende passive Regeln. „Übernehmen …“ öffnet die Regelmaske ausgefüllt und passiv, „ignorieren“ merkt sich die App. Nie für Zähler, geschützte Entitäten oder Quellen von Energie-Dashboard und Helfern; die Langzeit bleibt unverändert
- **Einfachere Regelanlage:** Live-Vorschau mit Treffern, Löschmenge sofort und Ersparnis; viele einzelne IDs werden zu Mustern verdichtet; „Regel für gleichartige“ im Entitäts-Dialog; Autovervollständigung im Musterfeld; Schnellknöpfe für die Werte; Regeltypen im Klartext, der Typ ergibt sich aus den Mustern; beim Typ „klasse“ Auswahl aus allen vorhandenen Klassen
- **Neue Spalte „Klasse“** (device_class) im Tab Entitäten, auch im CSV-Export
- **Auswahl im Tab Entitäten** gilt nur für den aktuellen Filter
- **Verbindungswächter:** Ist die App nicht erreichbar, zeigt die Oberfläche ein rotes Banner und sperrt ändernde Knöpfe; neben dem Logo steht die Version
- **Logo und Icons:** neues Logo in der Oberfläche (Schrift im Rot des Piktogramms), eigene Icons für App und Integration (bei der Integration ab Home Assistant 2026.3)
- Tab Löschen: die Knöpfe zur Tabelle stehen direkt über der Tabelle
- Die Integration ändert sich nur bei Icons und Logo; ihr Update über HACS ist dafür nötig, sonst optional

## Nachtrag zu 0.5.4 (nach 0.5.4b7, ohne eigene Beta)
- Logo: Schrift „DB Hygiene Manager“ im Rot des Piktogramms (#5c0000; im dunklen Modus bleibt das hellere Rot); Reiter „Optimierungen“ heißt jetzt „Empfehlungen“

## 0.5.4b7 (Beta)
- **Neuer Reiter „Optimierungen“ (Punkt 176):** Regelvorschläge aus der letzten Analyse – Gruppen gleichartiger Entitäten als ein Muster, Vielschreiber, große Attribute, nirgends verwendete und deaktivierte Entitäten, verwaiste ohne Regel, Event-Typen ohne Event-Regel. Je Vorschlag Treffer, Zuwachs, Löschmenge sofort und Ersparnis im Prognosezeitraum; Hinweis, wenn eine passive Regel die Daten schon abdeckt. „Übernehmen …“ öffnet die Regelmaske ausgefüllt und passiv, „ignorieren“ merkt sich die App. Nie vorgeschlagen werden Zähler, geschützte Entitäten und Quellen des Energie-Dashboards oder von Helfern; die Langzeit bleibt unverändert
- **Live-Vorschau in der Regelmaske (Punkt 170):** Treffer, Überstimmungen, zusätzliche Löschmenge sofort und Ersparnis erscheinen beim Eingeben; der Knopf „Treffer anzeigen“ entfällt
- **Auswahl zu Mustern verdichten (Punkt 171):** Bei vielen einzelnen IDs schlägt die Maske Muster mit Platzhaltern vor, die genau dieselben Entitäten treffen
- **„Regel für gleichartige“ (Punkt 172):** Im Entitäts-Dialog Knöpfe für gleiches Präfix, gleiche Endung und gleiche Klasse, je mit Trefferzahl
- **Autovervollständigung (Punkt 173)** im Musterfeld, **Schnellknöpfe (Punkt 174)** für Kurzzeit und Langzeit, **Regeltypen im Klartext (Punkt 175)**; bei neuen Regeln ergibt sich der Typ aus den Mustern
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

## 0.5.4b6 (Beta)
- **Auswahl gilt nur für den aktuellen Filter (Punkt 169):** Ändert sich ein Filter im Tab Entitäten (Spaltenfilter, Domain, Regel-Filter, „nur mit Hinweis/Schutz“, Gerät), werden ausgewählte Entitäten abgewählt, die nicht mehr passen; die Leiste meldet, wie viele. Sortieren und Blättern lassen die Auswahl unverändert. „Regel/Schutz aus Auswahl anlegen“ und „Auswahl als CSV“ nehmen damit keine ausgefilterten Entitäten mehr mit
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

## 0.5.4b5 (Beta)
- **Regelmaske, Typ klasse (Punkt 168, Korrektur zu 166):** Eine lange Klassen-Auswahl macht die Maske nicht mehr breiter als das Fenster (ID, Name und Typ blieben sonst unerreichbar). Beim Wechsel auf „klasse“ werden Entity-IDs (z. B. aus „Regel aus Auswahl anlegen“) in ihre Klassen umgesetzt statt als ungültige Muster übernommen; beim Wechsel zurück stehen die vorherigen Muster wieder da
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

## 0.5.4b4 (Beta)
- **Neue Spalte „Klasse“ (Punkt 167):** Tab Entitäten, Gruppe Stammdaten, hinter „Gerät / Herkunft“: die `device_class` der Entität. Sortierbar, Filter als Auswahl (alle vorkommenden Klassen oder „ohne Klasse“); sichtbar in den Ansichten Standard und States & Statistik. Auch im CSV-Export und im Regel-Dialog der Entität
- **Klassen-Auswahl in der Regelmaske (Punkt 166):** Beim Typ „klasse“ werden die Klassen nicht mehr eingetippt, sondern aus einer aufklappbaren Liste gewählt (Mehrfachauswahl, mit Suche). Je Klasse steht die Anzahl aktiver Entitäten, davon Zähler (vom Zähler-Schutz ausgenommen). Gespeicherte Klassen, die die Analyse nicht mehr kennt, bleiben markiert in der Liste
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

## 0.5.4b3 (Beta)
- **Logo (Punkt 165):** Die Oberfläche zeigt oben das neue Logo statt der Überschrift, daneben die Version; der Knopf „Analyse starten“ ist genauso hoch wie das Logo
- **Icons:** App und Integration haben eigene Icons und Logos (im App-Store bzw. ab Home Assistant 2026.3 bei der Integration)
- Die Integration ändert sich sonst nicht; ihr Update über HACS ist nur für das Icon nötig

## 0.5.4b2 (Beta)
- **Verbindungswächter (Punkt 164):** Ist die App nicht erreichbar (gestoppt, Neustart), zeigt die Oberfläche ein rotes Banner und sperrt alle ändernden Knöpfe; Speichern wird abgewiesen. Sobald die App wieder antwortet, verschwindet das Banner und die Daten werden neu geladen
- **Kennung (Punkt 164):** Neben der Überschrift steht die Version, bei der Beta auffällig als „Beta 0.5.4b2“; der Fenstertitel der Beta endet auf „(Beta)“
- **Tab Löschen (Punkt 163):** „Filter zurücksetzen“, „Spaltenbreiten zurücksetzen“, „Spalten …“ und „CSV“ stehen jetzt direkt über der Tabelle; oben bleiben „Umfang“ und „Plan neu laden“
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

## 0.5.4b1 (Beta)
- **Beta-Version, nur auf dem Branch `beta`:** Stand wie 0.5.3, Grundlage für die nächsten Testversionen (noch keine neuen Punkte)

## 0.5.3
Zusammenfassung der Testversionen 0.5.3b1 bis 0.5.3b7 (Einzelheiten darunter).
- **Neue Startregel HA-Standard:** bildet das Löschverhalten von Home Assistant in der Grundeinstellung ab (10 Tage, Langzeit unbegrenzt); bei bestehenden Installationen passiv ergänzt, nicht löschbar
- **Prüfung der Recorder-Einrichtung:** Banner mit Verweis auf die neue Hilfe-FAQ „Erstinbetriebnahme“, wenn `auto_purge`/`auto_repack` nicht auf false stehen oder `purge_keep_days` unter 10 Jahren liegt
- **Nachtlauf nach Mitternacht:** Stammt die Analyse vom Vortag, analysiert die App vor dem Lauf neu
- **Neues Aussehen aller Tabellen:** farbige Spaltengruppen passend zu den Kacheln, zweizeilige Spaltenköpfe, schlanke Summenzeile, fixierte erste Spalte(n), Spaltendialog nach Gruppen mit Ansichten; neue Spalte „5-Min (Zeilen)“
- **Regel-Chips** öffnen die Regel; eigene Regeln erscheinen als „E“
- **Bedienung:** Reiter mit farbiger Oberkante in neuer Reihenfolge („Löschprotokolle“), Knopf „Analyse starten“ im Kachel-Stil, im Tab Löschen bleibt „Jetzt ausführen“ beim Scrollen stehen und erklärt im Tooltip den Umfang
- Kürzere Spaltentitel; der CSV-Export der Tabellen übernimmt sie
- Die Integration ändert sich nicht, nur ihre Versionsnummer; ihr Update über HACS ist optional

## 0.5.3b7 (Beta)
- **Tab Löschen:** Der Knopf „Jetzt ausführen“ erklärt im Tooltip, dass er den ganzen Plan des gewählten Umfangs löscht und die Tabellenfilter nur die Anzeige betreffen; ist im Umfang eine einzelne Regel gewählt, steht daneben ein Hinweis (Punkt 161). Der Text „Letzter Lauf …“ neben dem Knopf entfällt (Punkt 162)
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

## 0.5.3b6 (Beta)
- **Behoben (Punkt 160):** Bei eigenen Regeln (E:…) verdrängte die lange Regel-ID im Chip die Vorhaltezeit in den Spalten Kurzzeit/Langzeit. Der Chip zeigt jetzt nur „E“ (volle ID im Tooltip); Chips sind höchstens etwa 8 Zeichen breit
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

## 0.5.3b5 (Beta)
- **Reiter (Punkt 158):** neue Reihenfolge Entitäten, Events, Statistik ohne Entität, Regeln, Löschen, Löschprotokolle, Prognosen je Regel; „Regeln“ mit gelber Oberkante wie Löschen; „Löschprotokoll“ heißt jetzt „Löschprotokolle“
- Behoben: In der Überschrift wurden „g“ und „y“ unten abgeschnitten (Punkt 159)
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

## 0.5.3b4 (Beta)
- **Alle Tabellen im Look & Feel des Tabs Entitäten (Punkte 144–151):** Events, Regeln, Prognosen je Regel, Statistik ohne Entität, Löschen, Löschprotokoll (Läufe, Lauf-Details, Monatssumme) haben jetzt farbige Spaltengruppen (Stammdaten/Lauf blau, Vorhaltezeiten/Wirkung/Plan zweites Blau, Istzustand rot, Löschmenge/Gelöscht gelb, Prognose grün), zweizeilige Spaltenköpfe, eine schlanke Summenzeile, Filter in zwei Ebenen, fixierte erste Spalte(n) und einen nach Gruppen geordneten Spaltendialog
- Kürzere Spaltentitel nach dem Muster „Titel / Zusatz“ (z. B. „States“ statt „davon States“); der CSV-Export übernimmt die neuen Titel
- Tab Events: Regel-Chip öffnet die Regel
- Knopf „Analyse starten“ im Stil der Kacheln (rot, Rahmen signalrot), 40 px breiter, unten bündig mit der Unterkante des „g“ der Überschrift und rechts bündig mit den Kacheln (Punkt 156)
- Reiter: Oberkante in der Farbe ihres Inhalts wie Kacheln und Spaltengruppen (Istzustand rot, Regeln blau, Prognosen grün, Löschen gelb) (Punkt 157)
- Tab Löschen: Beim Scrollen bleibt der Knopf „Jetzt ausführen“ oben stehen, der Text darüber verschwindet (Punkt 155)
- Tab Entitäten: Die Ansicht „Attribute“ im Spaltendialog zeigt auch die Kurzzeit (sie gilt für die Attribute) und die Attributzeilen, die mit den States wegfallen (Punkt 154)
- Zwischen den Knöpfen oben und der Tabelle bleiben immer 8 px Abstand, auch beim Scrollen (Punkte 152/153)
- Die Zeile mit Anzahl und Bereich über den Tabellen entfällt (die Anzahl steht in der Summenzeile); sie erscheint nur noch für Gerätefilter, Warnungen und die Auswahl „Zeilen“ (Punkt 153)
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

## 0.5.3b3 (Beta)
- **Tab Entitäten:** Summenzeile schlanker – je Spalte nur noch ein Wert, die Gesamtzahl in Klammern nur bei gesetztem Filter, Details im Tooltip (Punkt 139). Die Spalte „Entität“ bleibt beim Scrollen nach rechts stehen (Punkt 140). Dezente Trennlinie in der Gruppenfarbe zwischen Kopf und Daten, Kopf etwas kräftiger getönt (Punkt 142)
- **Regel-Chip:** Ein Klick auf den Chip einer Regel (z. B. „R32“) in Kurzzeit/Langzeit öffnet jetzt die Maske dieser Regel statt der Filtermaske; „Std.“ ist nicht mehr anklickbar; der Chip wird nicht mehr abgeschnitten (Punkt 143)
- **HA-Standard:** Symbol von Home Assistant (Material Design Icons) im HA-Blau statt Haus-Emoji (Punkt 141)
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

## 0.5.3b2 (Beta)
- **Prüfung der Recorder-Einrichtung (Punkt 133):** Beim Start, bei jeder Änderung der configuration.yaml und vor jeder Analyse prüft die App, ob `auto_purge: false`, `auto_repack: false` und `purge_keep_days` (mindestens 3650 Tage) gesetzt sind und `db_url` – falls vorhanden – auf die SQLite-Datei zeigt. Fehlt etwas, erscheint ein Banner mit Verweis auf die Hilfe. `recorder: !include …` wird mitgelesen
- **Hilfe:** neue erste FAQ „Erstinbetriebnahme“ mit Beispielen für die configuration.yaml und die beiden Automationen Löschlauf und Verdichten (Punkt 134)
- **Tab Entitäten (Punkt 132):** jede Spaltengruppe in eigener Farbe (Stammdaten und Vorhaltezeiten blau, Istzustand rot und Löschmenge sofort gelb wie die Kachelzeilen), Gruppennamen größer, Spaltenköpfe zweizeilig (Titel, darunter Zusatz bzw. Einheit). Die Gruppe „Aktuell“ heißt jetzt „Istzustand“. Die Statusspalte heißt „Entitäts-Status“ (Punkt 128)
- **Tab Entitäten (Punkt 135):** neue Spalte „5-Min (Zeilen)“, neue Ansicht „States & Statistik“ im Spaltendialog; der Dialog ist nach Gruppen geordnet, die Ansichtsknöpfe sind deutlicher und zeigen, welche Ansicht gerade gilt
- **Tab Entitäten (Punkt 136):** In der Filterzeile stehen Auswahlfelder oben und Textfelder unten, jeweils auf einer Linie
- **Tab Entitäten (Punkt 137):** Die Auswahlspalte (☐) gehört farblich zum Block Stammdaten
- **Hilfe (Punkt 138):** In der FAQ sind die Antworten unter jeder Frage eingerückt
- Behoben: Seit 0.5.3b1 fehlten im Tab Entitäten die Griffe zum Ändern der Spaltenbreite an der richtigen Stelle (die Gruppenzeile war die erste Kopfzeile)
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

## 0.5.3b1 (Beta)
- **Beta-Version, nur auf dem Branch `beta`:** Normale Nutzer sehen sie nicht; die Integration ist als GitHub-Pre-Release vorgesehen
- **Neue Startregel HA-Standard (Punkt 127):** Regel `HA:standard` bildet das Löschverhalten von Home Assistant in der Grundeinstellung ab (10 Tage für States, 5-Min-Statistik, Attribute und Events, Langzeit unbegrenzt). Sie ist bei einer Neuinstallation aktiv, bei bestehenden Installationen wird sie passiv ergänzt. Name, Muster und Werte sind fest; sie lässt sich nicht entfernen, nur passiv schalten. Niedrigster Rang: Jede andere Regel geht vor. Ist sie aktiv, warnt die App vor einem Löschlauf aus dem Tab Löschen, dass sie im HA-Standard läuft
- **Lauf aus HA nach Mitternacht (Punkt 126):** Stammt die Analyse von einem früheren Kalendertag, analysiert die App vor dem Lauf neu. Bisher überging der Nachtlauf Regeln, die am Vortag schon bereinigt waren, und holte sie erst in der folgenden Nacht nach
- **Tab Entitäten:** neue Spaltenreihenfolge in vier Gruppen (Stammdaten, Vorhaltezeiten, Aktuell, Löschmenge sofort) mit Gruppenzeile über den Spaltenköpfen (Punkt 130). Die Statusspalte heißt wie im Tab Löschen „Entität“ und hat einen Tooltip (Punkt 128). Klick auf das Label in Kurzzeit/Langzeit öffnet die Filtermaske der Spalte, vorbelegt mit dem angeklickten Wert (Punkt 129)
- Nur die App ändert sich. Die Integration bekommt nur die neue Versionsnummer

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
