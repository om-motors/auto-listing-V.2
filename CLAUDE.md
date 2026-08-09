# Auto-Listing — eBay-Entwürfe aus Produktfotos

Der Nutzer verkauft gebrauchte Kfz-Teile auf eBay.de (überwiegend Original-Teile
von Audi/VW/Mercedes aus Fahrzeugen mit geringer Laufleistung).

**Wichtig: Das ist inzwischen eine eigenständige Python-Anwendung, keine
Claude-Aufgabe mehr.** Die Pipeline läuft als Hintergrunddienst auf dem Mac und
braucht Claude nicht. Siehe [README.md](README.md) für die Bedienung.

Claude arbeitet hier also **am Code**, nicht an einzelnen Inseraten.

## Zuerst lesen: [ARBEITSSTAND.md](ARBEITSSTAND.md)

Dort steht, wo die Arbeit gerade steht, welche Befunde offen sind und was als
Nächstes ansteht — das überlebt ein `/clear`, der Gesprächsverlauf nicht.
**Am Ende einer Sitzung, in der sich der Stand geändert hat, dort nachtragen.**
Dauerhaftes Projektwissen gehört weiterhin hierher, nicht in den Arbeitsstand.

## Aufbau

| Modul | Aufgabe |
|---|---|
| `autolister/watcher.py` | Überwacht `Eingang/`, startet die Pipeline nach Ruhephase |
| `autolister/webapp.py` | Upload-Website (Port 8790), auch vom Handy nutzbar |
| `autolister/pipeline.py` | Orchestrierung: Phase 1 parallel analysieren, Phase 2 ein Browser für alles |
| `autolister/images.py` | HEIC-Umwandlung, Verkleinern (13,7 MB → 1,4 MB) |
| `autolister/ocr.py` | **Kostenlose Texterkennung** über das macOS-Vision-Framework |
| `autolister/partnumber.py` | Teilenummern per Regex je Hersteller, Mehrheitsentscheid |
| `autolister/ableiten.py` | Teilname/Modellcodes/Versandstufe aus eBay-Titeln auszählen |
| `autolister/vision.py` | Fotos → Teilenummer (lokal oder per Bildmodell) |
| `autolister/research.py` | eBay-Suche, Kandidatenprüfung, verkaufte Artikel |
| `autolister/compose.py` | Titel, Preis (Median + Quartilsfilter), Versandstufe |
| `autolister/draft.py` | Formular ausfüllen + Entwurf speichern (Playwright) |
| `autolister/llm.py` | Optionaler Claude-Zugriff: API, sonst `claude -p` |
| `autolister/doctor.py` | Selbsttest der Voraussetzungen |
| `autolister/notify.py` | Berichte + macOS-Mitteilungen |

## Betriebsarten

`config.aktiver_modus()` entscheidet, welcher Weg läuft — gesteuert über
`AUTOLISTER_MODUS` in der `.env`:

- **`lokal` (Standard, kostenlos)** — `ocr.py` + `partnumber.py` + `ableiten.py`.
  Keine API, keine laufenden Kosten. Das ist der Weg, der gepflegt werden muss.
- **`cli`** — über ein bestehendes Claude-Abo (`claude -p`).
- **`api`** — kostenpflichtig, bestes Ergebnis bei schlechten Fotos.

Wichtig: `vision.py` und `compose.py` fallen bei einem KI-Fehler **automatisch
auf `lokal` zurück**, statt abzubrechen. Diese Rückfallebene nicht entfernen.

## Unverrückbare Regeln

- **Niemals veröffentlichen.** Nur Entwürfe speichern. In `draft.py` sperrt die
  Konstante `FORBIDDEN` jeden Klick auf „anbieten", „einstellen", „verkaufen".
  Diese Sperre darf nicht entfernt oder aufgeweicht werden.
  Seit dem 2026-08-08 arbeitet das Browserprofil auf dem **echten
  Verkäuferkonto `om.motors`**, nicht mehr auf dem Testkonto. Ein Fehlklick
  steht damit im Konto des Nutzers, nicht in einem Bericht. Die Sperre wurde
  in bisher keinem Lauf herausgefordert — sie ist also nicht erprobt, nur
  vorhanden.
- **CAPTCHAs werden nicht gelöst oder umgangen.** Bei einer Sicherheitsabfrage
  bricht die Pipeline mit `CaptchaBlocked` ab und bittet den Nutzer.
- **Preise werden gerechnet, nicht geschätzt.** `compose.py` bildet den
  **Median** der Vergleichsangebote und wirft vorher alles außerhalb des
  1,5-fachen Quartilsabstands weg. Kein Mittelwert: der Markt ist stark
  gespreizt (gemessen 140 € bis 1236 € für dasselbe Teil), da zieht ein
  Mittelwert nach oben.
- **Der Nachsatzbuchstabe gehört zur Teilenummer.** In die Preisbasis kommen
  nur Angebote mit *genau* dieser Nummer. Erst wenn es davon weniger als drei
  gibt, zählen andere Ausführungen mit — und dann steht es im Bericht, samt
  Aufzählung der fremden Nummern. Ohne diese Regel zahlt der Käufer für die
  falsche Variante: Am Steuergerät `8K0907801J` führten 10 von 23
  Vergleichsangeboten in Wahrheit `…801H`, `…801M`, `…801N`, `…801D`, `…801E`
  oder `…801F` und hoben den Preis von 22,90 € auf 24,90 €.
- **Kostenlos ist der Normalfall.** Neue Funktionen müssen ohne API auskommen
  oder sauber darauf verzichten können.

## Benennen und Rechnen brauchen verschiedene Auswahlen

Beides speist sich aus denselben eBay-Titeln, aber mit entgegengesetzter
Anforderung — `compose._lokal()` hält sie deshalb auseinander:

| | Auswahl | Warum |
|---|---|---|
| **Teilname, Modellcodes, Marke, Position, Versandstufe** | auch fremde Ausführungen | Ein `…801H` ist genauso ein „Steuergerät Feststellbremse". Je mehr Titel mitzählen, desto stabiler das Auszählen. |
| **Preis** | nur die genaue Nummer | Der Nachsatzbuchstabe entscheidet über den Betrag. |

Wer das wieder zusammenlegt, verliert eine der beiden Seiten: Mit der engen
Auswahl fürs Benennen wurde aus „Steuergerät Feststellbremse" prompt
„Feststellbremssteuergerät" — mit der weiten fürs Rechnen war der Preis 2 €
zu hoch.

## Tests

Ohne zusätzliches Paket, mit dem eingebauten `unittest`:

```bash
.venv/bin/python -m unittest discover tests
```

Das Material in `tests/` sind **echte Angebotstitel aus `Berichte/`**, keine
erfundenen. Wer die Preisrechnung anfasst, kann so gegen die vorhandenen
Berichte gegenprüfen, statt zu raten.

## Erkenntnisse zur Texterkennung

- Eingestanzte Teilenummern stehen oft **hochkant**. Deshalb liest `ocr.py`
  jedes Foto in allen vier Drehungen. Am Testteil kam bei 0° nur `205` /
  `351 00 05` heraus, bei 270° die vollständige Nummer `A 205 351 00 05`.
- **Nicht verkleinern.** Bei 3000 px zerfiel die Nummer wieder in Fragmente,
  bei 4200 px war sie vollständig. Daher `OCR_MAX_EDGE=4200`.
- Herstellerlogos werden als `©`, `®`, `@`, `•` oder `S` gelesen, Lücken
  zwischen Zifferngruppen als `.` oder `:` — beides begradigt
  `partnumber._begradigen()`.
- **eBay ist der beste Prüfstein**: bei mehreren Lesarten entscheidet
  `research.pruefe_kandidaten()`. Die echte Nummer bringt Treffer, ein
  Lesefehler nicht. Das kostet nichts und fing im Test `A2033510005`
  (Fehllesung) gegen `A2053510005` (korrekt) ab.
- eBay hängt an jeden Suchtreffer „Wird in neuem Fenster oder Tab geöffnet" an.
  Ungefiltert wurde daraus einmal der Teilname „Geöffnet" — entfernt in
  `research._clean_title()`.

## Datenschutzsperre von macOS

Liegt das Projekt in `~/Desktop`, `~/Documents` oder `~/Downloads`, bekommen
launchd-Dienste dort ein `Operation not permitted` — wortlos, ohne Nachfrage,
in einer Neustartschleife. `doctor._check_datenschutzsperre()` erkennt das.
Lösung: Projekt nach `~/Auto-Listing` verschieben oder Festplattenvollzugriff
für `.venv/bin/python` erteilen.

## Fachliche Vorgaben des Nutzers

- **Titel** (max. 80 Zeichen): `Original <Marke> <Modellcodes> <Teilname> <Position> <Teilenummer>`
- **Zustand**: immer „Gebraucht"
- **Format**: Sofort-Kaufen, keine Auktion
- **Preisvorschläge zulassen**: immer an
- **Angebot bewerben**: immer an, Anzeigentarif **2 %** (Vorgabe 2026-07-11).
  Die Schnellauswahl bietet nur 8/10/12 % — über „Eigenen Anzeigentarif
  auswählen" das Prozentfeld öffnen und `2` eintragen.
- **Rücknahme**: 14 Tage Inland, Käufer zahlt Rückversand, keine internationale
  Rücknahme. Achtung: Das Formular startet mit „Keine Rücknahme"!
- **Einbauposition**: als Artikelmerkmal **immer weglassen** (Vorgabe
  2026-07-30, umgesetzt in `config.MERKMALE_AUSLASSEN`). Im **Titel** bleibt
  die Position erhalten.
- **Übersetzungsverhältnis** bei Differentialen: leer lassen.

| Versandstufe | Preis (Käufer zahlt) | typische Teile |
|---|---|---|
| Standard | 7,69 € | Halter, Sensoren, Kleinteile, Zierleisten |
| Mittel | 23,99 € | Scheinwerfer, Spiegel, größere Verkleidungen |
| Spedition | 60,00 € | Stoßstangen, Träger, Türen, Hauben, Kotflügel, Sitze |

Alles Sperrige geht per **Spedition zu 60 €** (Vorgabe 2026-07-30). Die frühere
Stufe „Groß" zu 79,90 € gibt es nicht mehr — sie ist in „Spedition"
aufgegangen. Im Zweifel die kleinste Stufe wählen und im Bericht darauf
hinweisen.

## Erkenntnisse zum eBay-Verkaufsformular

Alle Selektoren am echten Formular ermittelt und per Trockenlauf verifiziert.

**Einstieg und Grundsätzliches**

- `ebay.de/sl/prelist/suggest` → Titel eingeben → „Weiter" → das Formular
  legt sofort einen Entwurf an (`draftId` in der URL).
- **Dazwischen schiebt eBay bis zu drei Zwischenseiten ein — alle unter
  derselben Adresse `/sl/prelist/identify`.** Welche kommt, hängt davon ab,
  wie sicher eBay den Titel einordnen kann; unterschieden werden sie am
  `view`-Anhängsel bzw. am Inhalt:

  | Seite | Erkennung | Antwort |
  |---|---|---|
  | Kategorie | `button.se-field-card__body` mit „ > " im Text | eBays ersten eigenen Vorschlag |
  | Produktbibliothek | Knopf „Ohne passendes Produkt fortfahren" | **kein** Katalogprodukt wählen — es brächte Titel und Merkmale von eBay mit und überschriebe, was `compose.py` hergeleitet hat |
  | Zustand | `view=sellnode-condition` | Radio `name=condition`, Wert `3000` = Gebraucht |

  Am 2026-08-09 kannte die Pipeline die letzten beiden nicht, wartete
  60 Sekunden auf ein Formular, das nie kam, und meldete „Verkaufsformular
  wurde nicht erreicht" — alle drei Teile eines Laufs scheiterten daran.
  Wer hier einen Fehler sucht: Die Meldung nennt inzwischen die Stelle.
- Ist der Zustand schon auf der Zwischenseite gesetzt, steht er im Formular
  bereits richtig. Der Schritt dort **prüft zuerst und klickt nur, wenn
  nötig** — sonst lief er in einen Timeout und verlangte Handarbeit für
  einen Wert, der längst im Entwurf stand.
- Über einem **frisch angelegten** Entwurf liegt ein modaler Dialog
  („Zum erweiterten Verkaufsformular wechseln"), der jeden Klick blockiert.
  Bei einem *nachgeladenen* Entwurf erscheint er nicht — deshalb war er bei
  der Selektor-Untersuchung unsichtbar und ließ alle Schritte in Timeouts
  laufen. `_dialoge_schliessen()` klickt „Nein, bleiben".
- eBay rendert das lange Formular **abschnittsweise nach**. Ohne
  `_formular_bereit()` (auf Anker warten + einmal durchscrollen) fehlt die
  halbe Seite.
- Speichern über **„Speichern"** ganz unten. Die Veröffentlichen-Knöpfe
  heißen „Zu genannten Gebühren einstellen" bzw. „Artikel kostenlos
  einstellen" — beide fängt `FORBIDDEN` ab.
- **Foto-Upload:** direkt per `set_input_files()` in das `input[type=file]`.

**Ein frisch angelegter Entwurf ist nicht derselbe wie ein nachgeladener**

Das ist die teuerste Falle im ganzen Formular, und sie schlägt zweimal zu:

- Der Wechsel-Dialog erscheint nur beim **frisch angelegten** Entwurf (siehe
  oben) — deshalb blieb er bei der Selektor-Untersuchung unsichtbar.
- **Dieselben Felder heißen unterschiedlich.** Am nachgeladenen Entwurf trägt
  das Preisfeld nur `aria-label="Artikelpreis"` und kein `name`; am frisch
  angelegten ist es umgekehrt. Kontrollen, die nur einen der beiden Wege
  lesen, melden „Feld war nicht im Formular", obwohl der Wert sauber
  drinsteht. Genau das kostete am 2026-08-02 drei Trockenläufe.
  `_formularwerte()` legt jedes Feld deshalb **unter beiden** Schlüsseln ab.
- Ebenso bei Fotos: direkt nach dem Upload hängen die Vorschaubilder als
  `blob:`-URL im DOM, erst nach dem Speichern werden daraus `ebayimg`-Adressen.
  Wer nur auf `ebayimg` zählt, sieht null Bilder.

**Merke:** Selektoren und Kontrollen immer am *frisch angelegten* Entwurf
prüfen, nie an einem nachgeladenen. Der nachgeladene lügt.

**Artikelmerkmale — die vier Fallen**

Aufbau einer Zeile: Beschriftung, sichtbarer Aufklapp-Knopf
`button.se-expand-button__button[aria-label="<Name>"]`, und ein
`input.textbox__control`, das bis zum Aufklappen `display:none` hat.

1. Direkt ins `input` schreiben geht nicht („element is not visible") —
   erst den Aufklapp-Knopf klicken.
2. `fill()` setzt den Wert, löst aber die Übernahme nicht aus; er
   verschwindet beim Schließen. Es braucht `press_sequentially()` und Enter.
3. Das Feld liegt im **direkten Elternelement** des Knopfes
   (`div.fake-menu-button`). Ein globales `.first` traf nach dem ersten
   gesetzten Merkmal weiterhin dessen Feld — alle folgenden blieben leer.
4. **Niemals per Präfix suchen.** Das Feld heißt real
   `OE/OEM Referenznummer(n)`, ein exakter Vergleich geht daran vorbei — aber
   ein Präfixvergleich lässt „Hersteller" das Feld „Herstellernummer"
   greifen. `_merkmal_gleich()` vergleicht deshalb exakt und verzeiht nur
   Klammerzusätze. Und sobald ein Merkmal gefüllt ist, **entfernt eBay
   dessen `aria-label`**.

**Merkmale gibt es nur je Kategorie.** In „ECUs & Steuergeräte" existiert
*kein* Merkmal „Hersteller", in „Sonstige" *keine* „Produktart". Am
2026-08-09 schrieb der Präfixvergleich deshalb „Audi" in die
Herstellernummer und die Teilenummer gleich hinterher — das Feld zeigte
„Audi (+1)", die echte Herstellernummer fehlte. `_merkmal_namen()` liest
vorher aus, was die Kategorie anbietet; was es nicht gibt, ist eine
**Meldung** im Bericht, keine Aufgabe für den Nutzer.

**„Mehr anzeigen" muss vorher geklickt werden.** eBay zeigt nur die
Pflichtmerkmale; dahinter liegt unter anderem `OE/OEM Referenznummer(n)` —
das Feld, über das Käufer Kfz-Teile suchen.

**Zum Zurücklesen die Stelle merken, solange das Feld noch leer ist.** Am
echten Formular nachgemessen (2026-08-02) — die gefüllten Felder trugen
`aria=''` mit dem Text `Audi`, `Sonnenblende`, `8K0857552`, nur die leeren
noch ihren Namen:

```
aria=''                 text='Audi'           <- Hersteller, gefüllt
aria=''                 text='Sonnenblende'   <- Produktart, gefüllt
aria='Farbe'            text=''               <- leer, Name noch da
aria='Einbauposition'   text=''
aria=''                 text='8K0857552'      <- Herstellernummer, gefüllt
```

Jeder Selektor über `aria-label` findet ein **ausgefülltes** Merkmal also
grundsätzlich nicht mehr — beim Setzen greift er noch, beim Kontrollieren nie.

Zwei Wege, die dafür naheliegen und beide **nicht** tragen:

- *Beschriftung und Wertfeld über den Index paaren.* Trug bis zum
  2026-08-09; seither haben auch Fotos, Titel, Preis und Lieferung
  Aufklapp-Knöpfe. Gemessen: 5 Beschriftungen gegen 16 Wertfelder — die
  Kontrolle las für „Hersteller" prompt „Foto-Optionen ansehen".
- *Das Eingabefeld `search-box-attributes…` auslesen.* Das ist das Suchfeld
  der Auswahlliste und bleibt auch nach dem Setzen **leer** (am gesetzten
  „Audi" geprüft).

Verlässlich ist allein die **Stelle in der Liste**, gemerkt zu dem Zeitpunkt,
an dem das Feld noch leer war und seinen Namen trug. `_merkmal_setzen()`
merkt sie sich und reicht sie an `_merkmal_wert()` durch.

**Die Beschreibung ist beim Anlegen mit dem Titel vorbelegt.** Das versteckte
`textarea[Beschreibung]` trägt zunächst den Angebotstitel, und der
Rich-Text-Editor schreibt seinen Inhalt erst beim Verlassen dorthin zurück.
Eine Kontrolle, die das Textfeld liest, prüft also gegen den Titel. Richtig ist
der Blick in den Editor selbst: `iframe#se-rte-frame__summary` → `body`.

**Die Fotos zählt man an eBays eigenem Zähler.** Über dem Fotofeld steht
`3/25`. Die Vorschaubilder stecken weder als `ebayimg`- noch als `blob:`-Adresse
in einem `<img>` — wer sie zählt, findet null, während drei Fotos sichtbar
hochgeladen sind.

**Der Foto-Editor: „Hintergrund entfernen"**

Er hat drei Fallen, jede einzeln teuer bezahlt (2026-08-07):

```
button.uploader-thumbnails-ux__image       Kachel, öffnet den Editor
div[role=dialog].uploader-editor           der Editor
  button.icon-btn[title='Hintergrund entfernen']
  button.btn--primary  "Speichern"         erscheint ERST nach dem Klick
  button.btn--primary  "Fertig"            im Normalzustand
```

1. **Ohne „Speichern" wird das Ergebnis verworfen.** Nach dem Freistellen
   wechselt der Editor in einen Bestätigungszustand: Die Blätterpfeile
   verschwinden, „Fertig" wird durch „Abbrechen"/„Speichern" ersetzt.
2. **Nicht über die Pfeile blättern.** Der Schritt meldete „3 von 3",
   tatsächlich wurde dreimal dasselbe Foto bearbeitet. Jedes Foto gezielt
   über seine Kachel öffnen — mehr Klicks, dafür weiß man, wo man ist.
3. **Der Speichern-Knopf erscheint, bevor eBay fertig gerechnet hat.** Wer
   sofort drückt, speichert das unbearbeitete Bild: Die Kacheladresse ändert
   sich, der Hintergrund bleibt. Nach dem Freistellen **acht Sekunden fest
   warten**, dann erst speichern.

**Der Nachweis geht über die Kacheladressen**, nicht über gezählte Klicks:
Die Kacheln tragen ihr Bild als CSS-`background-image` von `i.ebayimg.com`,
und nach einer echten Bearbeitung steht dort eine andere Adresse.

**Grenze des Werkzeugs:** Bei Nahaufnahmen mit Hand oder unruhigem
Hintergrund kann eBay das Teil nicht isolieren — dort bleibt der Hintergrund,
egal wie oft man klickt. Das ist kein Fehler der Automation.

**Anzeigentarif, Rücknahme, Versand — Stand 2026-08-09**

Am 2026-08-09 hat eBay die untere Formularhälfte umgebaut. `se-field-card`
und `div.promoted-listing-simple` gibt es **nicht mehr** (je null Treffer),
Karten und Dialoge sind durch Felder direkt im Formular ersetzt:

| | vorher | jetzt |
|---|---|---|
| Anzeigentarif | `div.promoted-listing-simple` + „Eigenen Anzeigentarif" | Schalter `input[role=switch][name='Basis auswählen']`, danach Feld `input[name='adRate']` |
| Versandkosten | Knopf „Versandkosten bearbeiten" → Dialog | `input[name='domesticShippingPrice1']` direkt |
| Auslandsversand | `isInternationalShippingOn` | `intlShippingServicePref` |
| Rücknahme | Karte → Dialog → drei Felder | nur noch Anzeige: `div.returns-field-display__container` |

Drei Dinge, die dabei Geld kosten:

- **`adRate` steht auf 11 %**, sobald „Basis" eingeschaltet ist. Wer es nicht
  überschreibt, zahlt das Fünfeinhalbfache der Vorgabe von 2 %. „Basis"
  kostet pro Verkauf, „Premium" pro Klick — genommen wird Basis.
- **Das Versandkostenfeld ist vorbelegt**, und zwar mit dem Preis des
  *zuletzt eingestellten* Artikels (gemessen: 23,99 €). Wer es nicht
  überschreibt, verkauft zum falschen Versandpreis, und man sieht es dem
  Entwurf nicht an.
- **Die Rücknahme merkt sich eBay am Konto** und zeigt sie nur noch an. Am
  frisch angelegten Entwurf stand dort bereits „Akzeptiert innerhalb von
  14 Tage / Käufer zahlt den Rückversand" und „Keine internationale
  Rücknahme". `draft.py` **klickt hier nicht mehr, sondern prüft nur** — ein
  Editor, den niemand ausgemessen hat, wäre geraten, und die Rücknahme ist
  rechtlich bindend. Stimmt sie nicht, nennt der Bericht den Wortlaut.

## Warum jeder Schritt sein Ergebnis kontrolliert

`_step()` nimmt eine Kontrollfunktion. Ohne sie galt ein Schritt als
erfolgreich, sobald keine Ausnahme flog — im Echtlauf trugen drei Schritte
gar nichts ein, während der Bericht meldete, es sei nichts mehr von Hand zu
tun. **Ein Bericht, der lügt, ist schädlicher als einer, der Arbeit
auflistet.** Neue Formularschritte deshalb immer mit Kontrolle versehen.

## Wenn eBay das Formular umbaut

Die Selektoren in `draft.py` sind der wartungsanfälligste Teil. Jeder Schritt
läuft über `_step()` und schreibt bei Misserfolg eine Warnung in den Bericht,
statt den Durchlauf abzubrechen — häufen sich dort Meldungen, hat sich das
Formular geändert. Zum Nachsehen:

```bash
.venv/bin/python -m autolister.pipeline Eingang/<ordner> --trockenlauf
```

Füllt alles aus, speichert nichts, Browser bleibt sichtbar.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
