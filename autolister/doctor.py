"""Selbsttest: prüft alle Voraussetzungen und sagt, was noch fehlt.

    .venv/bin/python -m autolister.doctor
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

from . import config

OK, WARN, FAIL = "  OK  ", " HINW ", "FEHLER"


def _check_python_pakete() -> Tuple[str, str]:
    fehlend = []
    for modul, name in (("playwright", "playwright"), ("watchdog", "watchdog"),
                        ("flask", "flask"), ("anthropic", "anthropic"),
                        ("PIL", "pillow"), ("pillow_heif", "pillow-heif")):
        try:
            __import__(modul)
        except ImportError:
            fehlend.append(name)
    if fehlend:
        return FAIL, "Fehlende Pakete: %s  ->  .venv/bin/pip install %s" % (
            ", ".join(fehlend), " ".join(fehlend))
    return OK, "Alle Python-Pakete vorhanden"


def _check_browser() -> Tuple[str, str]:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
        if path and Path(path).exists():
            return OK, "Chromium installiert"
        return FAIL, "Chromium fehlt  ->  .venv/bin/playwright install chromium"
    except Exception as exc:
        return FAIL, "Playwright-Problem: %s" % exc


def _check_claude_zugang() -> Tuple[str, str]:
    if config.ANTHROPIC_API_KEY:
        return OK, "ANTHROPIC_API_KEY gesetzt (Modell: %s)" % config.VISION_MODEL
    if shutil.which(config.CLAUDE_CLI):
        return WARN, ("Kein API-Key — es wird die claude-CLI verwendet. "
                      "Das ist deutlich langsamer. Empfehlung: API-Key in .env eintragen.")
    return FAIL, ("Kein Claude-Zugang! Weder ANTHROPIC_API_KEY in der .env noch die "
                  "claude-CLI gefunden. Key holen auf console.anthropic.com, dann "
                  "in die Datei .env eintragen (Vorlage: .env.example).")


def _check_ebay_login() -> Tuple[str, str]:
    if not config.BROWSER_PROFILE.exists():
        return FAIL, ("Kein Browser-Profil. Einmalig einloggen:  "
                      ".venv/bin/python -m autolister.login")
    try:
        from playwright.sync_api import sync_playwright
        from . import draft
        with sync_playwright() as p:
            browser = draft.open_browser(p)
            page = browser.pages[0] if browser.pages else browser.new_page()
            try:
                eingeloggt = draft.check_logged_in(page)
            finally:
                browser.close()
        if eingeloggt:
            return OK, "Bei eBay eingeloggt"
        return FAIL, ("Nicht bei eBay eingeloggt  ->  "
                      ".venv/bin/python -m autolister.login")
    except Exception as exc:
        name = type(exc).__name__
        if name == "CaptchaBlocked":
            return FAIL, str(exc)
        return WARN, "Login-Prüfung nicht möglich: %s" % exc


def _check_ordner() -> Tuple[str, str]:
    config.ensure_dirs()
    vorlage = config.VORLAGEN / "beschreibung.md"
    if not vorlage.exists():
        return FAIL, "Vorlagen/beschreibung.md fehlt"
    return OK, "Ordner und Vorlagen vorhanden"


def _check_autostart() -> Tuple[str, str]:
    agents = Path.home() / "Library" / "LaunchAgents"
    vorhanden = [
        n for n in ("de.ommotors.autolisting.watcher.plist",
                    "de.ommotors.autolisting.webapp.plist")
        if (agents / n).exists()
    ]
    if len(vorhanden) == 2:
        try:
            out = subprocess.run(["launchctl", "list"], capture_output=True,
                                 text=True, timeout=10).stdout
            laufend = out.count("de.ommotors.autolisting")
            if laufend >= 2:
                return OK, "Autostart aktiv (Watcher + Upload-Website laufen)"
            return WARN, "Autostart eingerichtet, aber nicht geladen  ->  ./install.sh"
        except Exception:
            return WARN, "Autostart eingerichtet, Status nicht prüfbar"
    if vorhanden:
        return WARN, "Autostart nur teilweise eingerichtet  ->  ./install.sh"
    return WARN, "Kein Autostart eingerichtet  ->  ./install.sh"


CHECKS = [
    ("Python-Pakete", _check_python_pakete),
    ("Browser", _check_browser),
    ("Claude-Zugang", _check_claude_zugang),
    ("Ordner/Vorlagen", _check_ordner),
    ("eBay-Login", _check_ebay_login),
    ("Autostart", _check_autostart),
]


def main() -> int:
    print("Auto-Listing Selbsttest")
    print("=" * 72)
    ergebnisse: List[str] = []
    for name, fn in CHECKS:
        try:
            status, text = fn()
        except Exception as exc:  # noqa: BLE001
            status, text = FAIL, "Prüfung abgestürzt: %s" % exc
        ergebnisse.append(status)
        print("[%s] %-16s %s" % (status, name, text))
    print("=" * 72)
    if FAIL in ergebnisse:
        print("Es fehlt noch etwas — siehe FEHLER-Zeilen oben.")
        return 1
    if WARN in ergebnisse:
        print("Läuft, aber es gibt Verbesserungshinweise.")
        return 0
    print("Alles bereit. Fotos in den Eingang legen — der Rest passiert von selbst.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
