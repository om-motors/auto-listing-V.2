"""Schritt 4: eBay-Entwurf per Browser-Automation anlegen (Playwright).

Nutzt ein persistentes Browser-Profil, in dem der Nutzer einmalig bei eBay
eingeloggt ist (python -m autolister.login).

SICHERHEIT: Es wird ausschließlich "Entwurf speichern"/"Speichern" geklickt.
Buttons, die veröffentlichen würden ("Artikel anbieten", "... einstellen",
"Verkaufen"), sind hart gesperrt.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, List, Optional  # noqa: F401

from playwright.sync_api import sync_playwright

from . import config

# Diese Button-Texte dürfen NIEMALS geklickt werden (würden veröffentlichen)
FORBIDDEN = re.compile(r"anbieten|einstellen|verkaufen|list it|sell it", re.IGNORECASE)


class NotLoggedIn(RuntimeError):
    pass


class CaptchaBlocked(RuntimeError):
    """eBay verlangt eine Sicherheitsabfrage.

    Die wird bewusst NICHT automatisch gelöst. Der Nutzer muss den Browser
    einmal selbst öffnen (python -m autolister.login), die Abfrage bestätigen
    und sich einloggen. Danach läuft die Automation wieder durch.
    """


class DraftError(RuntimeError):
    def __init__(self, message: str, screenshot: Optional[Path] = None):
        super().__init__(message)
        self.screenshot = screenshot


def _check_captcha(page) -> None:
    """Sicherheitsabfrage erkennen und sauber abbrechen."""
    url = page.url or ""
    if "captcha" in url.lower() or "splashui" in url.lower():
        raise CaptchaBlocked(
            "eBay zeigt eine Sicherheitsabfrage. Bitte einmal selbst öffnen und "
            "bestätigen:  .venv/bin/python -m autolister.login  — danach läuft "
            "die Automation wieder automatisch weiter."
        )


def open_browser(playwright):
    config.BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        str(config.BROWSER_PROFILE),
        headless=config.HEADLESS,
        locale="de-DE",
        viewport={"width": 1440, "height": 950},
        args=["--disable-blink-features=AutomationControlled"],
    )


def _safe_click(page, locator, warnings: List[str], step: str) -> bool:
    """Klick mit Publish-Schutz: Buttontext wird vorher geprüft."""
    try:
        text = (locator.inner_text(timeout=3000) or "").strip()
    except Exception:
        text = ""
    if FORBIDDEN.search(text):
        warnings.append("%s: Klick auf '%s' blockiert (würde veröffentlichen!)" % (step, text))
        return False
    try:
        locator.click(timeout=8000)
        return True
    except Exception as exc:
        warnings.append("%s: %s" % (step, str(exc).splitlines()[0]))
        return False


def _step(warnings: List[str], name: str, fn) -> bool:
    try:
        fn()
        return True
    except Exception as exc:
        warnings.append("%s: %s" % (name, str(exc).splitlines()[0]))
        return False


def _settle(page, timeout: int = 8000) -> None:
    """Auf Ruhe im Netzwerk warten statt auf eine feste Sekundenzahl.

    Spart pro Formularschritt typisch 1–3 Sekunden gegenüber festen Wartezeiten
    und ist auf langsamer Verbindung trotzdem zuverlässiger.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass  # networkidle wird bei Seiten mit Dauer-Polling nie erreicht


def check_logged_in(page) -> bool:
    """Prüft den Login. Wirft CaptchaBlocked, wenn eBay eine Abfrage zeigt."""
    page.goto("https://www.ebay.de/sh/lst/drafts", wait_until="domcontentloaded",
              timeout=60000)
    _settle(page)
    _check_captcha(page)
    url = page.url or ""
    return "signin" not in url.lower() and "/sh/lst" in url


def create_draft_on_page(page, listing: Dict, vision: Dict, description: str,
                         photos: List[Path], work_dir: Path,
                         dry_run: bool = False) -> Dict:
    """Entwurf auf einer bereits geöffneten (eingeloggten) Page anlegen.

    `dry_run=True` füllt das Formular vollständig aus, klickt aber nicht auf
    "Speichern" — gedacht für den ersten überwachten Testlauf.
    """
    warnings: List[str] = []
    page.set_default_timeout(15000)
    try:
        if not check_logged_in(page):
            raise NotLoggedIn(
                "Nicht bei eBay eingeloggt. Bitte einmalig ausführen: "
                ".venv/bin/python -m autolister.login"
            )
        result = _fill_form(page, listing, vision, description, photos, warnings,
                            work_dir, dry_run)
        result["warnings"] = warnings
        return result
    except (NotLoggedIn, CaptchaBlocked, DraftError):
        raise
    except Exception as exc:
        shot = work_dir / "fehler_screenshot.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
        except Exception:
            shot = None
        raise DraftError(str(exc), screenshot=shot)


def create_draft(listing: Dict, vision: Dict, description: str,
                 photos: List[Path], work_dir: Path, dry_run: bool = False) -> Dict:
    """Kompletten Entwurf anlegen (eigener Browser). Gibt {draft_url, ...} zurück."""
    with sync_playwright() as p:
        browser = open_browser(p)
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            return create_draft_on_page(page, listing, vision, description, photos,
                                        work_dir, dry_run)
        finally:
            browser.close()


def _fill_form(page, listing, vision, description, photos, warnings, work_dir,
               dry_run: bool = False) -> Dict:
    titel = listing["titel"]

    # --- Einstieg: Titel eingeben, Formular öffnen (legt den Entwurf an) ---
    page.goto("https://www.ebay.de/sl/prelist/suggest", wait_until="domcontentloaded",
              timeout=60000)
    _settle(page)
    _check_captcha(page)

    box = page.locator("input[type='text'], input[type='search'], textarea").first
    box.fill(titel)
    page.keyboard.press("Enter")
    _settle(page)

    # Ggf. "Weiter" / "Ohne Übereinstimmung fortfahren"
    for label in ("Weiter", "Ohne Übereinstimmung fortfahren", "Weiter zum Angebot"):
        btn = page.get_by_role("button", name=label, exact=False)
        if btn.count() > 0 and btn.first.is_visible():
            _safe_click(page, btn.first, warnings, "Einstieg '%s'" % label)
            _settle(page)

    # Warten bis das Verkaufsformular (mit draftId) geladen ist
    deadline = time.time() + 30
    while time.time() < deadline:
        _check_captcha(page)
        if "draftId" in page.url or "/lstng" in page.url:
            break
        page.wait_for_timeout(500)
    if "draftId" not in page.url and "/lstng" not in page.url:
        raise DraftError("Verkaufsformular wurde nicht erreicht (URL: %s)" % page.url)
    draft_url = page.url

    # --- Fotos hochladen (direkt ins File-Input, kein CORS-Server nötig) ---
    def upload_photos():
        file_input = page.locator("input[type='file']").first
        file_input.set_input_files([str(p) for p in photos])
        # auf die Vorschaubilder warten statt pauschal pro Foto zu schlafen
        try:
            page.wait_for_function(
                "n => document.querySelectorAll('img[src*=\"ebayimg\"],"
                " [class*=\"uploader\"] img').length >= n",
                arg=len(photos), timeout=90000)
        except Exception:
            _settle(page, 20000)
    if photos:
        _step(warnings, "Foto-Upload", upload_photos)

    # --- Titel sicherstellen ---
    def ensure_title():
        field = page.locator(
            "input[name='title'], input[aria-label*='Titel' i]"
        ).first
        if field.count() and field.input_value() != titel:
            field.fill(titel)
    _step(warnings, "Titel", ensure_title)

    # --- Zustand: Gebraucht ---
    def set_condition():
        el = page.get_by_text("Gebraucht", exact=True).first
        el.click(timeout=5000)
    _step(warnings, "Zustand 'Gebraucht'", set_condition)

    # --- Beschreibung ---
    def set_description():
        frame = None
        for f in page.frames:
            if "rte" in (f.name or "").lower() or "beschreibung" in (f.title() or "").lower():
                frame = f
                break
        if frame:
            body = frame.locator("body")
            body.click()
            body.fill(description)
            return
        area = page.locator(
            "textarea[name*='description' i], div[contenteditable='true']"
        ).first
        area.click()
        area.fill(description)
    _step(warnings, "Beschreibung", set_description)

    # --- Artikelmerkmale (Suchdropdowns, best effort) ---
    merkmale = {
        "Hersteller": vision.get("hersteller"),
        "Herstellernummer": vision.get("teilenummer_kompakt"),
        "OE/OEM Referenznummer(n)": vision.get("teilenummer_kompakt"),
        "Einbauposition": listing.get("einbauposition"),
        "Herstellungsland und -region": vision.get("ursprungsland"),
    }
    for label, value in merkmale.items():
        if not value:
            continue
        def set_specific(label=label, value=value):
            field = page.get_by_label(label, exact=False).first
            field.click()
            field.fill(str(value))
            page.wait_for_timeout(1200)
            # Vorschlag übernehmen oder eigenen Wert anlegen
            option = page.get_by_text(str(value), exact=True).first
            if option.count() and option.is_visible():
                option.click()
            else:
                eigen = page.get_by_text("hinzufügen", exact=False).first
                if eigen.count() and eigen.is_visible():
                    eigen.click()
                else:
                    page.keyboard.press("Enter")
        _step(warnings, "Merkmal '%s'" % label, set_specific)

    # --- Preis + Sofort-Kaufen + Preisvorschläge ---
    def set_price():
        preis = listing.get("preis")
        if preis is None:
            raise RuntimeError("kein Preis ermittelt — im Entwurf manuell setzen")
        field = page.locator(
            "input[name='price'], input[aria-label*='Preis' i]"
        ).first
        field.fill(("%.2f" % preis).replace(".", ","))
    _step(warnings, "Preis", set_price)

    def enable_best_offer():
        toggle = page.get_by_text("Preisvorschlag", exact=False).first
        row = toggle.locator("xpath=ancestor::*[self::div or self::section][1]")
        switch = row.locator("input[type='checkbox'], [role='switch']").first
        if switch.count() and not switch.is_checked():
            switch.click()
    _step(warnings, "Preisvorschläge zulassen", enable_best_offer)

    # --- Angebot bewerben: eigener Anzeigentarif 2 % ---
    def promote_2_percent():
        section = page.get_by_text("bewerben", exact=False).first
        section.scroll_into_view_if_needed()
        row = section.locator("xpath=ancestor::*[self::div or self::section][2]")
        switch = row.locator("input[type='checkbox'], [role='switch']").first
        if switch.count() and not switch.is_checked():
            switch.click()
            page.wait_for_timeout(1000)
        eigen = page.get_by_text("Eigenen Anzeigentarif", exact=False).first
        eigen.click()
        page.wait_for_timeout(800)
        rate = page.locator(
            "input[type='number'], input[inputmode='decimal'], input[aria-label*='tarif' i]"
        ).last
        rate.fill("2")
    _step(warnings, "Angebot bewerben (2%)", promote_2_percent)

    # --- Rücknahme im Inland aktivieren (Formular startet mit 'Keine Rücknahme') ---
    def enable_returns():
        section = page.get_by_text("Rücknahme", exact=False).first
        section.scroll_into_view_if_needed()
        row = section.locator("xpath=ancestor::*[self::div or self::section][2]")
        switch = row.locator("input[type='checkbox'], [role='switch']").first
        if switch.count() and not switch.is_checked():
            switch.click()
    _step(warnings, "Rücknahme aktivieren", enable_returns)

    # --- Versandkosten (DHL-Stufe) ---
    def set_shipping():
        preis = listing.get("versandpreis")
        field = page.locator(
            "input[aria-label*='Versand' i], input[name*='shipping' i]"
        ).first
        field.fill(("%.2f" % preis).replace(".", ","))
    _step(warnings, "Versandkosten", set_shipping)

    # --- Screenshot vor dem Speichern (für den Bericht) ---
    shot = work_dir / "entwurf_vorschau.png"
    _step(warnings, "Screenshot", lambda: page.screenshot(path=str(shot), full_page=True))

    if dry_run:
        warnings.append("TROCKENLAUF: Formular ausgefüllt, aber NICHT gespeichert.")
        return {"draft_url": draft_url, "screenshot": str(shot) if shot.exists() else None}

    # --- SPEICHERN (niemals veröffentlichen!) ---
    saved = False
    save_btn = page.get_by_role("button", name=re.compile(r"^(Entwurf speichern|Speichern)$"))
    for i in range(save_btn.count()):
        btn = save_btn.nth(i)
        if btn.is_visible() and _safe_click(page, btn, warnings, "Speichern"):
            saved = True
            break
    if not saved:
        # Fallback: letzter sichtbarer Button namens "Speichern" irgendwo
        btn = page.locator("button", has_text="Speichern").last
        if btn.count() and _safe_click(page, btn, warnings, "Speichern (Fallback)"):
            saved = True
    if not saved:
        raise DraftError("Speichern-Button nicht gefunden — Entwurf existiert aber "
                         "vermutlich schon unter ebay.de/sh/lst/drafts (Auto-Save).")
    page.wait_for_timeout(4000)

    return {"draft_url": draft_url, "screenshot": str(shot) if shot.exists() else None}
