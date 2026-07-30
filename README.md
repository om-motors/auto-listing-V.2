# Auto-Listing

Aus Produktfotos werden automatisch fertige eBay-Entwürfe — **ohne dass Claude
laufen muss**. Fotos in den Ordner `Eingang/` legen oder auf der Upload-Website
hochladen, den Rest erledigt der Mac im Hintergrund.

Veröffentlicht wird **nie** automatisch. Es entstehen ausschließlich Entwürfe,
die du in Ruhe prüfst und selbst freigibst.

---

## Einrichtung (einmalig, ca. 10 Minuten)

```bash
./install.sh
```

Das installiert alles und richtet den Autostart ein. Danach fehlen noch zwei
Dinge, die nur du machen kannst:

**1. API-Key eintragen.** Auf <https://console.anthropic.com> einen Key
erstellen und in die Datei `.env` eintragen:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Ohne Key kann der Mac die Fotos nicht selbstständig auswerten — das ist genau
der Teil, der Auto-Listing von Claude unabhängig macht.

**2. Einmalig bei eBay einloggen.** Es öffnet sich ein Browserfenster:

```bash
.venv/bin/python -m autolister.login
```

Dort ganz normal einloggen und **„Angemeldet bleiben" anhaken**, dann das
Fenster schließen. Der Login bleibt gespeichert; das muss man nur alle paar
Monate wiederholen.

**Prüfen, ob alles bereit ist:**

```bash
.venv/bin/python -m autolister.doctor
```

---

## Täglicher Gebrauch

Es gibt zwei Wege — beide führen zum selben Ergebnis:

### Weg A: Ordner

Fotos in `Eingang/` legen. Entweder einen Unterordner pro Teil, oder lose
Fotos, wenn es nur ein Teil ist. Nach etwa 25 Sekunden Ruhe startet die
Verarbeitung von selbst.

### Weg B: Upload-Website (auch vom Handy)

Im Browser öffnen — auch vom iPhone im selben WLAN:

```
http://<name-deines-macs>.local:8790
```

Fotos auswählen, hochladen, fertig. Ein Upload = ein Teil.

### Was dann passiert

1. **Fotos aufbereiten** — HEIC wird umgewandelt, Bilder werden verkleinert
2. **Teilenummer lesen** — bei unscharfer Nummer automatisch ein zweiter
   Durchgang in voller Auflösung
3. **Preise recherchieren** — vergleichbare gebrauchte Original-Angebote auf
   eBay.de, Ausreißer werden gekappt
4. **Entwurf anlegen** — Titel, Beschreibung, Artikelmerkmale, Versandstufe,
   Rücknahme, Preisvorschläge, Anzeigentarif 2 %
5. **Entwurf speichern** — erscheint auch in der eBay-App unter „Entwürfe"
6. **Bescheid geben** — Mac-Mitteilung plus Bericht in `Berichte/`

Die Fotos wandern anschließend nach `Erledigt/<Teilenummer>/`.

---

## Ergebnisse prüfen

- **`Berichte/`** — pro Teil ein Bericht: Titel, Preis, auf welchen
  Vergleichsangeboten der Preis beruht, geschätzte Versandstufe, offene Punkte
  und ein Screenshot des ausgefüllten Formulars.
- **`Fehler/`** — falls etwas schiefging, mit Screenshot.
- **`logs/`** — laufendes Protokoll der Hintergrunddienste.

Der Bericht listet unter **„Bitte prüfen"** jeden Schritt auf, den die
Automation nicht sicher ausfüllen konnte. Diese Punkte im eBay-Entwurf
kontrollieren, bevor du ihn veröffentlichst.

---

## Sicherheit

- **Es wird niemals veröffentlicht.** Buttons wie „Artikel anbieten",
  „einstellen" oder „Verkaufen" sind im Code hart gesperrt: bevor geklickt
  wird, wird der Buttontext geprüft und ein solcher Klick abgebrochen.
- **Sicherheitsabfragen werden nicht umgangen.** Zeigt eBay ein CAPTCHA,
  stoppt die Automation und meldet sich bei dir.

---

## Erster Testlauf (empfohlen)

Beim allerersten Mal lohnt sich ein Trockenlauf. Der füllt das Formular
komplett aus, speichert aber nichts — du siehst im sichtbaren Browserfenster,
ob jedes Feld richtig landet:

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

## Einstellungen (`.env`)

| Einstellung | Standard | Bedeutung |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | **Pflicht.** Key von console.anthropic.com |
| `AUTOLISTER_VISION_MODEL` | `claude-opus-4-8` | Modell fürs Fotolesen |
| `AUTOLISTER_VISION_MAX_EDGE` | `1568` | Bildgröße für die Analyse (kleiner = schneller) |
| `AUTOLISTER_VISION_PARALLEL` | `4` | Wie viele Teile gleichzeitig analysiert werden |
| `AUTOLISTER_HEADLESS` | `0` | `1` = Browser unsichtbar (eBay erkennt das eher) |
| `AUTOLISTER_WEBAPP_PORT` | `8790` | Port der Upload-Website |
| `AUTOLISTER_SETTLE_SECONDS` | `25` | Ruhezeit im Eingang vor dem Start |

---

## Wenn etwas klemmt

**„Kein Claude-Zugang"** — API-Key fehlt in der `.env`.

**„Nicht bei eBay eingeloggt" / Sicherheitsabfrage** —
`.venv/bin/python -m autolister.login` ausführen, einloggen bzw. die Abfrage
bestätigen.

**Viele Punkte unter „Bitte prüfen"** — eBay hat vermutlich das
Verkaufsformular umgebaut. Die betroffenen Felder stehen im Bericht; einmal
mit `--trockenlauf` bei sichtbarem Browser nachschauen zeigt, was sich
geändert hat.

**Nichts passiert beim Ablegen von Fotos** —
`launchctl list | grep autolisting` prüfen und notfalls `./install.sh`
erneut ausführen.
