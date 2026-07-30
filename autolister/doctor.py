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


def _check_betriebsart() -> Tuple[str, str]:
    modus = config.aktiver_modus()
    if modus == "lokal":
        from . import ocr
        if not ocr.verfuegbar():
            return FAIL, ("Betriebsart 'lokal', aber die macOS-Texterkennung fehlt  ->  "
                          ".venv/bin/pip install pyobjc-framework-Vision "
                          "pyobjc-framework-Quartz")
        return OK, "Betriebsart 'lokal' — kostenlos, alles läuft auf diesem Mac"
    if modus == "api":
        if not config.ANTHROPIC_API_KEY:
            return FAIL, "Betriebsart 'api', aber ANTHROPIC_API_KEY fehlt in der .env"
        return WARN, ("Betriebsart 'api' — KOSTENPFLICHTIG pro Foto (Modell: %s). "
                      "Kostenlos wäre AUTOLISTER_MODUS=lokal" % config.VISION_MODEL)
    if modus == "cli":
        if not shutil.which(config.CLAUDE_CLI):
            return FAIL, ("Betriebsart 'cli', aber die claude-CLI wurde nicht gefunden  ->  "
                          "AUTOLISTER_MODUS=lokal in die .env schreiben")
        return OK, "Betriebsart 'cli' — läuft über das bestehende Claude-Abo"
    return FAIL, "Unbekannte Betriebsart %r (erlaubt: lokal, cli, api, auto)" % modus


def _check_texterkennung() -> Tuple[str, str]:
    from . import ocr
    if not ocr.verfuegbar():
        return WARN, ("macOS-Texterkennung nicht installiert (nur nötig für die "
                      "kostenlose Betriebsart)  ->  .venv/bin/pip install "
                      "pyobjc-framework-Vision pyobjc-framework-Quartz")
    return OK, "macOS-Texterkennung einsatzbereit (kostenlos, lokal)"


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


def _check_datenschutzsperre() -> Tuple[str, str]:
    """macOS sperrt Schreibtisch/Dokumente/Downloads für Hintergrunddienste.

    Das ist die häufigste Ursache dafür, dass der Autostart eingerichtet ist,
    aber nichts passiert: der Dienst startet, bekommt beim Lesen ein
    "Operation not permitted" und stürzt wortlos ab — in einer Schleife.
    """
    projekt = str(config.PROJECT_DIR)
    heim = str(Path.home())
    geschuetzt = any(
        projekt.startswith("%s/%s/" % (heim, ordner))
        for ordner in ("Desktop", "Documents", "Downloads",
                       "Schreibtisch", "Dokumente")
    )

    # Belegt das Protokoll, dass es tatsächlich klemmt?
    log = config.LOGS / "watcher.log"
    blockiert = False
    if log.exists():
        try:
            ende = log.read_text(errors="ignore")[-4000:]
            blockiert = "Operation not permitted" in ende or "PermissionError" in ende
        except Exception:
            pass

    if blockiert:
        return FAIL, (
            "macOS blockiert den Hintergrunddienst (Datenschutzsperre). Entweder\n"
            "                          das Projekt aus dem geschützten Ordner holen:\n"
            "                            mv '%s' ~/Auto-Listing && cd ~/Auto-Listing && ./install.sh\n"
            "                          oder Festplattenvollzugriff geben für:\n"
            "                            %s/.venv/bin/python" % (projekt, projekt))
    if geschuetzt:
        return WARN, (
            "Projekt liegt in einem geschützten Ordner (%s). Läuft im Terminal, "
            "aber der Autostart braucht Festplattenvollzugriff oder einen Umzug "
            "nach ~/Auto-Listing." % projekt.replace(heim, "~"))
    return OK, "Speicherort ist für Hintergrunddienste zugänglich"


def _check_autostart() -> Tuple[str, str]:
    agents = Path.home() / "Library" / "LaunchAgents"
    vorhanden = [
        n for n in ("de.ommotors.autolisting.watcher.plist",
                    "de.ommotors.autolisting.webapp.plist")
        if (agents / n).exists()
    ]
    if len(vorhanden) != 2:
        if vorhanden:
            return WARN, "Autostart nur teilweise eingerichtet  ->  ./install.sh"
        return WARN, "Kein Autostart eingerichtet  ->  ./install.sh"
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return WARN, "Autostart eingerichtet, Status nicht prüfbar"

    zeilen = [z for z in out.splitlines() if "de.ommotors.autolisting" in z]
    if len(zeilen) < 2:
        return WARN, "Autostart eingerichtet, aber nicht geladen  ->  ./install.sh"
    # Erste Spalte ist die PID; "-" heißt: läuft gerade nicht
    laufen = [z for z in zeilen if not z.split("\t")[0].strip() == "-"]
    if not laufen:
        return WARN, ("Dienste sind geladen, laufen aber nicht — meist die "
                      "Datenschutzsperre (siehe Zeile darüber)")
    return OK, "Autostart aktiv (%d von 2 Diensten laufen)" % len(laufen)


CHECKS = [
    ("Python-Pakete", _check_python_pakete),
    ("Browser", _check_browser),
    ("Betriebsart", _check_betriebsart),
    ("Texterkennung", _check_texterkennung),
    ("Ordner/Vorlagen", _check_ordner),
    ("eBay-Login", _check_ebay_login),
    ("Speicherort", _check_datenschutzsperre),
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
