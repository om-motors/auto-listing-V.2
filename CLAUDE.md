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
- **CAPTCHAs werden nicht gelöst oder umgangen.** Bei einer Sicherheitsabfrage
  bricht die Pipeline mit `CaptchaBlocked` ab und bittet den Nutzer.
- **Preise werden gerechnet, nicht geschätzt.** `compose.py` bildet den
  **Median** der Vergleichsangebote und wirft vorher alles außerhalb des
  1,5-fachen Quartilsabstands weg. Kein Mittelwert: der Markt ist stark
  gespreizt (gemessen 140 € bis 1236 € für dasselbe Teil), da zieht ein
  Mittelwert nach oben.
- **Kostenlos ist der Normalfall.** Neue Funktionen müssen ohne API auskommen
  oder sauber darauf verzichten können.

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
4. Das Feld heißt real `OE/OEM Referenznummer(n)`; exakter Vergleich schlägt
   fehl, Präfix-Vergleich nötig. Und sobald ein Merkmal gefüllt ist,
   **entfernt eBay dessen `aria-label`** — das Zurücklesen braucht deshalb
   Rückfallebenen.

**Anzeigentarif, Rücknahme, Versand**

- Anzeigentarif: erst `div.promoted-listing-simple input[role=switch]`
  einschalten, sonst existiert der Block nicht. Die Schnellauswahl kennt nur
  8/10/12 % — für 2 % führt der Weg über `button.custom-rate-button-switch`.
- Rücknahme und Versand sitzen hinter **Karten**, nicht hinter
  Bearbeiten-Knöpfen: `button.se-field-card__body`. Im Dialog dann
  `label.field__label` „Rücknahme im Inland" und „Fertig".

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
