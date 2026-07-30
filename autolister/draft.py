"""Schritt 4: eBay-Entwurf per Browser-Automation anlegen (Playwright).

Nutzt ein persistentes Browser-Profil, in dem der Nutzer einmalig bei eBay
eingeloggt ist (python -m autolister.login).

SICHERHEIT: Es wird ausschließlich "Entwurf speichern"/"Speichern" geklickt.
Buttons, die veröffentlichen würden ("Artikel anbieten", "... einstellen",
"Verkaufen"), sind hart gesperrt.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional  # noqa: F401

from playwright.sync_api import sync_playwright

from . import config

log = logging.getLogger("autolister")

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


# Dialoge, die eBay über ein frisch angelegtes Formular legt. Der Text in
# Klammern ist der Knopf, mit dem wir sie loswerden — immer die Variante, die
# nichts umstellt.
MODAL_KNOEPFE = (
    "Nein, bleiben",      # "Zum erweiterten Verkaufsformular wechseln?"
    "Später",
    "Nicht jetzt",
    "Abbrechen",
)


def _dialoge_schliessen(page) -> int:
    """Modale Dialoge und Hinweisfenster wegräumen.

    Zwei verschiedene Sorten Overlay stehen im Weg:

    * ein **modaler Dialog** ("Zum erweiterten Verkaufsformular wechseln"),
      der die ganze Seite blockiert. Er erscheint nur bei neu angelegten
      Entwürfen — beim Nachladen eines bestehenden Entwurfs nicht, weshalb er
      bei der Selektor-Untersuchung unsichtbar blieb und alle Formularschritte
      in Timeouts laufen ließ.
    * mehrere **Tipp-Fenster** ("Artikelkategorie überprüfen", "Tipp zu Feld
      Produktart"), die einzelne Felder verdecken.
    """
    geschlossen = 0
    for _ in range(4):  # nach dem Schließen tauchen teils weitere auf
        vorher = geschlossen

        # 1) Modaler Dialog: bewusst den Knopf nehmen, der nichts verändert
        for beschriftung in MODAL_KNOEPFE:
            knopf = page.get_by_role("button", name=beschriftung, exact=True).first
            try:
                if knopf.count() and knopf.is_visible():
                    knopf.click(timeout=3000)
                    geschlossen += 1
                    page.wait_for_timeout(600)
            except Exception:
                continue

        # 2) Schließen-Kreuze von Dialogen und Tipp-Fenstern
        knoepfe = page.locator(
            "button[aria-label*='schließen' i], button[aria-label*='Schließen'],"
            " button.lightbox-dialog__close, [role='dialog'] button[aria-label]")
        for i in range(min(knoepfe.count(), 12)):
            try:
                knopf = knoepfe.nth(i)
                if knopf.is_visible():
                    knopf.click(timeout=2000)
                    geschlossen += 1
            except Exception:
                continue

        page.wait_for_timeout(500)
        if geschlossen == vorher:
            break
    return geschlossen


# Alter Name, wird an mehreren Stellen aufgerufen
_tooltips_schliessen = _dialoge_schliessen


def _formular_bereit(page, warnings: List[str]) -> bool:
    """Warten, bis das Verkaufsformular vollständig aufgebaut ist.

    eBay rendert das lange Formular abschnittsweise nach. Wer sofort losklickt,
    findet die halbe Seite nicht — im ersten Echtlauf scheiterten dadurch acht
    Schritte an Elementen, die nachweislich existieren, nur eben noch nicht.

    Deshalb: auf einen verlässlichen Anker warten (den Speichern-Knopf ganz
    unten) und einmal durch die Seite scrollen, damit alle Abschnitte
    tatsächlich gebaut werden.
    """
    try:
        page.wait_for_selector(
            "button:has-text('Speichern'), input[aria-label='Artikelpreis']",
            timeout=60000)
    except Exception:
        warnings.append("Formular war nach 60 s nicht vollständig geladen")
        return False

    # Einmal langsam durchscrollen: erzwingt das Nachladen der unteren Blöcke
    try:
        page.evaluate("""async () => {
          const schritt = window.innerHeight * 0.8;
          for (let y = 0; y < document.body.scrollHeight; y += schritt) {
            window.scrollTo(0, y);
            await new Promise(r => setTimeout(r, 250));
          }
          window.scrollTo(0, 0);
          await new Promise(r => setTimeout(r, 400));
        }""")
    except Exception:
        pass
    _settle(page, 10000)
    return True


def _merkmal_feld(page, label: str):
    """Eingabefeld einer Artikelmerkmal-Zeile über seine Beschriftung finden.

    Alle Merkmalfelder tragen dieselbe Beschriftung ("Suchen oder eigene
    Angaben machen"), unterscheidbar sind sie nur über den Text links daneben.
    Der steht nicht in einem <label>, sondern als Knopf mit Tooltip in
    `div.summary__attributes--label` — das Feld selbst folgt im DOM direkt
    darauf.
    """
    kandidaten = (
        "xpath=//div[contains(@class,'summary__attributes--label')]"
        "[normalize-space(.)='%s']/following::input[1]" % label,
        "xpath=//div[contains(@class,'summary__attributes--label')]"
        "[starts-with(normalize-space(.),'%s')]/following::input[1]" % label,
        "xpath=//*[normalize-space(text())='%s']/following::input[1]" % label,
    )
    for xpath in kandidaten:
        feld = page.locator(xpath).first
        try:
            if feld.count():
                return feld
        except Exception:
            continue
    return None


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

    # Der Wechsel-Dialog erscheint sofort nach dem Anlegen des Entwurfs und
    # blockiert alles Weitere — deshalb hier schon wegräumen, nicht erst
    # nach dem Foto-Upload.
    _settle(page, 8000)
    _dialoge_schliessen(page)

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

    # --- Warten, bis wirklich alles da ist, dann Hinweisfenster wegklicken ---
    _formular_bereit(page, warnings)
    anzahl = _tooltips_schliessen(page)
    log.info("Formular geladen, %d Hinweisfenster geschlossen", anzahl)

    # --- Zustand: Gebraucht ---
    def set_condition():
        knopf = page.locator("button.condition-recommendation-value",
                             has_text=re.compile(r"^\s*Gebraucht\s*$")).first
        if not knopf.count():
            knopf = page.get_by_role("button", name="Gebraucht", exact=True).first
        knopf.click(timeout=8000)
    _step(warnings, "Zustand 'Gebraucht'", set_condition)

    # --- Beschreibung (Rich-Text-Editor in einem iframe) ---
    def set_description():
        rahmen = page.frame_locator("iframe#se-rte-frame__summary")
        koerper = rahmen.locator("body")
        koerper.click(timeout=8000)
        page.keyboard.press("Meta+A")
        page.keyboard.press("Delete")
        koerper.type(description, delay=1)
    _step(warnings, "Beschreibung", set_description)

    # --- Artikelmerkmale ---
    merkmale = {
        "Hersteller": vision.get("hersteller"),
        "Herstellernummer": vision.get("teilenummer_kompakt"),
        "OE/OEM Referenznummer": vision.get("teilenummer_kompakt"),
        "Einbauposition": listing.get("einbauposition"),
        "Produktart": listing.get("teilname"),
    }
    for label, value in merkmale.items():
        if not value:
            continue

        def set_specific(label=label, value=value):
            feld = _merkmal_feld(page, label)
            if feld is None:
                raise RuntimeError("Feld nicht gefunden")
            # Erst in den sichtbaren Bereich holen: eBay hält Felder außerhalb
            # des Blickfelds inaktiv, ein Klick darauf läuft sonst in einen
            # Timeout, obwohl das Element im DOM längst existiert.
            feld.scroll_into_view_if_needed(timeout=8000)
            _dialoge_schliessen(page)
            feld.click(timeout=10000)
            feld.fill(str(value))
            page.wait_for_timeout(1200)
            # Vorschlagsliste: exakten Treffer nehmen, sonst eigenen Wert anlegen
            vorschlag = page.get_by_role("option", name=str(value), exact=True).first
            if vorschlag.count() and vorschlag.is_visible():
                vorschlag.click()
                return
            eigen = page.get_by_text(re.compile(r"(hinzufügen|verwenden)", re.I)).first
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
        kasten = page.locator("input[type='checkbox'][aria-label*='Preisvorschläge' i]").first
        if kasten.count() and not kasten.is_checked():
            kasten.check(timeout=8000)
    _step(warnings, "Preisvorschläge zulassen", enable_best_offer)

    # --- Angebot bewerben: eigener Anzeigentarif 2 % ---
    def promote_2_percent():
        # Der Schalter "Angebot bewerben" ist bereits an; nur der Tarif steht
        # auf den voreingestellten 10 %. Über diesen Knopf wird das Prozentfeld
        # überhaupt erst editierbar.
        # Abschnitt erst sichtbar machen — er wird verzögert gerendert
        abschnitt = page.get_by_text(re.compile(r"ANGEBOT BEWERBEN", re.I)).first
        if abschnitt.count():
            abschnitt.scroll_into_view_if_needed()
            page.wait_for_timeout(1500)
        eigen = page.get_by_text(re.compile(r"Anzeigentarif auswählen", re.I)).first
        if not eigen.count():
            eigen = page.get_by_role(
                "button", name=re.compile("Anzeigentarif", re.I)).first
        eigen.scroll_into_view_if_needed()
        _dialoge_schliessen(page)
        eigen.click(timeout=8000)
        page.wait_for_timeout(1200)
        feld = page.locator(
            "input[aria-label*='Anzeigentarif' i], input[aria-label*='Prozent' i]").last
        if not feld.count():
            feld = page.locator("input[inputmode='decimal'], input[type='number']").last
        feld.fill("2")
        page.keyboard.press("Tab")
    _step(warnings, "Angebot bewerben (2%)", promote_2_percent)

    # --- Rücknahme im Inland aktivieren (Formular startet mit 'Keine Rücknahme') ---
    def enable_returns():
        # Der Schalter liegt in einem Dialog ("@dialog" in seiner Element-ID),
        # der über "Bearbeiten" bei "Details zur Lieferung" geöffnet wird.
        # Den "Bearbeiten"-Knopf über den daneben stehenden Text finden:
        # "Standort: ... Keine Rücknahme" steht im selben Block.
        anker = page.get_by_text(re.compile(r"Keine Rücknahme|Rücknahme innerhalb")).first
        geoeffnet = False
        if anker.count():
            anker.scroll_into_view_if_needed(timeout=8000)
            page.wait_for_timeout(800)
            knopf = anker.locator(
                "xpath=ancestor::*[self::div or self::section][5]"
                "//button[contains(normalize-space(.),'Bearbeiten')]").first
            if knopf.count():
                knopf.click(timeout=8000)
                page.wait_for_timeout(2500)
                geoeffnet = True
        if not geoeffnet:
            raise RuntimeError("Abschnitt 'Details zur Lieferung' nicht gefunden")

        schalter = page.locator("label", has_text=re.compile(r"^\s*Rücknahme im Inland")).first
        if not schalter.count():
            schalter = page.get_by_text(re.compile(r"Rücknahme im Inland", re.I)).first
        schalter.click(timeout=8000)
        page.wait_for_timeout(1000)
        fertig = page.get_by_role(
            "button", name=re.compile(r"^(Fertig|Übernehmen|Speichern|OK)$", re.I)).first
        if fertig.count() and fertig.is_visible():
            _safe_click(page, fertig, warnings, "Rücknahme übernehmen")
            page.wait_for_timeout(1500)
    _step(warnings, "Rücknahme aktivieren", enable_returns)

    # --- Versandkosten (DHL-Stufe) ---
    def set_shipping():
        # Kein freies Preisfeld: eBay bietet Versanddienste als Kacheln an.
        # Wir öffnen den Dialog und tragen den Preis der gewählten Stufe ein.
        preis = listing.get("versandpreis")
        knopf = page.get_by_role(
            "button", name=re.compile("Versandkosten bearbeiten", re.I)).first
        if not knopf.count():
            raise RuntimeError("Knopf 'Versandkosten bearbeiten' nicht gefunden")
        knopf.scroll_into_view_if_needed()
        knopf.click(timeout=8000)
        page.wait_for_timeout(2000)
        feld = page.locator(
            "input[aria-label*='Versandkosten' i], input[aria-label*='Kosten' i]").first
        if not feld.count():
            raise RuntimeError("Preisfeld im Versanddialog nicht gefunden")
        feld.fill(("%.2f" % preis).replace(".", ","))
        fertig = page.get_by_role(
            "button", name=re.compile(r"(Fertig|Übernehmen|Speichern|OK)", re.I)).first
        if fertig.count() and fertig.is_visible():
            _safe_click(page, fertig, warnings, "Versanddialog schließen")
    if not _step(warnings, "Versandkosten", set_shipping):
        warnings.append("Versandstufe '%s' (%.2f €) bitte im Entwurf von Hand setzen."
                        % (listing.get("versandstufe"), listing.get("versandpreis", 0)))

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
