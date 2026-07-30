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


def _step(warnings: List[str], name: str, fn, pruefen=None) -> bool:
    """Einen Formularschritt ausführen und das Ergebnis nachkontrollieren.

    `pruefen` ist eine Funktion, die True liefert, wenn der Wert wirklich im
    Formular steht. Ohne diese Kontrolle meldet ein Schritt Erfolg, sobald
    kein Fehler auftrat — im Echtlauf trugen so drei Schritte (Artikelmerkmale,
    Rücknahme, Anzeigentarif) gar nichts ein, und der Bericht behauptete
    trotzdem, es sei nichts mehr von Hand zu tun. Ein Bericht, der lügt, ist
    schlimmer als einer, der Arbeit auflistet.
    """
    try:
        fn()
    except Exception as exc:
        warnings.append("%s: %s" % (name, str(exc).splitlines()[0]))
        return False

    if pruefen is None:
        return True
    try:
        if pruefen():
            return True
        warnings.append("%s: kein Fehler, aber der Wert steht nicht im Formular" % name)
    except Exception as exc:
        warnings.append("%s: Kontrolle fehlgeschlagen (%s)" % (name, str(exc).splitlines()[0]))
    return False


def _feldwert(page, label: str) -> str:
    """Aktuellen Wert eines Artikelmerkmals auslesen."""
    return _merkmal_wert(page, label)


def _abschnitt_text(page, ueberschrift: str) -> str:
    """Sichtbaren Text eines Formularabschnitts holen (für Kontrollen)."""
    try:
        anker = page.get_by_text(re.compile(ueberschrift, re.I)).first
        if not anker.count():
            return ""
        block = anker.locator("xpath=ancestor::*[self::div or self::section][4]")
        return block.inner_text(timeout=4000) or ""
    except Exception:
        return ""


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


def _merkmal_knopf(page, label: str):
    """Den sichtbaren Aufklapp-Knopf einer Artikelmerkmal-Zeile finden.

    Aufbau einer Merkmalzeile (am echten Formular ermittelt):

        div.summary__attributes--section-container
          button.fake-link.tooltip__host          <- Beschriftung "Hersteller"
          button.se-expand-button__button          <- SICHTBAR, öffnet das Feld
            [aria-label="Hersteller"]              <- zeigt später den Wert
          input.textbox__control                   <- unsichtbar bis geöffnet
            [aria-label="Suchen oder eigene …"]

    Der frühere Versuch, direkt in das `input` zu schreiben, scheiterte daran,
    dass es bis zum Öffnen `display:none` hat — Playwright meldete stur
    "element is not visible".
    """
    # Präfix-Vergleich statt exakt: das Feld heißt "OE/OEM Referenznummer(n)",
    # ein exakter Vergleich auf "OE/OEM Referenznummer" ging daneben.
    knopf = page.locator(
        "button.se-expand-button__button[aria-label^='%s']" % label).first
    if knopf.count():
        return knopf
    # Ist bereits ein Wert gesetzt, entfernt eBay das aria-label. Dann bleibt
    # nur der Weg über die Beschriftung links daneben.
    knopf = page.locator(
        "xpath=//div[contains(@class,'summary__attributes--label')]"
        "[starts-with(normalize-space(.),'%s')]/following::button"
        "[contains(@class,'se-expand-button__button')][1]" % label).first
    return knopf if knopf.count() else None


def _merkmal_setzen(page, label: str, wert: str) -> str:
    """Ein Artikelmerkmal ausfüllen.

    Der Ablauf ist am echten Formular verifiziert:
      1. Aufklapp-Knopf klicken — erst dann existiert das Eingabefeld sichtbar
      2. mit **echten Tastenanschlägen** tippen; ein `fill()` setzt den Wert
         zwar in das Feld, löst aber die Vorschlagslogik nicht aus und wird
         beim Schließen verworfen
      3. Enter — damit übernimmt eBay auch frei eingegebene Werte
         ("Suchen oder eigene Angaben machen")
      4. Escape schließt die Auswahl, ohne den Wert zu verwerfen
    """
    knopf = _merkmal_knopf(page, label)
    if knopf is None:
        raise RuntimeError("Aufklapp-Knopf nicht gefunden")
    knopf.scroll_into_view_if_needed(timeout=8000)
    _dialoge_schliessen(page)
    knopf.click(timeout=8000)
    page.wait_for_timeout(1500)

    # Das Eingabefeld MUSS aus derselben Zeile stammen. Ein globales `.first`
    # traf nach dem ersten gesetzten Merkmal weiterhin dessen Feld, weshalb
    # alle folgenden Merkmale leer blieben.
    #
    # Wichtig: auf das Einblenden **warten**, nicht die Sichtbarkeit sofort
    # abfragen. eBay braucht nach dem Klick einen Moment; eine sofortige
    # Prüfung meldet "unsichtbar" und schickt uns in die falsche Rückfallebene.
    # Das Feld liegt im direkten Elternelement des Knopfes (div.fake-menu-button).
    zeile = knopf.locator("xpath=..")
    feld = zeile.locator("input.textbox__control").first
    try:
        feld.wait_for(state="visible", timeout=8000)
    except Exception:
        feld = page.locator("input.textbox__control:visible").first
        feld.wait_for(state="visible", timeout=6000)
    feld.click(timeout=6000)
    feld.press_sequentially(str(wert), delay=110)
    page.wait_for_timeout(1800)

    # Exakten Vorschlag anklicken, falls einer angeboten wird
    vorschlag = page.get_by_role("option", name=str(wert), exact=True).first
    if vorschlag.count() and vorschlag.is_visible():
        vorschlag.click(timeout=4000)
    else:
        feld.press("Enter")
    page.wait_for_timeout(1800)
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)

    # Wert zurücklesen — mit Rückfallebenen, weil beides schiefgehen kann:
    # sobald ein Merkmal gefüllt ist, entfernt eBay dessen aria-label (das
    # erneute Suchen findet nichts), und beim Pflichtfeld "Hersteller" baut
    # eBay die Merkmalliste neu auf (der alte Elementzeiger wird ungültig).
    page.wait_for_timeout(1200)
    for versuch in range(3):
        try:
            text = (knopf.inner_text(timeout=3000) or "").strip()
            if text:
                return text
        except Exception:
            pass
        # Knopf neu suchen — nach dem Neuaufbau ist der alte Zeiger tot
        neu = _merkmal_knopf(page, label)
        if neu is not None:
            try:
                text = (neu.inner_text(timeout=3000) or "").strip()
                if text:
                    return text
                knopf = neu
            except Exception:
                pass
        page.wait_for_timeout(900)

    # Letzte Ebene: zeigt irgendein Merkmalknopf diesen Wert an?
    try:
        treffer = page.locator("button.se-expand-button__button",
                               has_text=re.compile(re.escape(str(wert)), re.I))
        if treffer.count():
            return str(wert)
    except Exception:
        pass
    return ""


def _merkmal_wert(page, label: str) -> str:
    """Gesetzten Wert eines Merkmals ablesen — steht im Aufklapp-Knopf."""
    knopf = _merkmal_knopf(page, label)
    if knopf is None:
        return ""
    try:
        return (knopf.inner_text(timeout=3000) or "").strip()
    except Exception:
        return ""


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
        "OE/OEM Referenznummer": vision.get("teilenummer_kompakt"),  # heißt real "...(n)"
        "Produktart": listing.get("teilname"),
        # "Einbauposition" steht bewusst nicht hier — siehe
        # config.MERKMALE_AUSLASSEN (Vorgabe des Nutzers). Im Titel bleibt die
        # Position erhalten, nur als Artikelmerkmal wird sie nicht gesetzt.
    }
    gesetzte_werte: Dict[str, str] = {}
    for label, value in merkmale.items():
        if not value or label in config.MERKMALE_AUSLASSEN:
            continue

        def set_specific(label=label, value=value):
            gesetzte_werte[label] = _merkmal_setzen(page, label, value)

        def kontrolle(label=label, value=value):
            steht = gesetzte_werte.get(label) or _merkmal_wert(page, label)
            return bool(steht) and str(value)[:6].lower() in steht.lower()
        _step(warnings, "Merkmal '%s'" % label, set_specific, kontrolle)

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
        # Reihenfolge ist zwingend: erst den Schalter einschalten, sonst gibt
        # es weder die Schnellauswahl (8/10/12 %) noch den Knopf für den
        # eigenen Tarif — der ganze Block wird erst beim Einschalten gebaut.
        schalter = page.locator(
            "div.promoted-listing-simple input[role='switch']").first
        if not schalter.count():
            raise RuntimeError("Schalter 'Angebot bewerben' nicht gefunden")
        schalter.scroll_into_view_if_needed(timeout=8000)
        if not schalter.is_checked():
            schalter.click(timeout=8000)
            page.wait_for_timeout(2500)

        # Die Schnellauswahl kennt nur 8/10/12 %. Für 2 % braucht es das
        # Freitextfeld hinter "Eigenen Anzeigentarif auswählen".
        eigen = page.locator("button.custom-rate-button-switch").first
        if not eigen.count():
            eigen = page.get_by_role(
                "button", name=re.compile("Eigenen Anzeigentarif", re.I)).first
        eigen.scroll_into_view_if_needed(timeout=8000)
        eigen.click(timeout=8000)
        page.wait_for_timeout(1800)

        feld = page.locator(
            "input[aria-label*='Anzeigentarif' i], input[aria-label*='Prozent' i]").last
        if not feld.count():
            feld = page.locator(
                "div.promoted-listing-simple input[type='text']").last
        feld.click(timeout=6000)
        feld.fill("")
        feld.press_sequentially(config.ANZEIGENTARIF_PROZENT, delay=120)
        page.keyboard.press("Tab")
        page.wait_for_timeout(1500)

    def bewerben_kontrolle():
        # Der eingetragene Tarif steht als Feldwert, nicht als Text im Abschnitt
        try:
            feld = page.locator(
                "input[aria-label*='Anzeigentarif' i], "
                "div.promoted-listing-simple input[type='text']").last
            if feld.count():
                wert = (feld.input_value(timeout=3000) or "").strip().rstrip("%").strip()
                if wert == config.ANZEIGENTARIF_PROZENT:
                    return True
        except Exception:
            pass
        text = _abschnitt_text(page, r"Heben Sie Ihre Angebote hervor")
        return config.ANZEIGENTARIF_PROZENT + " %" in text or \
            config.ANZEIGENTARIF_PROZENT + "%" in text
    _step(warnings, "Angebot bewerben (%s%%)" % config.ANZEIGENTARIF_PROZENT,
          promote_2_percent, bewerben_kontrolle)

    # --- Rücknahme im Inland aktivieren (Formular startet mit 'Keine Rücknahme') ---
    def enable_returns():
        # "Details zur Lieferung" ist eine anklickbare Karte, kein
        # Bearbeiten-Knopf: button.se-field-card__body mit dem Text
        # "Standort: ... Keine Rücknahme". Ein Klick öffnet den Dialog, in dem
        # die Rücknahme-Einstellungen liegen.
        karte = page.locator("button.se-field-card__body", has_text="Standort").first
        if not karte.count():
            karte = page.locator("button.se-field-card__body",
                                 has_text=re.compile("Rücknahme")).first
        if not karte.count():
            raise RuntimeError("Karte 'Details zur Lieferung' nicht gefunden")
        karte.scroll_into_view_if_needed(timeout=8000)
        _dialoge_schliessen(page)
        karte.click(timeout=8000)
        page.wait_for_timeout(3000)

        # 1) Inlandsrücknahme einschalten (Schalter mit Label im Dialog)
        beschriftung = page.locator(
            "label.field__label", has_text=re.compile(r"Rücknahme im Inland")).first
        if not beschriftung.count():
            beschriftung = page.get_by_text(
                re.compile(r"Rücknahme im Inland", re.I)).first
        beschriftung.scroll_into_view_if_needed(timeout=6000)
        beschriftung.click(timeout=8000)
        page.wait_for_timeout(2000)

        # 2) Frist auf 14 Tage (Vorgabe des Nutzers)
        frist = "%d Tage" % config.RUECKNAHME_TAGE
        auswahl = page.get_by_text(re.compile(r"^%s$" % re.escape(frist))).first
        if auswahl.count() and auswahl.is_visible():
            auswahl.click(timeout=5000)
        else:
            liste = page.locator("select").filter(
                has_text=re.compile("Tage")).first
            if liste.count():
                liste.select_option(label=frist)
        page.wait_for_timeout(800)

        # 3) Rückversand zahlt der Käufer
        if config.RUECKVERSAND_ZAHLT_KAEUFER:
            kaeufer = page.get_by_text(
                re.compile(r"Käufer (zahlt|trägt).*(Rückversand|Rücksendung)", re.I)).first
            if kaeufer.count() and kaeufer.is_visible():
                kaeufer.click(timeout=5000)
                page.wait_for_timeout(600)

        fertig = page.locator("button.btn--primary", has_text=re.compile(
            r"^\s*(Fertig|Übernehmen|Speichern|OK)\s*$", re.I)).first
        if not fertig.count():
            fertig = page.get_by_role(
                "button", name=re.compile(r"^(Fertig|Übernehmen|Speichern|OK)$", re.I)).first
        if fertig.count() and fertig.is_visible():
            _safe_click(page, fertig, warnings, "Rücknahme übernehmen")
            page.wait_for_timeout(2500)

    def ruecknahme_kontrolle():
        # Die Karte "Details zur Lieferung" zeigt den Stand im Klartext.
        karte = page.locator("button.se-field-card__body", has_text="Standort").first
        try:
            text = karte.inner_text(timeout=4000) if karte.count() else ""
        except Exception:
            text = ""
        if not text:
            text = _abschnitt_text(page, r"Details zur Lieferung")
        # Solange dort "Keine Rücknahme" steht, hat der Schritt nichts bewirkt
        return bool(text) and "Keine Rücknahme" not in text
    _step(warnings, "Rücknahme aktivieren", enable_returns, ruecknahme_kontrolle)

    # --- Versandkosten (DHL-Stufe) ---
    def set_shipping():
        # eBay bietet Versanddienste als Kacheln mit festen Preisen an. Für
        # einen eigenen Betrag (60 € Spedition) führt der Weg über
        # "Versandkosten bearbeiten" unter "Wer zahlt?".
        preis = ("%.2f" % listing.get("versandpreis", 0)).replace(".", ",")
        knopf = page.get_by_role(
            "button", name=re.compile("Versandkosten bearbeiten", re.I)).first
        if not knopf.count():
            knopf = page.get_by_text(
                re.compile("Versandkosten bearbeiten", re.I)).first
        if not knopf.count():
            raise RuntimeError("'Versandkosten bearbeiten' nicht gefunden")
        knopf.scroll_into_view_if_needed(timeout=8000)
        _dialoge_schliessen(page)
        knopf.click(timeout=8000)
        page.wait_for_timeout(3000)

        # Im Dialog das erste sichtbare Betragsfeld füllen
        feld = page.locator(
            "input[aria-label*='Versandkosten' i], input[aria-label*='Kosten' i], "
            "input[aria-label*='Betrag' i]").first
        if not feld.count():
            feld = page.locator("[role='dialog'] input[type='text']").first
        if not feld.count():
            raise RuntimeError("Betragsfeld im Versanddialog nicht gefunden")
        feld.click(timeout=6000)
        feld.fill("")
        feld.press_sequentially(preis, delay=110)
        page.wait_for_timeout(1000)

        fertig = page.locator("button.btn--primary", has_text=re.compile(
            r"^\s*(Fertig|Übernehmen|Speichern|OK)\s*$", re.I)).first
        if not fertig.count():
            fertig = page.get_by_role(
                "button", name=re.compile(r"^(Fertig|Übernehmen|Speichern|OK)$", re.I)).first
        if fertig.count() and fertig.is_visible():
            _safe_click(page, fertig, warnings, "Versanddialog schließen")
            page.wait_for_timeout(2500)

    def versand_kontrolle():
        erwartet = ("%.2f" % listing.get("versandpreis", 0)).replace(".", ",")
        return erwartet in _abschnitt_text(page, r"Wer zahlt")
    if not _step(warnings, "Versandkosten", set_shipping, versand_kontrolle):
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
