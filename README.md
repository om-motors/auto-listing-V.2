# Auto-Listing

Aus Produktfotos werden automatisch fertige eBay-Entwürfe — **kostenlos und
ohne dass Claude laufen muss**. Fotos in den Ordner `Eingang/` legen oder auf
der Upload-Website hochladen, den Rest erledigt der Mac im Hintergrund.

Veröffentlicht wird **nie** automatisch. Es entstehen ausschließlich Entwürfe,
die du in Ruhe prüfst und selbst freigibst.

---

## Von null bis zum fertigen Entwurf

### Schritt 0 — MacBook aufklappen

**Du musst nichts tun.** Die beiden Hintergrunddienste starten beim Anmelden von
selbst (`RunAtLoad`) und starten sich nach einem Absturz selbst neu
(`KeepAlive`). Wenn du sichergehen willst:

```bash
launchctl list | grep autolisting
```

Zwei Zeilen = alles läuft. Die mittlere Spalte muss `0` sein; steht dort eine
andere Zahl, ist der Dienst mit Fehler beendet worden — dann `logs/watcher.log`
ansehen. Fehlt eine Zeile ganz:

```bash
launchctl load ~/Library/LaunchAgents/de.ommotors.autolisting.watcher.plist
```

### Schritt 1 — Fotos hineingeben

Je Teil **ein eigener Ordner**. Die Fotos eines Ordners gehören zu einem
Inserat. Mindestens eines davon muss die **eingestanzte Teilenummer scharf und
formatfüllend** zeigen — daran hängt alles Weitere.

**Weg A — vom MacBook (Finder):**
Ordner anlegen in `~/Auto-Listing/Eingang/`, Fotos hineinziehen. Fertig.

**Weg B — vom Handy (gleiches WLAN):**
Im Browser öffnen — `http://MacBook-Air-von-Ogulcan.local:8790`
(oder, falls der Name nicht auflöst, `http://192.168.0.248:8790`; die IP kann
sich ändern, den aktuellen Stand liefert `ipconfig getifaddr en0`).
Fotos auswählen, hochladen. Die Website legt den Ordner selbst an.

> **Kennst du die Teilenummer schon?** Dann benenne den Ordner danach
> (z. B. `8K0857552`) — sie wird dann direkt übernommen, ohne Raten. Namen wie
> „Test 1" oder automatische Upload-Namen erkennt die Pipeline als
> Nicht-Nummern und liest weiter von den Fotos.

### Schritt 2 — warten

Ab jetzt läuft alles allein. Ablauf und ungefähre Dauer:

| | was passiert | Dauer |
|---|---|---|
| 1 | Der Watcher wartet, bis **25 Sekunden Ruhe** im Eingang sind (damit AirDrop und Uploads fertig werden) | 25 s |
| 2 | macOS-Texterkennung liest die Teilenummer, jedes Foto in vier Drehungen | ~5 s |
| 3 | eBay-Recherche: Vergleichsangebote und verkaufte Artikel zur Nummer | ~20 s |
| 4 | Ein **sichtbares Browserfenster** öffnet sich und füllt das Verkaufsformular | 3–5 min |
| 5 | Es klickt **„Speichern"** — nie „einstellen" | |

**Das Browserfenster nicht wegklicken und den Mac nicht zuklappen**, solange es
arbeitet. Zusehen ist ausdrücklich erwünscht: so merkst du, wenn eBay das
Formular umgebaut hat.

### Schritt 3 — Meldung abwarten

Am Ende kommt eine **macOS-Mitteilung** mit Titel und Preis. Gleichzeitig
entsteht ein Bericht:

```bash
open ~/Auto-Listing/Berichte
```

Die neueste Datei ist deine. Lies dort zwei Abschnitte:

- **„Im eBay-Entwurf noch von Hand setzen"** — steht das dort, hat ein
  Formularschritt nicht gegriffen. Die Punkte sind einzeln abhakbar formuliert.
- **„Bitte prüfen"** — Preisgrundlage, Versandstufe und alles, was die Pipeline
  geraten statt gemessen hat.

Fehlen **beide** Abschnitte, ist im Entwurf nichts mehr zu tun.

### Schritt 4 — freigeben (das machst du selbst)

Den Entwurf öffnen (die Adresse steht im Bericht unter **Entwurf**), Fotos und
Preis kurz gegenlesen, dann **selbst** einstellen. Die Automation klickt
grundsätzlich nie auf „einstellen" — das bleibt deine Entscheidung.

Die Fotos sind inzwischen nach `Erledigt/<Teilenummer>/` gewandert, der Eingang
ist wieder leer und bereit für das nächste Teil.

### Wenn etwas schiefgeht

Ein Fehler bricht nur **dieses eine Teil** ab, nicht den ganzen Lauf. Es
entsteht eine Datei in `Fehler/` mit Klartext und Screenshot:

```bash
open ~/Auto-Listing/Fehler
```

Die zwei häufigsten Fälle:

- **„Keine Teilenummer im erkannten Text gefunden"** — das Foto der Nummer war
  zu unscharf oder zu schräg. Ein besseres Foto nachlegen, oder den Ordner
  gleich nach der Teilenummer benennen.
- **„Sicherheitsabfrage" / CAPTCHA** — eBay will dich sehen. Einmal selbst
  einloggen, dann läuft es wieder von allein:
  ```bash
  cd ~/Auto-Listing && .venv/bin/python -m autolister.login
  ```

Im Zweifel zuerst den Selbsttest laufen lassen, er prüft alle Voraussetzungen
auf einmal:

```bash
cd ~/Auto-Listing && .venv/bin/python -m autolister.doctor
```

### Mehrere Teile auf einmal

Einfach mehrere Ordner in den Eingang legen. Die Bildanalyse läuft parallel,
die Browser-Arbeit nacheinander in **einem** Fenster — vier Teile brauchen also
kaum länger als eines plus vier Formulare.

---

## Warum das nichts kostet

Die Teilenummer liest die **in macOS eingebaute Texterkennung** von den Fotos —
dieselbe, die im Vorschau-Programm Text aus Bildern kopiert. Sie läuft auf
deinem Mac, braucht kein Konto und kostet nichts.

Titel, Teilname und Preis kommen aus den **eBay-Angeboten zur selben
Teilenummer**: Wer dasselbe Teil verkauft, schreibt den Namen in den Titel —
das lässt sich auszählen. Ein Sprachmodell ist dafür nicht nötig.

Gemessen an einem echten Mercedes-Differential: Teilenummer gelesen und Entwurf
vorbereitet in **7,5 Sekunden, 0,00 €**.

---

## Einrichtung (einmalig)

```bash
./install.sh
```

Danach fehlt nur noch **ein** Schritt, den nur du machen kannst — einmalig bei
eBay einloggen. Es öffnet sich ein Browserfenster:

```bash
.venv/bin/python -m autolister.login
```

Dort ganz normal einloggen und **„Angemeldet bleiben" anhaken**, dann das
Fenster schließen. Der Login bleibt gespeichert und hält Monate.

**Prüfen, ob alles bereit ist:**

```bash
.venv/bin/python -m autolister.doctor
```

### Wichtig: Datenschutzsperre von macOS

macOS lässt Hintergrunddienste nicht auf **Schreibtisch, Dokumente und
Downloads** zugreifen. Liegt das Projekt dort, startet der Autostart zwar,
kann aber nichts lesen und stürzt wortlos ab.

Zwei Wege — einer reicht:

**A) Projekt umziehen (empfohlen, keine Sonderrechte nötig)**

```bash
mv ~/Desktop/Auto-Listing ~/Auto-Listing && cd ~/Auto-Listing && ./install.sh
```

**B) Festplattenvollzugriff geben (Projekt bleibt liegen)**

Systemeinstellungen → Datenschutz & Sicherheit → Festplattenvollzugriff → „+" →
die Datei `.venv/bin/python` aus dem Projektordner hinzufügen. Einstellungen
öffnen mit:

```bash
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
```

Der Selbsttest sagt dir, ob es klemmt.

---

## Täglicher Gebrauch

Zwei Wege, beide führen zum selben Ergebnis:

### Weg A: Ordner

Fotos in `Eingang/` legen — entweder einen Unterordner pro Teil, oder lose
Fotos, wenn es nur ein Teil ist. Nach etwa 25 Sekunden Ruhe startet die
Verarbeitung von selbst.

### Weg B: Upload-Website (auch vom Handy)

```
http://<name-deines-macs>.local:8790
```

Fotos auswählen, hochladen, fertig. Ein Upload = ein Teil.

### Was dann passiert

1. **Fotos aufbereiten** — HEIC wird umgewandelt, Bilder gedreht und geschärft
2. **Teilenummer lesen** — die Texterkennung prüft jedes Foto in allen vier
   Drehungen, weil eingestanzte Nummern oft hochkant stehen
3. **Nummer bestätigen** — mehrere Lesarten werden auf eBay gegengeprüft; die
   echte Nummer bringt Treffer, ein Lesefehler nicht
4. **Preis ermitteln** — Median der Vergleichsangebote mit derselben Nummer,
   Ausreißer über den Quartilsabstand entfernt
5. **Entwurf ausfüllen** — Fotos, Titel, Zustand „Gebraucht", Beschreibung,
   Artikelmerkmale (Hersteller, Herstellernummer, OE/OEM, Produktart),
   Preis, Preisvorschläge, Anzeigentarif 2 %, Rücknahme 14 Tage Inland mit
   Rückversand zulasten des Käufers, Versandstufe
6. **Speichern** — erscheint auch in der eBay-App unter „Entwürfe"
7. **Bescheid geben** — Mac-Mitteilung plus Bericht in `Berichte/`

Jeder Schritt **kontrolliert sein Ergebnis nach**. Was nicht sicher gesetzt
werden konnte, steht als abhakbare Liste im Bericht — mit dem fertigen Wert
zum Übertragen. Der Bericht behauptet also nie, etwas sei erledigt, das es
nicht ist.

Die Fotos wandern anschließend nach `Erledigt/<Teilenummer>/`.

---

## Ergebnisse prüfen

- **`Berichte/`** — pro Teil ein Bericht: Titel, Preis mit Marktspanne,
  auf welchen Angeboten er beruht, alle gelesenen Nummern-Kandidaten mit
  Bewertung, und ein Screenshot des ausgefüllten Formulars.
- **`Fehler/`** — falls etwas schiefging, mit Screenshot.
- **`logs/`** — laufendes Protokoll der Hintergrunddienste.

Unter **„Bitte prüfen"** steht jeder Schritt, den die Automation nicht sicher
ausfüllen konnte. Diese Punkte im Entwurf kontrollieren, bevor du ihn
veröffentlichst.

Weil Teilname und Modellcodes ohne KI aus fremden Titeln abgeleitet werden,
lohnt ein kurzer Blick auf den Titel — meistens sitzt er, gelegentlich braucht
er einen Handgriff.

---

## Sicherheit

- **Es wird niemals veröffentlicht.** Buttons wie „Artikel anbieten",
  „einstellen" oder „Verkaufen" sind hart gesperrt: vor jedem Klick wird der
  Buttontext geprüft und ein solcher Klick abgebrochen.
- **Sicherheitsabfragen werden nicht umgangen.** Zeigt eBay ein CAPTCHA,
  stoppt die Automation und meldet sich bei dir.

---

## Erster Testlauf (empfohlen)

Beim allerersten Mal lohnt sich ein Trockenlauf. Der füllt das Formular
komplett aus, speichert aber nichts:

```bash
.venv/bin/python -m autolister.pipeline Eingang/mein-teil --trockenlauf
```

---

## Nützliche Befehle

| Zweck | Befehl |
|---|---|
| Selbsttest | `.venv/bin/python -m autolister.doctor` |
| Bei eBay einloggen | `.venv/bin/python -m autolister.login` |
| Eingang jetzt verarbeiten | `.venv/bin/python -m autolister.pipeline` |
| Ein bestimmtes Teil | `.venv/bin/python -m autolister.pipeline Eingang/ordner` |
| Trockenlauf | `... --trockenlauf` |
| Upload-Website von Hand | `.venv/bin/python -m autolister.webapp` |
| Protokoll mitlesen | `tail -f logs/watcher.log` |
| Dienste neu starten | `./install.sh` |

---

## Betriebsarten

Standard ist `lokal` — kostenlos. Umstellen in der `.env`:

| Betriebsart | Kosten | Wann sinnvoll |
|---|---|---|
| `lokal` | **kostenlos** | Standard. Texterkennung auf dem Mac, feste Regeln. |
| `cli` | kostenlos im Rahmen eines Claude-Abos | Wenn das Programm `claude` installiert ist. |
| `api` | **kostenpflichtig pro Foto** | Wenn Fotos so schlecht sind, dass die Texterkennung nicht mehr mitkommt. Braucht `ANTHROPIC_API_KEY`. |
| `auto` | gemischt | Nimmt `api` wenn ein Key da ist, sonst `cli`, sonst `lokal`. |

Die kostenpflichtige Variante ist ein Rückfallnetz, keine Voraussetzung. Fällt
sie aus, schaltet die Pipeline von selbst auf `lokal` zurück, statt abzubrechen.

### Weitere Einstellungen (`.env`)

| Einstellung | Standard | Bedeutung |
|---|---|---|
| `AUTOLISTER_MODUS` | `lokal` | Betriebsart (siehe oben) |
| `AUTOLISTER_OCR_MAX_EDGE` | `4200` | Auflösung fürs Lesen. Kleiner = schneller, aber schlechtere Trefferquote |
| `AUTOLISTER_VISION_PARALLEL` | `4` | Wie viele Teile gleichzeitig ausgewertet werden |
| `AUTOLISTER_HEADLESS` | `0` | `1` = Browser unsichtbar (eBay wird misstrauischer) |
| `AUTOLISTER_WEBAPP_PORT` | `8790` | Port der Upload-Website |
| `AUTOLISTER_SETTLE_SECONDS` | `25` | Ruhezeit im Eingang vor dem Start |

---

## Wenn etwas klemmt

**„Keine Teilenummer gefunden"** — meist ist kein Foto dabei, auf dem die
eingestanzte Nummer scharf und formatfüllend zu sehen ist. Ein Nahaufnahme-Foto
der Nummer ergänzen. Der Bericht zeigt, welchen Text die Erkennung gelesen hat.

**Falsche Teilenummer im Entwurf** — der Bericht listet alle Kandidaten mit
Bewertung. Kam die richtige gar nicht vor, hilft ein besseres Foto; kam sie vor,
wurde aber nicht gewählt, war sie auf eBay nicht auffindbar.

**Nichts passiert beim Ablegen von Fotos** — fast immer die Datenschutzsperre
von macOS (siehe oben). `.venv/bin/python -m autolister.doctor` sagt es dir.

**„Nicht bei eBay eingeloggt" / Sicherheitsabfrage** —
`.venv/bin/python -m autolister.login` ausführen.

**Viele Punkte unter „Im eBay-Entwurf noch von Hand setzen"** — eBay hat
vermutlich das Formular umgebaut. Einmal mit `--trockenlauf` bei sichtbarem
Browser nachschauen; der Screenshot im Bericht zeigt, wie weit es kam.

**Ein Teil, das die Automation nicht lesen kann** — den Ordner einfach nach
der Teilenummer benennen (`Eingang/8K0807832A/`) oder die Nummer beim Upload
ins Namensfeld schreiben. Dann wird sie ohne Raten verwendet.
