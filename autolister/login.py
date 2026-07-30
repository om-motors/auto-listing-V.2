"""Einmalige Einrichtung: eBay-Login im Automations-Browser.

Öffnet ein sichtbares Browserfenster mit dem persistenten Profil. Dort
selbst bei eBay einloggen ("Angemeldet bleiben" anhaken!) und das Fenster
danach schließen. Der Login bleibt im Profil gespeichert.

Aufruf:  .venv/bin/python -m autolister.login
"""
from __future__ import annotations

from playwright.sync_api import sync_playwright

from . import config


def main() -> None:
    config.BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            str(config.BROWSER_PROFILE),
            headless=False,
            locale="de-DE",
            viewport={"width": 1440, "height": 950},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto("https://www.ebay.de/signin/")
        print("Bitte im Browserfenster bei eBay einloggen ('Angemeldet bleiben' anhaken).")
        print("Danach das Fenster einfach schließen.")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
    print("Fertig. Login ist im Profil gespeichert:", config.BROWSER_PROFILE)


if __name__ == "__main__":
    main()
