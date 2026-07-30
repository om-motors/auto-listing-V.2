"""Zentrale Konfiguration für die Auto-Listing-Pipeline.

Werte kommen aus der .env im Projektordner (siehe .env.example).
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

EINGANG = PROJECT_DIR / "Eingang"
ERLEDIGT = PROJECT_DIR / "Erledigt"
FEHLER = PROJECT_DIR / "Fehler"
BERICHTE = PROJECT_DIR / "Berichte"
VORLAGEN = PROJECT_DIR / "Vorlagen"
LOGS = PROJECT_DIR / "logs"
ARBEIT = PROJECT_DIR / ".arbeit"  # temporäre Bildkopien während der Analyse

# Persistentes Browser-Profil (hier bleibt der eBay-Login gespeichert)
BROWSER_PROFILE = Path(os.environ.get(
    "AUTOLISTER_BROWSER_PROFILE",
    str(Path.home() / ".autolisting" / "browser-profile"),
))

# Anthropic API. Wenn kein Key gesetzt ist, wird als Fallback die
# Claude-Code-CLI (`claude -p`, nutzt das bestehende Abo) verwendet.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
VISION_MODEL = os.environ.get("AUTOLISTER_VISION_MODEL", "claude-opus-4-8")
TEXT_MODEL = os.environ.get("AUTOLISTER_TEXT_MODEL", "claude-opus-4-8")
CLAUDE_CLI = os.environ.get("AUTOLISTER_CLAUDE_CLI", "claude")

# Bildaufbereitung. 1568 px lange Kante = ~1600 Tokens pro Bild und sehr
# schneller Upload. Reicht die Auflösung zum Ablesen der Teilenummer nicht,
# wiederholt vision.py die Analyse automatisch mit VISION_MAX_EDGE_RETRY
# (2576 px ist das Maximum, das die aktuellen Claude-Modelle auswerten).
VISION_MAX_EDGE = int(os.environ.get("AUTOLISTER_VISION_MAX_EDGE", "1568"))
VISION_MAX_EDGE_RETRY = int(os.environ.get("AUTOLISTER_VISION_MAX_EDGE_RETRY", "2576"))
VISION_JPEG_QUALITY = int(os.environ.get("AUTOLISTER_VISION_JPEG_QUALITY", "85"))
UPLOAD_JPEG_QUALITY = int(os.environ.get("AUTOLISTER_UPLOAD_JPEG_QUALITY", "92"))

# Wie viele Produkte gleichzeitig analysiert werden (nur die Bildanalyse
# läuft parallel; die Browser-Arbeit bleibt bewusst seriell).
VISION_PARALLEL = int(os.environ.get("AUTOLISTER_VISION_PARALLEL", "4"))

# Browser sichtbar laufen lassen (empfohlen: eBay blockt Headless eher)
HEADLESS = os.environ.get("AUTOLISTER_HEADLESS", "0") == "1"

# Web-Upload-Server
WEBAPP_PORT = int(os.environ.get("AUTOLISTER_WEBAPP_PORT", "8790"))

# Watcher: so viele Sekunden muss Ruhe im Eingang sein, bevor verarbeitet wird
SETTLE_SECONDS = int(os.environ.get("AUTOLISTER_SETTLE_SECONDS", "25"))

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}

# DHL-Versandstufen (Käufer zahlt)
VERSAND_STUFEN = [
    ("Standard", 7.69, "Halter, Sensoren, Kleinteile, Zierleisten"),
    ("Mittel", 23.99, "Scheinwerfer, Spiegel, größere Verkleidungen"),
    ("Groß", 79.90, "Stoßstangen, Türverkleidungen"),
    ("Spedition", 99.90, "Türen, Hauben, Kotflügel, Sitze"),
]


def ensure_dirs() -> None:
    for d in (EINGANG, ERLEDIGT, FEHLER, BERICHTE, LOGS, ARBEIT):
        d.mkdir(parents=True, exist_ok=True)
