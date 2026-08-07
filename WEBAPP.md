# Web-App: Fotos vom Handy, Entwurf vom Mac

Vom Handy hochladen, von überall. Der Mac holt die Aufträge ab und legt die
eBay-Entwürfe an. Kein `Eingang/`-Ordner mehr, keine Upload-Seite im
Heimnetz.

```
   Handy  ──▶  Netlify (die Seite)  ──▶  Supabase (Fotos + Warteschlange)
                                              ▲
                                              │  holt Aufträge, meldet Ergebnis
                                        Mac (cloud_worker)
                                    ocr.py · research.py · draft.py
```

**Was der Mac weiterhin tun muss.** Die Texterkennung läuft über das
macOS-Vision-Framework — das ist der Grund, warum das Programm nichts kostet,
und es gibt es nur auf einem Mac. Der eBay-Entwurf entsteht in einem echten
Browser mit deiner eingeloggten Sitzung. Beides kann eine Netlify-Funktion
nicht leisten. Der Mac muss also **nicht ständig** laufen — aber irgendwann.
Was du hochlädst, wartet in der Warteschlange, bis er das nächste Mal wach ist.

**Kosten:** keine. Netlify und Supabase reichen im Gratis-Kontingent locker;
die Fotos werden nach der Verarbeitung gelöscht, damit das 1-GB-Limit nie
in die Nähe kommt.

---

## Einrichtung — einmalig, etwa 20 Minuten

### 1. Datenbank anlegen (Supabase)

Im Projekt → **SQL Editor** → **New query** → den Inhalt von
[`supabase/schema.sql`](supabase/schema.sql) hineinkopieren → **Run**.

Das legt an:
- die Tabelle `auftraege` (die Warteschlange)
- den Speicher-Bucket `fotos` — **nicht öffentlich**
- die Zugriffsregeln: nur angemeldete Nutzer dürfen hochladen und lesen

Das Skript darf mehrfach laufen, es macht nichts kaputt.

### 2. Deinen Benutzer anlegen

**Authentication → Users → Add user → Create new user.**
E-Mail und Passwort frei wählen, „Auto Confirm User" anhaken.

Das ist dein Login für die Web-App. Ohne ihn kann niemand hochladen, der die
Netlify-Adresse zufällig findet.

### 3. Schlüssel eintragen

Beide findest du unter **Project Settings → API Keys**.

**a) Der öffentliche Schlüssel** kommt in [`web/config.js`](web/config.js):

```js
window.AUTOLISTING_CONFIG = {
  SUPABASE_URL: "https://dsjfxlxqskhcezmsvafx.supabase.co",
  SUPABASE_ANON_KEY: "hier der anon- bzw. publishable-Schlüssel",
};
```

**b) Der geheime Schlüssel** kommt in die `.env` auf dem Mac — und **nur**
dorthin:

```
SUPABASE_URL=https://dsjfxlxqskhcezmsvafx.supabase.co
SUPABASE_SERVICE_KEY=hier der service_role- bzw. secret-Schlüssel
```

> **Der Unterschied ist wichtig.** Der `anon`-Schlüssel darf nur das, was die
> Zugriffsregeln erlauben — deshalb ist er in einer öffentlichen Seite
> unbedenklich. Der `service_role`-Schlüssel umgeht alle Regeln. Wer ihn in
> `config.js` schreibt, gibt jedem Besucher der Seite volle Rechte auf die
> Datenbank. Die `.env` steht in `.gitignore` und wird nie mitversioniert.

### 4. Seite veröffentlichen (Cloudflare Workers)

**Die Seite läuft auf Cloudflare:**
**https://auto-listing.o-guelues.workers.dev**

Neu veröffentlichen nach einer Änderung in `web/`:

```bash
cd /Users/ogulcang/Auto-Listing && npx wrangler deploy
```

Das ist alles. Kein Build, keine Installation — es gibt bewusst **kein
Worker-Skript**: Die Einstellungen in [`wrangler.jsonc`](wrangler.jsonc) sagen
Cloudflare nur, dass es die Dateien aus `web/` direkt ausliefern soll.

Die Sicherheitskopfzeilen stehen in [`web/_headers`](web/_headers) — im
Asset-Verzeichnis, nicht in der `wrangler.jsonc`. Sie erlauben der Seite,
ausschließlich mit Supabase zu sprechen. Sollte sie je manipuliert werden,
kann sie deine Fotos nirgendwo anders hinschicken.

Adresse aufs Handy, in Safari **Teilen → Zum Home-Bildschirm**. Dann startet
sie wie eine App.

> **Netlify ist abgeschaltet** (2026-08-07). Die `netlify.toml` wurde entfernt.
> Falls das Projekt im Netlify-Konto noch existiert: **Site configuration →
> Danger zone → Delete this project**. Solange es existiert, versucht es bei
> jedem Push zu bauen und scheitert — ohne Konfigurationsdatei findet es die
> `requirements.txt` der Pipeline und will `pyobjc-framework-Vision` auf Linux
> bauen. Schadet nichts, macht aber Lärm.

### 5. Selbsttest

```bash
.venv/bin/python -m autolister.cloud_worker --einmal
```

Meldet er `SUPABASE_URL und SUPABASE_SERVICE_KEY fehlen`, stimmt Schritt 3b
noch nicht. Sonst arbeitet er die Warteschlange einmal ab und beendet sich.

---

## Der tägliche Ablauf

1. **Fotos machen.** Mindestens eines von der eingestanzten Teilenummer —
   scharf und formatfüllend. Daran hängt alles Weitere.
2. **Seite auf dem Handy öffnen**, Fotos auswählen, optional die Teilenummer
   eintippen (wenn du sie kennst, wird sie direkt verwendet), **Hochladen**.
3. **Handy weglegen.** Der Auftrag steht in der Warteschlange.
4. **Der Mac arbeitet ihn ab**, sobald er läuft — Texterkennung,
   eBay-Recherche, Preis, Entwurf.
5. **Auf dem Handy nachsehen.** Die Liste aktualisiert sich von selbst und
   zeigt Titel, Preis, den Link zum Entwurf und was noch von Hand zu tun ist.
6. **Entwurf bei eBay prüfen und selbst einstellen.** Das Programm
   veröffentlicht nie — siehe `CLAUDE.md`.

---

## Den Mac umstellen

Bisher lief der Ordner-Watcher. Für den Cloud-Betrieb:

```bash
# alter Dienst aus
launchctl unload ~/Library/LaunchAgents/de.ommotors.autolisting.watcher.plist

# von Hand starten und zusehen
.venv/bin/python -m autolister.cloud_worker
```

Läuft das ein paar Tage sauber, kann der Arbeiter als Dienst dauerhaft laufen
(gleiche Bauart wie der Watcher, nur mit `autolister.cloud_worker` als Modul).

**Beide Wege funktionieren weiter nebeneinander.** Der `Eingang/`-Ordner ist
nicht abgeschafft — wer am Mac sitzt, kann Fotos weiter dorthin legen.

Zum Ausprobieren ohne Speichern:

```bash
.venv/bin/python -m autolister.cloud_worker --trockenlauf --einmal
```

---

## Wenn etwas klemmt

| Symptom | Ursache |
|---|---|
| Anmeldung schlägt fehl | Benutzer in Schritt 2 nicht angelegt oder nicht bestätigt |
| „Upload fehlgeschlagen" | `schema.sql` nicht gelaufen — der Bucket `fotos` fehlt |
| Aufträge bleiben auf `neu` | Der Arbeiter läuft nicht, oder Schritt 3b fehlt |
| Auftrag steht auf `fehler` | Grund steht direkt darunter in der Liste |
| Alles leer nach dem Deploy | `config.js` noch nicht ausgefüllt |

Ein Auftrag auf `laeuft`, der sich nicht mehr rührt, bedeutet: Der Mac ist
mittendrin abgestürzt oder eingeschlafen. In der Supabase-Tabelle den Status
von Hand zurück auf `neu` setzen, dann nimmt der Arbeiter ihn erneut.
