# Auto-Listing — eBay-Entwürfe aus Produktfotos

Der Nutzer verkauft gebrauchte Kfz-Teile auf eBay.de (überwiegend Original-Teile von Audi/VW aus Fahrzeugen mit geringer Laufleistung). Aufgabe von Claude: Aus Produktfotos einen **kompletten eBay-Entwurf** vorbereiten. Der Nutzer prüft den Entwurf nur noch und veröffentlicht ihn selbst.

## Ablauf pro Produkt

Trigger: Der Nutzer schickt Produktfotos im Chat ODER legt sie in `Eingang/` ab (ein Unterordner pro Produkt, oder lose Fotos, die erkennbar zum selben Teil gehören).

### 1. Fotos analysieren
- Alle Fotos lesen. Die **Teilenummer** ist immer auf dem Teil eingestanzt oder auf einem Etikett sichtbar (z.B. `8T0 807 284`, oft mit Suffix-Buchstabe wie `C`). Sorgfältig lesen — 0/O und 8/B verwechselbar. Bei Unsicherheit mit Zoom auf den Bildausschnitt prüfen.
- Zusätzlich erfassen: Markenlogo (Audi-Ringe, VW etc.), Materialkennzeichnung, „Germany", Links/Rechts-Hinweise.

### 2. Teil identifizieren + Marktrecherche (eBay.de im Browser)
- Auf ebay.de nach der Teilenummer suchen (mit und ohne Leerzeichen/Suffix probieren).
- Aus den Treffern ableiten: **Teilname** (z.B. „Halter Stoßfänger vorne links"), **passende Fahrzeuge/Modellcodes** (z.B. „Audi A5 8T 8TA 8F"), übliche **Kategorie**.
- **Preis**: aktive vergleichbare Angebote (gebraucht, Original) sammeln, Ausreißer ignorieren, **Marktdurchschnitt** als Preis ansetzen. Preisbasis im Bericht an den Nutzer dokumentieren (welche Angebote, welche Preise).

### 3. Entwurfsdaten erstellen
- **Titel** (max. 80 Zeichen): `Original <Marke> <Modellcodes> <Teilname> <Position> <Teilenummer>` — Beispiel: `Original Audi A5 8T 8TA 8F Halter Stoßfänger vorne links 8T0807284`
- **Zustand**: immer **„Gebraucht"**
- **Beschreibung**: Vorlage aus `Vorlagen/beschreibung.md`
- **Artikelmerkmale**: Hersteller, Herstellernummer (= Teilenummer), OE/OEM-Referenznummer, Einbauposition, Ursprungsland (falls erkennbar, z.B. „Germany" auf dem Teil)
- **Format**: Sofort-Kaufen (keine Auktion)
- **Versand**: DHL, Größe nach Teil schätzen (siehe Tabelle unten). Im Zweifel kleinste Größe wählen und den Nutzer im Bericht auf die Schätzung hinweisen.

| DHL-Größe | Preis (Käufer zahlt) | typische Teile |
|---|---|---|
| Standard (kleinste) | 7,69 € | Halter, Sensoren, Kleinteile, Zierleisten |
| Mittel | 23,99 € | Scheinwerfer, Spiegel, größere Verkleidungen |
| Groß | 79,90 € | Stoßstangen, Türverkleidungen |
| Spedition (größte) | 99,90 € | Türen, Hauben, Kotflügel, Sitze |

- **Rücknahme**: 14 Tage Inland, Käufer zahlt Rückversand, keine internationale Rücknahme.

### 4. Entwurf bei eBay anlegen (Browser-Automation)
- Claude-in-Chrome verwenden (der Nutzer ist in Chrome bei eBay eingeloggt).
- `https://www.ebay.de/sl/sell` öffnen, Titel/Teilenummer eingeben, ggf. eBay-Vorschlag übernehmen.
- Fotos per Datei-Upload hochladen (Fotos liegen lokal in `Eingang/`; Chat-Anhänge zuerst dorthin speichern).
- Alle Felder ausfüllen, dann **„Entwurf speichern"** — der Entwurf erscheint auch in der eBay-App unter „Entwürfe".
- **WICHTIG: Niemals veröffentlichen.** Der Button „Artikel anbieten"/„Verkaufen" wird ausschließlich vom Nutzer selbst geklickt. Claude speichert nur Entwürfe.

### 5. Abschluss
- Fotos des Produkts nach `Erledigt/<Teilenummer>/` verschieben.
- Bericht an den Nutzer: Teilenummer, Titel, Preis (+ Preisbasis/Vergleichsangebote), gewählte Versandgröße, offene Punkte zum Prüfen.

## Erkenntnisse aus bisherigen Durchläufen (Web-Verkaufsformular)

- Einstieg: `ebay.de/sl/prelist/suggest` → Titel eingeben → „Weiter" → das Formular legt sofort einen Entwurf an (draftId in der URL).
- Die automatisch vorgeschlagene **Kategorie ist oft falsch** — immer prüfen und über „Bearbeiten" per Suche korrigieren.
- Artikelmerkmale sind Such-Dropdowns; fehlende Werte (z.B. „Mercedes-Benz" als Hersteller) über „Eigenen Wert hinzufügen" anlegen.
- **Preisvorschläge zulassen** aktivieren (macht der Nutzer bei seinen Listings immer).
- **„Angebot bewerben" IMMER aktivieren mit Anzeigentarif 2 %** (Vorgabe des Nutzers, 2026-07-11). Die Schnellauswahl bietet nur 8/10/12 % — über den Link „Eigenen Anzeigentarif auswählen" das Prozentfeld öffnen und `2` eintragen.
- **Rücknahme aktivieren**: Das Formular startet mit „Keine Rücknahme"! Unter „Details zur Lieferung" → „Rücknahme im Inland" einschalten (Standardwerte 14 Tage / Käufer zahlt stimmen dann).
- **Übersetzungsverhältnis** bei Differentialen: leer lassen (Vorgabe des Nutzers).
- **Foto-Upload** (Chrome-Erweiterung blockiert file_upload für lokale Ordner): Lokalen HTTP-Server mit CORS+PNA-Headern starten (Vorlage: `Vorlagen/cors_server.py`, Port 8748, Verzeichnis = Foto-Ordner), dann per javascript_tool im eBay-Tab die Dateien fetchen, in eine DataTransfer packen, dem `input[type=file]` zuweisen und ein `change`-Event dispatchen. Danach Server per `pkill -f cors_server.py` beenden. Ein simpler `python3 -m http.server` reicht NICHT (fehlende CORS-Header → „Failed to fetch").
- Speichern über den Button **„Speichern"** ganz unten (nicht „Artikel kostenlos einstellen" — das würde veröffentlichen!). Danach landet man in „Entwürfe verwalten" (`ebay.de/sh/lst/drafts`).
- Rücknahme-Einstellungen kommen aus den Account-Vorgaben (14 Tage Inland) — im Entwurf nicht anfassen.
- Chat-Anhänge liegen NICHT als Dateien auf der Festplatte: Für den Foto-Upload muss der Nutzer die Bilder in `Eingang/` ablegen (AirDrop). Ohne Fotos trotzdem den kompletten Entwurf anlegen und die Fotos nachreichen (Entwurf öffnen → „Vom Computer hochladen"-Feld per file_upload befüllen).

## Bei mehreren Produkten
Fotos zuerst nach Teil gruppieren (gleiche Teilenummer / gleiches Objekt), dann jedes Teil einzeln komplett durchziehen und am Ende einen Sammelbericht liefern.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
