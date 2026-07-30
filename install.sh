#!/bin/bash
# Auto-Listing einrichten: Abhängigkeiten installieren und Autostart aktivieren.
#
#   ./install.sh
#
# Danach laufen Ordner-Überwachung und Upload-Website automatisch bei jedem
# Anmelden am Mac — Claude muss dafür nicht offen sein.
set -euo pipefail

PROJEKT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
PY="$PROJEKT/.venv/bin/python"

echo "==> Auto-Listing einrichten in $PROJEKT"

# --- 1. Python-Umgebung ------------------------------------------------------
if [ ! -x "$PY" ]; then
  echo "==> Erstelle Python-Umgebung (.venv)"
  python3 -m venv "$PROJEKT/.venv"
fi
echo "==> Installiere Python-Pakete"
"$PROJEKT/.venv/bin/pip" install --quiet --upgrade pip
"$PROJEKT/.venv/bin/pip" install --quiet -r "$PROJEKT/requirements.txt"

echo "==> Installiere Browser (Chromium)"
"$PROJEKT/.venv/bin/playwright" install chromium

# --- 2. .env anlegen ---------------------------------------------------------
if [ ! -f "$PROJEKT/.env" ]; then
  cp "$PROJEKT/.env.example" "$PROJEKT/.env"
  echo "==> .env angelegt — bitte ANTHROPIC_API_KEY eintragen!"
fi

mkdir -p "$PROJEKT/logs" "$PROJEKT/Eingang" "$PROJEKT/Erledigt" \
         "$PROJEKT/Berichte" "$PROJEKT/Fehler"

# --- 3. Datenschutzsperre von macOS prüfen -----------------------------------
# macOS schützt Schreibtisch, Dokumente und Downloads. Hintergrunddienste
# bekommen dort ohne "Festplattenvollzugriff" ein "Operation not permitted" —
# und zwar wortlos, ohne Nachfrage. Liegt das Projekt in einem solchen Ordner,
# muss das vor dem Autostart geklärt werden.
GESCHUETZT=0
case "$PROJEKT" in
  "$HOME"/Desktop/*|"$HOME"/Documents/*|"$HOME"/Downloads/*|\
  "$HOME"/Schreibtisch/*|"$HOME"/Dokumente/*) GESCHUETZT=1 ;;
esac

if [ "$GESCHUETZT" = "1" ]; then
  cat <<HINWEIS

--------------------------------------------------------------------------
ACHTUNG: Das Projekt liegt in einem von macOS geschützten Ordner:
  $PROJEKT

Hintergrunddienste dürfen dort nicht lesen. Der Autostart würde in einer
Absturzschleife enden. Zwei Wege — einer reicht:

  A) Projekt aus dem geschützten Ordner holen (empfohlen, keine Rechte nötig):

       mv "$PROJEKT" "\$HOME/Auto-Listing"
       cd "\$HOME/Auto-Listing" && ./install.sh

  B) Festplattenvollzugriff erteilen (Projekt bleibt liegen):

       Systemeinstellungen -> Datenschutz & Sicherheit ->
       Festplattenvollzugriff -> "+" -> diese Datei hinzufügen:
         $PY

       Einstellungen öffnen mit:
       open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"

Der Autostart wird jetzt trotzdem eingerichtet, läuft aber erst nach einem
dieser beiden Schritte. Prüfen mit:  $PY -m autolister.doctor
--------------------------------------------------------------------------

HINWEIS
fi

# --- 4. Autostart (launchd) --------------------------------------------------
mkdir -p "$AGENTS"

schreibe_plist() {
  local label="$1" modul="$2"
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-m</string>
    <string>$modul</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJEKT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <!-- Bremst Neustarts bei dauerhaftem Fehler. Ohne das schreibt ein
       abstürzender Dienst binnen Minuten hunderte Fehler ins Protokoll. -->
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$PROJEKT/logs/${label##*.}.log</string>
  <key>StandardErrorPath</key><string>$PROJEKT/logs/${label##*.}.log</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST
  launchctl unload "$AGENTS/$label.plist" 2>/dev/null || true
  launchctl load "$AGENTS/$label.plist"
  echo "==> Autostart aktiv: $label"
}

schreibe_plist "de.ommotors.autolisting.watcher" "autolister.watcher"
schreibe_plist "de.ommotors.autolisting.webapp" "autolister.webapp"

# --- 4. Abschluss ------------------------------------------------------------
PORT="$(grep -E '^AUTOLISTER_WEBAPP_PORT=' "$PROJEKT/.env" 2>/dev/null | cut -d= -f2)"
PORT="${PORT:-8790}"
echo
echo "Fertig. Auto-Listing läuft in der kostenlosen Betriebsart 'lokal':"
echo "die Teilenummer liest die in macOS eingebaute Texterkennung, Titel und"
echo "Preis werden aus den eBay-Vergleichsangeboten abgeleitet. Keine API,"
echo "keine laufenden Kosten."
echo
echo "Es fehlt nur noch EIN Schritt, den nur du machen kannst:"
echo
echo "  Einmalig bei eBay einloggen ('Angemeldet bleiben' anhaken):"
echo "     $PY -m autolister.login"
echo
echo "Danach prüfen mit:  $PY -m autolister.doctor"
echo "Upload-Website:     http://$(hostname -s).local:$PORT"
