# Auto-Listing — eBay-Entwürfe aus Produktfotos

Der Nutzer verkauft gebrauchte Kfz-Teile auf eBay.de (überwiegend Original-Teile
von Audi/VW/Mercedes aus Fahrzeugen mit geringer Laufleistung).

**Wichtig: Das ist inzwischen eine eigenständige Python-Anwendung, keine
Claude-Aufgabe mehr.** Die Pipeline läuft als Hintergrunddienst auf dem Mac und
braucht Claude nicht. Siehe [README.md](README.md) für die Bedienung.

Claude arbeitet hier also **am Code**, nicht an einzelnen Inseraten.

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
- **Übersetzungsverhältnis** bei Differentialen: leer lassen.

| DHL-Größe | Preis (Käufer zahlt) | typische Teile |
|---|---|---|
| Standard | 7,69 € | Halter, Sensoren, Kleinteile, Zierleisten |
| Mittel | 23,99 € | Scheinwerfer, Spiegel, größere Verkleidungen |
| Groß | 79,90 € | Stoßstangen, Türverkleidungen |
| Spedition | 99,90 € | Türen, Hauben, Kotflügel, Sitze |

Im Zweifel die kleinste Stufe wählen und im Bericht darauf hinweisen.

## Erkenntnisse zum eBay-Verkaufsformular

- Einstieg: `ebay.de/sl/prelist/suggest` → Titel eingeben → „Weiter" → das
  Formular legt sofort einen Entwurf an (`draftId` in der URL).
- Die vorgeschlagene **Kategorie ist oft falsch** — prüfen und über
  „Bearbeiten" per Suche korrigieren.
- Artikelmerkmale sind Such-Dropdowns; fehlende Werte über „Eigenen Wert
  hinzufügen" anlegen.
- Speichern über **„Speichern"** ganz unten — *nicht* „Artikel kostenlos
  einstellen", das würde veröffentlichen. Danach landet man in
  `ebay.de/sh/lst/drafts`.
- **Foto-Upload:** direkt per `set_input_files()` in das `input[type=file]`.
  Der frühere Umweg über einen lokalen CORS-Server war nur wegen der
  Chrome-Erweiterung nötig und ist entfallen.

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
