# Arbeitsstand

Diese Datei ist die Übergabe zwischen zwei Claude-Sitzungen. Nach einem `/clear`
liest Claude `CLAUDE.md` automatisch, aber nicht den bisherigen Gesprächsverlauf.
Hier steht deshalb, **wo die Arbeit gerade steht und was als Nächstes ansteht**.

**Regel für Claude:** Diese Datei am Ende jeder Sitzung aktualisieren, in der sich
der Stand geändert hat — erledigte Punkte streichen, neue Erkenntnisse eintragen.
Dauerhaftes Wissen über das Projekt gehört dagegen nach `CLAUDE.md`, nicht hierher.

Letzte Aktualisierung: 2026-08-01 (nach dem ersten Echtlauf)

---

## Aktueller Stand

- Alles auf `main` (`f8cf98a`), lokal und auf GitHub gleichstand
- **Der erste Echtlauf gegen das echte eBay-Formular ist gelaufen und war
  erfolgreich.** Details unten. Die Selektoren in `draft.py` sind damit erstmals
  live bestätigt — für genau eine Formularvariante an genau einem Tag.

---

## Ergebnis des ersten Echtlaufs (2026-08-01, 15:35–15:40)

Testteil: Audi Sonnenblende `8K0 857 552`, 3 Fotos, Ordner `Eingang/Test 1`,
Betriebsart `lokal`. Entwurf: `draftId=5184972239823`.

**Nichts wurde veröffentlicht.** Nachgeprüft an `ebay.de/sh/lst/active`:
„Sie haben anscheinend keine aktiven Angebote." Der Entwurf steht in der
Entwurfsliste, `draftId` steht weiterhin in der URL.

Alle Feldwerte am Entwurf zurückgelesen (rein lesend, ohne Klicks):

| Vorgabe | Feld | Wert | |
|---|---|---|---|
| Titel | `title` | Original Audi A4 B8 A5 Sonnenblende vorne rechts 8K0857552 | ✅ |
| Sofort-Kaufen | `format` | `FixedPrice` | ⚠️ eBay-Vorgabe, nicht gesetzt |
| Preis | `Artikelpreis` | `29,90` | ✅ |
| Preisvorschläge | `bestOfferEnabled` | `true` | ✅ |
| Rücknahme | `returnPolicy` | `true` | ✅ |
| Frist 14 Tage | `returnDuration` | `Days_14` | ✅ |
| Käufer zahlt Rückversand | `returnShippingPayer` | `Buyer` | ✅ |
| Anzeigentarif 2 % | `promotedListingSelection` + `customAdRateField` | `true` / `2` | ✅ |
| Keine Auslandsrücknahme | Internationaler Versand | aus | ⚠️ eBay-Vorgabe, nicht gesetzt |
| Zustand Gebraucht | — | „Gebraucht" | ✅ |
| Merkmale | Hersteller / Produktart / Herstellernummer / OE-Referenz | Audi / Sonnenblende / 8K0857552 / 8K0857552 | ✅ |
| Einbauposition weglassen | — | leer | ✅ |
| Versandkosten | Wer zahlt | Käufer, 23,99 € | ❌ falsche Stufe, siehe F1 |

**Was das über die Prüfbefunde sagt:** Die Befunde A1, A2, A5, B2, B3, B4, B5,
C1, C2 haben in diesem Lauf **nicht ausgelöst**. Das entkräftet sie nicht — der
Speichern-Hauptweg wurde genommen, der `.last`-Fallback (A5) kam nie zum Zug, und
kein unbekannter Dialog tauchte auf, der die Blindklick-Schleife (A1) hätte
gefährlich werden lassen. Ein Lauf, in dem eine Sperre nicht herausgefordert
wurde, sagt nichts über die Sperre aus.

**Zwei Vorgaben stimmen nur zufällig:** `format=FixedPrice` und der
ausgeschaltete internationale Versand sind eBays Voreinstellungen, nicht das
Werk der Pipeline (D1, D2 stehen unverändert). eBay merkt sich zuletzt genutzte
Verkaufseinstellungen — nach einer einzigen manuell angelegten Auktion kippt das.

**Der Bericht enthielt keine einzige Warnung.** Bei sieben kontrollfreien
Schritten (B1) ist das erwartbar und kein Qualitätsnachweis: die falsche
Versandstufe F1 stand ungewarnt im Bericht, und `versand_kontrolle` hat sie
bestätigt, weil sie gegen denselben falschen Wert prüft.

---

## Offene Aufgabe: adversarische Prüfung von `draft.py`

**Leitfragen des Nutzers**

1. Kann die Automation versehentlich veröffentlichen?
2. Melden Kontrollfunktionen fälschlich Erfolg?
3. Welche Selektoren brechen beim nächsten eBay-Umbau?

**Status:** teilweise beantwortet. Ein Prüflauf mit vier parallelen Prüfern wurde
mitten in der Widerlegungsrunde abgebrochen (Sitzungsende). Gerettet aus
`journal.jsonl` wurden 21 Rohfunde von **drei** der vier Prüfer.

Was fehlt:

- Der Prüfer **„Melden Kontrollfunktionen fälschlich Erfolg?"** hat nie
  geantwortet — ausgerechnet Leitfrage 2. Die Funde unten zu diesem Thema
  stammen aus den Nachbar-Blickwinkeln und aus eigener Durchsicht, sind also
  nicht erschöpfend.
- Die **Widerlegungsrunde lief nicht durch**. Kein Fund unten wurde von einem
  Skeptiker angegriffen. Was unten nicht ausdrücklich als *selbst verifiziert*
  markiert ist, ist eine **unbestätigte Behauptung** und vor dem Beheben am Code
  gegenzulesen.

Legende: ✅ = am Code selbst nachgeprüft · ❓ = aus dem abgebrochenen Lauf, ungeprüft

---

### A — Kann die Automation veröffentlichen?

Kurzantwort: **Der Hauptweg ist sauber, die Nebenwege sind es nicht.** Der
Speichern-Hauptpfad (`draft.py:726`) nutzt `get_by_role` mit verankertem Regex
`^(Entwurf speichern|Speichern)$` und trifft die Veröffentlichen-Knöpfe nicht.
Gefährlich sind die Rückfallebenen und die Dialogbehandlung.

**A1 ✅ `_dialoge_schliessen` klickt blind jeden Dialogknopf — `draft.py:170`**
Der Selektor `[role='dialog'] button[aria-label]` ist keine Schließen-Erkennung,
sondern trifft *jeden* beschrifteten Knopf in einem offenen Dialog. Geklickt wird
roh mit `knopf.click()` (Zeile 177), nicht über `_safe_click` — `FORBIDDEN` gilt
hier also gar nicht. Die Funktion läuft 4 Runden je Aufruf und wird an fünf
Stellen gerufen (275, 457, 485, 613, 683), zuletzt also dann, wenn das Angebot
bereits vollständig ausgefüllt ist. Zeigt eBay dort einen Bestätigungsdialog,
wird dessen Primärknopf geklickt.
*Behebung:* `aria-label` gegen `/schließen|close|dismiss/i` prüfen, und alle
Klicks dieser Funktion über `_safe_click` führen.

**A2 ✅ `_safe_click` ist fail-open — `draft.py:71-77`**
```python
except Exception:
    text = ""
if FORBIDDEN.search(text):   # "" trifft nie zu -> Klick geht durch
```
Die Sperre öffnet genau dann, wenn sie nichts weiß: bei Timeout (Textprüfung hat
3 s, der Klick danach 8 s), bei abgelöstem Element, und bei Knöpfen ohne
Textknoten (`<input type=submit value="…">`, Icon-Knöpfe mit `aria-label`).
*Behebung:* fail-closed — bei leerem oder unlesbarem Text nicht klicken. Und die
Beschriftung aus `inner_text` + `aria-label` + `title` + `value` zusammensetzen.

**A3 ✅ `FORBIDDEN` kennt „veröffentlichen" nicht — `draft.py:25`**
```python
FORBIDDEN = re.compile(r"anbieten|einstellen|verkaufen|list it|sell it", re.I)
```
Dass die Sperre heute greift, hängt an dem einen Wort „einstellen". Fehlt:
`veröffentlichen`, `aktivieren`, `kostenpflichtig`, `bestätigen`, `publish`,
`list for free`, `confirm and list`.

**A4 ✅ 18 von 23 Klick-Aktionen umgehen `_safe_click` — ganze Datei**
Über die Sperre laufen nur die Zeilen 439, 653, 706, 729, 735. Direkt geklickt
wird in 163, 177, 276, 294, 301, 494, 501, 558, 568, 576, 614, 624, 631, 644,
684, 695 sowie `check()` in 544 und `select_option()` in 636. Besonders heikel
sind die `get_by_text`-Rückfallebenen in 621 und 678: die treffen das erste
Element in Dokumentreihenfolge mit diesem Text — oft ein umschließender
Container, dessen Mittelpunkt auf einem beliebigen Steuerelement liegt.
*Behebung:* einen einzigen Klick-Engpass erzwingen und direkte `.click()` im
Modul per Test verbieten.

**A5 ✅ Speichern-Fallback greift nach `.last` — `draft.py:734`**
```python
btn = page.locator("button", has_text="Speichern").last
```
`has_text` ist Teilstring-Suche und sucht auch in Kindelementen; `.last` nimmt
den letzten Treffer in DOM-Reihenfolge — in einer klebrigen Fußleiste steht dort
typischerweise der Veröffentlichen-Knopf. Anders als der Hauptweg (Zeile 729)
prüft der Fallback nicht auf `is_visible()`. Einzige Bremse bleibt `_safe_click`,
also A2.

**A6 ✅ Nach dem Speichern wird nichts kontrolliert — `draft.py:740`**
Auf den Klick folgt nur `wait_for_timeout(4000)`. Es wird nie geprüft, ob es ein
Entwurf blieb. `pipeline.py:124` meldet danach bedingungslos „eBay-Entwurf
gespeichert" — ein veröffentlichtes Inserat sähe im Bericht identisch aus.
*Behebung:* nach dem Speichern prüfen, dass `draftId` noch in der URL steht und
kein „Ihr Artikel ist online" auf der Seite ist.

**A7 ❓ Enter im Formular kann implizites Submit auslösen — `draft.py:303`**
`feld.press("Enter")` läuft bei jedem frei getippten Merkmal (Regelfall bei
Teilenummern). Liegt das Feld in einem `<form>`, aktiviert HTML implizit dessen
ersten Submit-Knopf. Tastendrücke laufen an `_safe_click` vorbei.
*Prüfen im Echtlauf:* `feld.evaluate("e => !!e.form")`.

**A8 ❓ Trockenlauf ist nicht klickfrei — `draft.py:720`**
`--trockenlauf` überspringt nur den Speichern-Block. Der Entwurf ist zu dem
Zeitpunkt längst angelegt (Zeile 449, `draftId`), eBay speichert automatisch, und
`_dialoge_schliessen` ist fünfmal gelaufen. Die Meldung „Formular ausgefüllt,
aber NICHT gespeichert" liest sich als Sicherheitszusage, die sie nicht ist.
*Behebung:* Meldungstext ehrlich machen und die Entwurfsadresse nennen.

---

### B — Melden Kontrollfunktionen fälschlich Erfolg?

Kurzantwort: **ja, an mindestens vier Stellen.**

**B1 ✅ Sieben von elf Schritten haben gar keine Kontrolle**
`_step(warnings, name, fn)` ohne `pruefen` liefert `True`, sobald keine Ausnahme
fliegt. Ohne Kontrolle sind: Foto-Upload (472), Titel (481), Zustand (495),
Beschreibung (505), Preis (539), Preisvorschläge (545), Screenshot (718).
Mit Kontrolle nur: Merkmale (528), Anzeigentarif (597), Rücknahme (667),
Versand (712).

**B2 ✅ Zwei davon können still lügen — `draft.py:475` und `draft.py:541`**
Beide benutzen `if x.count() and …:` ohne `else`. Trifft der Selektor nicht,
passiert nichts, es fliegt nichts, der Schritt gilt als erledigt:
- `ensure_title` — der Titel bliebe der aus der Suggest-Seite
- `enable_best_offer` — „Preisvorschläge zulassen" ist eine feste Vorgabe und
  würde geräuschlos fehlen. Der Selektor unterstellt eine Checkbox, während der
  Anzeigentarif-Block direkt darunter (553) von `input[role='switch']` ausgeht.

`notify.SCHRITT_KLARTEXT` hat für beide bereits einen Klartext-Eintrag
(`notify.py:34,37`) — die Aufgabe stünde also im Bericht, wenn der Schritt sie je
melden würde.

**B3 ✅ Anzeigentarif-Kontrolle ist praktisch immer wahr — `draft.py:594`**
```python
return config.ANZEIGENTARIF_PROZENT + " %" in text or ...
```
`"2 %" in "12 %"` ist `True`. Die Schnellauswahl zeigt fest 8/10/12 % — der
Abschnittstext enthält also fast immer „12 %". Bleibt der Tarif bei eBays
Voreinstellung (typisch 10 %), meldet der Bericht trotzdem Erfolg.
*Behebung:* Wortgrenze statt Teilstring, und zum Schreiben und Zurücklesen
denselben Locator verwenden statt zweier verschiedener Unions (572 vs. 587).

**B4 ✅ Rücknahme-Kontrolle prüft nur „nicht keine" — `draft.py:666`**
```python
return bool(text) and "Keine Rücknahme" not in text
```
`enable_returns` setzt drei Dinge, geprüft wird eines. Frist (630) und „Käufer
zahlt Rückversand" (643) hängen beide an `if count() and is_visible()` ohne
`else` — passt der Textanker nicht mehr, werden sie still übersprungen. Ein
Entwurf mit eBays Voreinstellung (30 Tage, Verkäufer zahlt) besteht die Kontrolle.
*Behebung:* auf alle drei Vorgaben prüfen; besser noch, die drei Teilaktionen als
eigene `_step` mit eigener Kontrolle führen.

**B5 ✅ Merkmal-Rückfallebene bestätigt sich am Nachbarfeld — `draft.py:332-339`**
Die letzte Ebene fragt, ob *irgendein* Merkmalknopf den Wert anzeigt. „Herstellernummer"
und „OE/OEM Referenznummer" bekommen aber denselben Wert (510-511) und werden in
dieser Reihenfolge gesetzt — die zweite Zuweisung bestätigt sich also am Knopf der
ersten. Das OE-Feld bliebe leer, ohne Hinweis im Bericht. Genau das Feld, über das
Käufer Kfz-Teile suchen.
*Behebung:* Suche auf den eigenen Zeilencontainer begrenzen; wird die eigene Zeile
nicht gefunden, leeren String zurückgeben und den Schritt scheitern lassen.

**B6 ✅ Merkmal-Kontrolle vergleicht nur 6 Zeichen — `draft.py:527`**
`str(value)[:6].lower() in steht.lower()` — bei `A2053510005` wird nur `a20535`
geprüft. Ein abgeschnittener oder verwechselter Wert besteht die Prüfung.

**B7 ❓ Versand-Kontrolle kann sich selbst bestätigen — `draft.py:709`**
`listing.get("versandpreis", 0)` steht sowohl im Setzen (674) als auch im Prüfen
(710). Fehlt der Schlüssel, wird „0,00" eingetragen *und* „0,00" gesucht.
*Anmerkung:* `compose.compose_listing` setzt `versandpreis` immer (compose.py:210),
der Fall ist also derzeit nicht erreichbar — aber die Bauart ist falsch.
`listing["versandpreis"]` verwenden, damit ein fehlender Wert auffällt.

---

### C — Welche Selektoren brechen zuerst?

Der Prüfer für diesen Blickwinkel hat geantwortet, aber die Liste blieb dünn —
seine Funde landeten überwiegend in Abschnitt B. **Hier ist noch Arbeit offen.**
Nicht systematisch bewertet wurden bisher: die generierten CSS-Klassennamen
(`se-expand-button__button`, `se-field-card__body`, `promoted-listing-simple`,
`custom-rate-button-switch`, `condition-recommendation-value`, `textbox__control`,
`btn--primary`, `iframe#se-rte-frame__summary`), der `ancestor::*[…][4]`-Sprung in
`_abschnitt_text` (124), die festen `wait_for_timeout`-Wartezeiten, und die
Scroll-Schleife in `_formular_bereit` (213) auf einer Seite, die während des
Scrollens nachwächst.

**C1 ❓ Zustand „Gebraucht" ohne Kontrolle — `draft.py:491`**
`button.condition-recommendation-value` ist ein feature-benannter Klassenname aus
einem Empfehlungs-Widget. Kein `pruefen`. Wird der Klick wirkungslos (React-Umbau,
das echte Ziel wäre ein inneres `label`/`input`), speichert die Pipeline einen
Entwurf mit eBays Vorbelegung — bei Kfz-Teilen oft „Neu". Ein gebrauchtes Teil als
„Neu" zu inserieren ist der teuerste denkbare Fehler, und der Bericht schwiege dazu.

**C2 ❓ Preisfeld über `.first` — `draft.py:535`**
```python
page.locator("input[name='price'], input[aria-label*='Preis' i]").first
```
Bei einer Selektorliste entscheidet die Dokumentreihenfolge, nicht die Reihenfolge
im Selektor — `input[name='price']` hat keinen Vorrang. `aria-label*='Preis'`
trifft per Teilstring auch „Startpreis", „Mindestpreis" oder „Preisvorschläge
automatisch ablehnen unter". Ohne Kontrolle bliebe der Fehlgriff unbemerkt.
*Behebung:* auf `input[aria-label='Artikelpreis']` festnageln — dieser exakte Name
steht bereits in Zeile 205 — und `input_value()` gegen den erwarteten Betrag prüfen.

---

### D — Vorgaben, die gar nicht umgesetzt sind

**D1 ✅ „Sofort-Kaufen, keine Auktion" wird nirgends gesetzt**
`grep` über `autolister/` findet dazu genau einen Treffer: den Kommentar in
`draft.py:530`. Es gibt keinen Formularschritt für das Angebotsformat und keinen
Eintrag in `notify.SCHRITT_KLARTEXT`. Dass Sofort-Kaufen die Voreinstellung ist,
ist eine unbelegte Annahme — eBay merkt sich zuletzt genutzte Verkaufseinstellungen.
Ein Auktionsentwurf mit dem gerechneten Median als Startpreis wäre die Folge, ohne
jede Meldung.

**D2 ✅ „Keine internationale Rücknahme" existiert im Code nicht**
`grep -i "international\|ausland"` über `autolister/` liefert null Treffer. Weder
Konstante in `config.py` noch Schritt in `enable_returns`. Eine aus einer früheren
Vorlage aktive Auslandsrücknahme bliebe aktiv, und B4 würde es nicht bemerken.

---

### E — Funde außerhalb von `draft.py`

**E1 ✅ Versandstufe „Groß" fällt auf 7,69 € statt 60 € — `compose.py:46` + `:210`**
Der KI-Prompt bietet weiterhin die abgeschaffte Stufe „Groß" an („Groß
(Stoßstangen, Türverkleidungen)"), `config.VERSAND_STUFEN` kennt sie nicht mehr.
`preise.get(stufe, 7.69)` macht daraus stillschweigend Standardversand — rund
52 € Verlust pro Stoßstange, und `versand_kontrolle` vergleicht gegen denselben
falschen Wert. Betrifft nur `api`/`cli`; `ableiten.py` kennt „Groß" korrekt nicht
mehr, der Prompt wurde beim Umstellen vergessen.
*Behebung:* „Groß" aus `PROMPT` streichen und Sperriges der Spedition zuordnen;
unbekannte Stufen nicht stumm auf 7,69 € abbilden, sondern auf die teuerste Stufe
plus Hinweis in `hinweise_fuer_nutzer`.

**E2 ❓ Captcha wird nur per URL und nur früh geprüft — `draft.py:47`**
`_check_captcha` läuft an drei Stellen (371, 428, 445) und erkennt nur URL-Muster.
Eine Sicherheitsabfrage beim Foto-Upload oder beim Speichern — oder eine, die als
Overlay auf derselben URL erscheint — führt zu Timeout-Kaskaden und am Ende zu
„Speichern-Button nicht gefunden". Der Nutzer bekommt eine Fehlermeldung, die eine
kaputte Selektorlage nahelegt, statt der Anleitung `python -m autolister.login`.

**E4 ✅ Zwei Hinweisfenster wurden nicht geschlossen — `draft.py:170`**
Im Echtlauf meldete `_dialoge_schliessen` „0 Hinweisfenster geschlossen", und auf
dem Screenshot stehen zwei offene Tipp-Fenster: „Artikelkategorie überprüfen" und
„Wählen Sie ein Angebotsformat aus" — letzteres verdeckte das Preisfeld
vollständig. Die Schließen-Selektoren treffen diese Fenster also nicht. Harmlos
in diesem Lauf, aber es heißt: die Funktion tut nicht, was sie behauptet, und es
ist dieselbe Funktion, die Risiko A1 trägt.

**E3 ❓ „Entwurf gespeichert" auch bei komplettem Fehlschlag — `pipeline.py:122`**
`_finish` verschiebt die Fotos aus dem Eingang und meldet unbedingt Erfolg mit
Titel und Preis, unabhängig davon, wie viele Schritte in `warnings` gelandet sind.
Umgekehrt bleibt bei einem Abbruch mittendrin ein halb gefüllter Auto-Save-Entwurf
bei eBay liegen: `DraftError` trägt nie `draft_url`, `notify.write_error` nennt
keine Adresse, und der Watcher legt beim nächsten Lauf einen zweiten Entwurf für
dasselbe Teil an.
*Behebung:* Zahl der offenen Punkte in die Mitteilung übernehmen, `draft_url` im
`DraftError` mitführen.

---

### F — Im Echtlauf neu gefunden

**F1 ✅ Fremde Angebotstitel kippen die Versandstufe — `ableiten.py:191`**
Die Sonnenblende bekam **Mittel (23,99 €)** statt **Standard (7,69 €)** — 16,30 €
zu viel auf einen Artikel für 29,90 €. Ursache:

```python
text = ((teil or "") + " " + zusatztext).lower()
for stufe, stichwoerter in VERSAND_STICHWOERTER:
    if any(s in text for s in stichwoerter):
        return stufe
```

`zusatztext` sind die Titel der fünf ersten Vergleichsangebote
(`compose.py:132`). Die Stufenliste ist von groß nach klein geordnet und das
**erste** Stichwort gewinnt — ein einzelnes „Spiegel" oder „Verkleidung" in einem
fremden Titel schlägt also den echten Teilnamen, der nur über „blende" in
„Standard" fällt. Nachgestellt:

```
versandstufe('Sonnenblende', '')                                -> Standard
versandstufe('Sonnenblende', 'Sonnenblende Spiegel Panel')      -> Mittel
versandstufe('Sonnenblende', 'Sonnenblende Verkleidung grau')   -> Mittel
```

Der Docstring sagt „im Zweifel die kleinste" — die Umsetzung tut das Gegenteil.
*Behebung:* den Teilnamen zuerst allein prüfen und den Zusatztext nur heranziehen,
wenn er nichts ergibt; oder Treffer über alle Stufen sammeln und bei Uneinigkeit
zwischen Teilname und Zusatztext die kleinere Stufe nehmen plus Hinweis im
Bericht. `versand_kontrolle` fängt den Fehler nicht, weil sie gegen denselben
Wert prüft.

---

## Empfohlene Reihenfolge

1. **A1 + A2** — die Publish-Sperre schließen (`_safe_click` fail-closed,
   Blindklick-Schleife entschärfen). Alles andere kostet Geld, das kostet
   das Konto.
2. **A3 + A5 + A6** — Wortliste erweitern, `.last`-Fallback verankern,
   nach dem Speichern prüfen, dass es ein Entwurf blieb.
3. **B3 + B4 + B5** — die drei Kontrollen, die aktiv lügen. Solange die
   lügen, ist jeder weitere Bericht wertlos.
4. **C1 + D1** — Zustand und Angebotsformat. Beide erzeugen ein falsches,
   aber gültiges Inserat, das niemand meldet.
5. **B1 + B2** — die sieben kontrollfreien Schritte nachrüsten.
6. **E1** — schneller Einzeiler, betrifft nur `api`/`cli`.

---

## Nächste Schritte

- [ ] **F1 beheben** — die falsche Versandstufe ist der einzige Fehler, der im
      Echtlauf tatsächlich Geld gekostet hätte. Schnellster Nutzen.
- [ ] **D1 + D2 nachrüsten** — Angebotsformat und internationale Rücknahme
      stimmen derzeit nur, weil eBays Voreinstellung zufällig passt
- [ ] **E4** — die Tipp-Fenster-Selektoren treffen nicht; beim Nachbessern
      gleich A1 mit erledigen (Blindklick raus, Positivliste rein)
- [ ] Widerlegungsrunde für die mit ❓ markierten Funde nachholen
- [ ] Blickwinkel „lügen die Kontrollfunktionen?" vollständig prüfen (Prüfer fiel aus)
- [ ] Selektor-Brüchigkeit systematisch bewerten (Abschnitt C ist unvollständig)
- [ ] Testentwurf `draftId=5184972239823` aufräumen, wenn er nicht mehr gebraucht wird

Offen geblieben, weil der Echtlauf sie nicht herausgefordert hat: A1, A2, A5, A7,
B2–B5, C1, C2. Für A7 (implizites Submit durch Enter) wäre ein gezielter Test
nötig: `feld.evaluate("e => !!e.form")` an einer Merkmalzeile.

---

## Sitzungsprotokoll

### 2026-08-01 — erster Echtlauf
Merge nach `main` (war über PR #1–#4 auf GitHub bereits erfolgt, lokal nachgezogen
auf `f8cf98a`). Selbsttest grün. Erster Echtlauf mit `Eingang/Test 1` gegen das
echte Formular, mit Speichern — auf ausdrückliche Entscheidung des Nutzers, nachdem
das Risiko aus A2/A5 benannt war.

Ergebnis: Entwurf sauber angelegt, **nichts veröffentlicht**, alle Vorgaben bis auf
die Versandstufe korrekt gesetzt. Ein neuer Fund (F1), einer bestätigt (E4). Neun
Prüfbefunde blieben unberührt, weil der Lauf sie nicht herausforderte. Code
unverändert.

### 2026-08-01 — Audit
Adversarische Prüfung von `draft.py` aufgesetzt (4 Prüfer + Widerlegung). Lauf
brach am Sitzungsende ab; 21 Rohfunde von 3 Prüfern aus dem Journal gerettet und
oben eingearbeitet. 16 davon selbst am Code nachgeprüft. Nichts am Code geändert.
