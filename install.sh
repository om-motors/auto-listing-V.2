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

# --- 3. Autostart (launchd) --------------------------------------------------
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
echo "Fertig. Noch zwei Dinge, die nur du machen kannst:"
echo
echo "  1. API-Key eintragen in:  $PROJEKT/.env"
echo "     (Key holen auf https://console.anthropic.com)"
echo
echo "  2. Einmalig bei eBay einloggen:"
echo "     $PY -m autolister.login"
echo
echo "Danach prüfen mit:  $PY -m autolister.doctor"
echo "Upload-Website:     http://$(hostname -s).local:$PORT"
