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

# Diese Button-Texte dürfen NIEMALS geklickt werden (würden veröffentlichen).
#
# Am echten Formular (2026-08-01) stehen unten genau drei Knöpfe:
#   button[aria-label="Zu genannten Gebühren einstellen"]  (btn--primary)  <- veröffentlicht
#   button[aria-label="Speichern"]                                          <- der gewollte
#   button[name="preview"]                                                  <- Vorschau
#
# Bis hierher hing alles an dem einen Wort "einstellen". Es fehlten
# "veröffentlichen", "aktivieren", "kostenpflichtig" und die englische
# Oberfläche — obwohl CLAUDE.md die Regel wörtlich mit "veröffentlichen"
# formuliert. Lieber einen Knopf zu viel sperren als einen zu wenig: eine
# blockierte Schaltfläche wird als Warnung gemeldet, ein versehentlich
# veröffentlichtes Inserat nicht.
FORBIDDEN = re.compile(
    r"anbieten|einstellen|verkauf|veröffentlich|veroeffentlich|aktivieren"
    r"|kostenpflichtig|gebühren|gebuehren"
    r"|list (it|item|for free)|sell it|publish|confirm and list|submit listing",
    re.IGNORECASE)


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


def _beschriftung(locator) -> Optional[str]:
    """Alle Beschriftungsquellen eines Knopfes einsammeln.

    `inner_text` allein reicht nicht: bei Icon-Knöpfen und `<input type=submit>`
    steht der Name ausschließlich im `aria-label`, `title` oder `value` — der
    sichtbare Text ist dann leer, und eine Sperre, die nur ihn liest, sieht den
    Namen nie. Am echten Formular trägt der Veröffentlichen-Knopf sein
    aria-label sogar wortgleich zum Text.

    Gibt **None** zurück, wenn sich gar nichts lesen ließ — dann darf nicht
    geklickt werden.
    """
    teile: List[str] = []
    gelesen = False
    try:
        teile.append(locator.inner_text(timeout=8000) or "")
        gelesen = True
    except Exception:
        pass
    for attribut in ("aria-label", "title", "value"):
        try:
            wert = locator.get_attribute(attribut, timeout=3000)
        except Exception:
            continue
        gelesen = True
        if wert:
            teile.append(wert)
    if not gelesen:
        return None
    return " ".join(t.strip() for t in teile if t and t.strip())


def _safe_click(page, locator, warnings: List[str], step: str) -> bool:
    """Klick mit Publish-Schutz: die Beschriftung wird vorher geprüft.

    **Fail-closed.** Lässt sich die Beschriftung nicht lesen oder ist sie leer,
    wird NICHT geklickt.

    Vorher stand hier `except Exception: text = ""`, und `FORBIDDEN.search("")`
    findet nie etwas — der Klick ging also durch. Die Sperre öffnete damit genau
    dann, wenn sie nichts wusste: bei Zeitüberschreitung (die Textprüfung hatte
    3 s, der Klick danach 8 s), bei abgelöstem Element und bei jedem Knopf ohne
    Textknoten.
    """
    text = _beschriftung(locator)
    if not text:
        warnings.append(
            "%s: Knopfbeschriftung nicht lesbar — Klick sicherheitshalber unterlassen"
            % step)
        return False
    if FORBIDDEN.search(text):
        warnings.append("%s: Klick auf '%s' blockiert (würde veröffentlichen!)"
                        % (step, text[:70]))
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


def _betrag(wert) -> str:
    """Zahl als deutscher Betrag: 29.9 -> '29,90'."""
    return ("%.2f" % float(wert)).replace(".", ",")


def _gleich(gelesen: str, erwartet: str) -> bool:
    """Werte vergleichen, ohne an Leerzeichen und Bindestrichen zu scheitern.

    Vorher verglich die Merkmal-Kontrolle nur die ersten sechs Zeichen — bei
    "A2053510005" also gerade "a20535", bei "Mercedes-Benz" nur "merced". Ein
    abgeschnittener oder verwechselter Wert bestand das mühelos.
    """
    putz = lambda s: re.sub(r"[\s\-./]", "", str(s or "")).lower()
    a, b = putz(gelesen), putz(erwartet)
    return bool(a) and bool(b) and b in a


def _formularwerte(page) -> Dict[str, str]:
    """Alle Formularfelder als {Name: Wert} auslesen — die ehrliche Kontrolle.

    Die Feldnamen stammen aus dem ersten Echtlauf am 2026-08-01 und sind weit
    stabiler als CSS-Klassen oder deutsche Beschriftungen:

        title                       Angebotstitel
        format                      "FixedPrice" | "ChineseAuction"
        Artikelpreis                z.B. "29,90"   (aria-label, kein name)
        quantity                    Stückzahl
        bestOfferEnabled            Preisvorschläge zulassen
        returnPolicy                Rücknahme im Inland
        returnDuration              "Days_14" | "Days_30" | "Days_60"
        returnShippingPayer         "Buyer" | "Seller"
        isInternationalShippingOn   internationaler Versand
        promotedListingSelection    Angebot bewerben
        customAdRateField           Anzeigentarif in Prozent, z.B. "2"
        handlingDuration            Bearbeitungszeit in Werktagen

    Schalter (`role=switch`) liefern `"true"`/`"false"`.

    **Fehlt ein Feld, fehlt der Schlüssel.** Keine Kontrolle darf daraus Erfolg
    ableiten — genau deshalb vergleicht `_felder_stimmen()` gegen einen
    Standardwert, der nie passt.
    """
    try:
        return page.evaluate("""() => {
          const out = {};
          for (const el of document.querySelectorAll('input, select, textarea')) {
            const name = el.getAttribute('name') || el.getAttribute('aria-label');
            if (!name) continue;
            const schalter = el.type === 'checkbox'
                             || el.getAttribute('role') === 'switch';
            out[name] = schalter ? String(el.checked) : String(el.value ?? '');
          }
          return out;
        }""") or {}
    except Exception:
        return {}


def _felder_stimmen(page, **erwartet: str) -> bool:
    """Prüfen, ob die genannten Formularfelder exakt diese Werte tragen.

    Ersetzt die früheren Textvergleiche im Abschnitt. Die waren zu weich: die
    Kontrolle des Anzeigentarifs prüfte `"2 %" in text` — und `"2 %"` steht in
    `"12 %"`, das die Schnellauswahl ohnehin anzeigt. Sie meldete also fast
    immer Erfolg, auch bei eBays voreingestellten 10 %.
    """
    # Bis zu dreimal lesen. eBay übernimmt Eingaben über React; unmittelbar
    # nach dem Tippen steht der Wert noch nicht zwingend im DOM. Im
    # Trockenlauf am 2026-08-01 meldete die Preiskontrolle deshalb einen
    # Fehlschlag, obwohl "29,90" sauber im Entwurf stand. Ein Bericht, der
    # Phantomarbeit auflistet, ist genauso schädlich wie einer, der schweigt —
    # beide bringen den Nutzer dazu, ihn nicht mehr ernst zu nehmen.
    #
    # Geduld macht die Kontrolle genauer, nicht durchlässiger: ein Wert, der
    # gar nicht gesetzt wurde, erscheint auch nach dem dritten Versuch nicht.
    for versuch in range(3):
        werte = _formularwerte(page)
        if all(werte.get(feld, "\x00").strip() == wert
               for feld, wert in erwartet.items()):
            return True
        if versuch < 2:
            page.wait_for_timeout(1200)
    return False


def _ist_noch_entwurf(page) -> bool:
    """Nach dem Speichern belegen, dass NICHTS veröffentlicht wurde.

    Ohne diese Prüfung sähe ein versehentlich eingestelltes Inserat im Bericht
    exakt aus wie ein gespeicherter Entwurf — der Bericht würde lügen, und zwar
    an der einzigen Stelle, an der das wirklich teuer ist.
    """
    try:
        if "draftId" in (page.url or ""):
            return True
        text = (page.locator("body").inner_text(timeout=8000) or "").lower()
    except Exception:
        return False
    if any(w in text for w in ("ist online", "wurde eingestellt", "artikel ansehen",
                               "angebot ansehen", "erfolgreich eingestellt")):
        return False
    return "entwurf" in text or "entwürfe" in text


# Dialoge, die eBay über ein frisch angelegtes Formular legt. Der Text in
# Klammern ist der Knopf, mit dem wir sie loswerden — immer die Variante, die
# nichts umstellt.
MODAL_KNOEPFE = (
    "Nein, bleiben",      # "Zum erweiterten Verkaufsformular wechseln?"
    "Später",
    "Nicht jetzt",
    "Abbrechen",
)


# Schließen-Knöpfe von Tipp-Fenstern und Dialogen — am echten Formular
# ermittelt (2026-08-01). Diese Liste ist bewusst eine **Erlaubnisliste**:
# vorher stand hier zusätzlich `[role='dialog'] button[aria-label]`, und das ist
# keine Schließen-Erkennung, sondern ein Treffer auf JEDEN beschrifteten Knopf
# in einem offenen Dialog — geklickt wurde roh, ohne Publish-Prüfung.
#
# Der zweite Fehler war die Suche nach dem Wort "schließen": der sichtbare
# Knopf des Tour-Tipps heißt `aria-label="Tipp ausblenden"` und enthält es
# gerade nicht. Deshalb blieben im ersten Echtlauf zwei Fenster stehen, eines
# davon quer über dem Preisfeld.
SCHLIESS_KNOEPFE = (
    "button.tourtip__close",           # "Tipp ausblenden"
    "button.infotip__close",           # "Tipp zu Feld <X> schließen"
    "button.lightbox-dialog__close",   # "Schließen", "Fenster mit Foto-Tipp schließen"
    "button[aria-label*='schließen' i]",
    "button[aria-label*='ausblenden' i]",
    "button[aria-label*='dismiss' i]",
)


def _dialoge_schliessen(page, warnings: Optional[List[str]] = None) -> int:
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
    notiz = warnings if warnings is not None else []
    geschlossen = 0
    for _ in range(4):  # nach dem Schließen tauchen teils weitere auf
        vorher = geschlossen

        # 1) Modaler Dialog: bewusst den Knopf nehmen, der nichts verändert
        for beschriftung in MODAL_KNOEPFE:
            knopf = page.get_by_role("button", name=beschriftung, exact=True).first
            try:
                if knopf.count() and knopf.is_visible():
                    if _safe_click(page, knopf, notiz, "Dialog '%s'" % beschriftung):
                        geschlossen += 1
                        page.wait_for_timeout(600)
            except Exception:
                continue

        # 2) Schließen-Kreuze von Dialogen und Tipp-Fenstern.
        #    Jeder Klick läuft über _safe_click — die Publish-Sperre gilt also
        #    auch hier. Vorher galt sie in dieser Funktion überhaupt nicht.
        knoepfe = page.locator(", ".join(SCHLIESS_KNOEPFE))
        for i in range(min(knoepfe.count(), 12)):
            try:
                knopf = knoepfe.nth(i)
                if not knopf.is_visible():
                    continue
            except Exception:
                continue
            if _safe_click(page, knopf, notiz, "Hinweisfenster schließen"):
                geschlossen += 1

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
    # nur der Weg über die Beschriftung links daneben. Die ist ein
    # `button.fake-link.tooltip__host` und behält ihren Text — am echten
    # Formular verifiziert (2026-08-01). Die frühere Suche ging über
    # `div.summary__attributes--label`; ein solches Element gibt es dort gar
    # nicht, die Rückfallebene lief also ins Leere.
    knopf = page.locator(
        "xpath=//button[contains(@class,'tooltip__host')]"
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

    # Hier stand früher eine letzte Rückfallebene, die fragte, ob IRGENDEIN
    # Merkmalknopf diesen Wert anzeigt — und bei einem Treffer "gesetzt"
    # meldete. Das war wertlos und gefährlich zugleich: "Herstellernummer" und
    # "OE/OEM Referenznummer" bekommen denselben Wert (die Teilenummer) und
    # werden nacheinander abgearbeitet. Scheiterte die zweite Zeile, bestätigte
    # sie sich am Knopf der ersten, und der Bericht schwieg — während im
    # Entwurf ausgerechnet das OE-Feld leer blieb, über das Käufer suchen.
    # Eine Bestätigung durch ein fremdes Feld ist keine Bestätigung.
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
    def upload_kontrolle():
        # Die Wartefunktion oben schluckt ihren eigenen Timeout — ohne diese
        # Kontrolle galt der Upload als erledigt, auch wenn kein einziges Bild
        # ankam.
        try:
            gefunden = page.evaluate(
                "() => document.querySelectorAll('img[src*=\"ebayimg\"],"
                " [class*=\"uploader\"] img').length")
            return int(gefunden) >= len(photos)
        except Exception:
            return False
    if photos:
        _step(warnings, "Foto-Upload", upload_photos, upload_kontrolle)

    # --- Titel sicherstellen ---
    def ensure_title():
        field = page.locator("input[name='title']").first
        if not field.count():
            field = page.locator("input[aria-label*='Titel' i]").first
        if not field.count():
            # Vorher endete der Schritt hier stillschweigend als "erfolgreich":
            # `if field.count() and ...` ohne else, und _step bekam keine
            # Kontrolle. Ein nicht gefundenes Titelfeld blieb unbemerkt.
            raise RuntimeError("Titelfeld nicht gefunden")
        if field.input_value() != titel:
            field.fill(titel)
    _step(warnings, "Titel", ensure_title, lambda: _felder_stimmen(page, title=titel))

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

    def zustand_kontrolle():
        # Der gewählte Zustand steht im Zusammenfassungsknopf
        # button[name='condition'] (#summary-condition-field-value) — am echten
        # Formular verifiziert. Ohne diese Kontrolle galt der Schritt als
        # erledigt, sobald der Klick keine Ausnahme warf; ein gebrauchtes Teil
        # als "Neu" zu inserieren wäre der teuerste Fehler im ganzen Ablauf.
        for selektor in ("button[name='condition']", "#summary-condition-field-value"):
            feld = page.locator(selektor).first
            try:
                if feld.count():
                    return "gebraucht" in (feld.inner_text(timeout=4000) or "").lower()
            except Exception:
                continue
        return False
    _step(warnings, "Zustand 'Gebraucht'", set_condition, zustand_kontrolle)

    # --- Beschreibung (Rich-Text-Editor in einem iframe) ---
    def set_description():
        rahmen = page.frame_locator("iframe#se-rte-frame__summary")
        koerper = rahmen.locator("body")
        koerper.click(timeout=8000)
        page.keyboard.press("Meta+A")
        page.keyboard.press("Delete")
        koerper.type(description, delay=1)

    def beschreibung_kontrolle():
        # Der Editorinhalt landet in textarea[name="Beschreibung"] — mit
        # HTML-Auszeichnung, deshalb vor dem Vergleich die Tags herausnehmen.
        roh = _formularwerte(page).get("Beschreibung", "")
        klar = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", roh)).strip().lower()
        probe = re.sub(r"\s+", " ", description).strip().lower()[:30]
        return bool(probe) and probe in klar
    _step(warnings, "Beschreibung", set_description, beschreibung_kontrolle)

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
            # Erst das Ergebnis des Setzens, dann bis zu dreimal frisch
            # zurücklesen — beim Pflichtfeld "Hersteller" baut eBay die
            # Merkmalliste neu auf, der Wert steht kurz danach noch nicht da.
            if _gleich(gesetzte_werte.get(label, ""), value):
                return True
            for versuch in range(3):
                if _gleich(_merkmal_wert(page, label), value):
                    return True
                if versuch < 2:
                    page.wait_for_timeout(1000)
            return False
        _step(warnings, "Merkmal '%s'" % label, set_specific, kontrolle)

    # --- Angebotsformat: Sofort-Kaufen, keine Auktion ---
    # Diese Vorgabe war bisher nur ein Kommentar. Dass die Entwürfe trotzdem
    # stimmten, lag an eBays Voreinstellung — und eBay merkt sich die zuletzt
    # genutzten Verkaufseinstellungen. Nach einer einzigen von Hand angelegten
    # Auktion wäre der Median stillschweigend zum Startpreis geworden.
    def set_format():
        if _formularwerte(page).get("format") == "FixedPrice":
            return  # schon richtig vorbelegt, nichts anfassen
        auswahl = page.locator("select[name='format']").first
        if not auswahl.count():
            raise RuntimeError("Auswahlfeld 'Format' nicht gefunden")
        auswahl.select_option("FixedPrice", timeout=8000)
        page.wait_for_timeout(1200)
    _step(warnings, "Angebotsformat Sofort-Kaufen", set_format,
          lambda: _felder_stimmen(page, format="FixedPrice"))

    # --- Preis ---
    def set_price():
        preis = listing.get("preis")
        if preis is None:
            raise RuntimeError("kein Preis ermittelt — im Entwurf manuell setzen")
        # Auf das exakte Label festnageln. `aria-label*='Preis'` traf per
        # Teilstring auch "Mindestbetrag für Preisvorschlag" oder eine
        # Auto-Ablehnungsschwelle, und bei einer Selektorliste entscheidet die
        # Dokumentreihenfolge, nicht die Reihenfolge im Selektor.
        field = page.locator("input[aria-label='Artikelpreis']").first
        if not field.count():
            field = page.locator("input[name='price']").first
        if not field.count():
            raise RuntimeError("Feld 'Artikelpreis' nicht gefunden")
        field.fill(_betrag(preis))
    _step(warnings, "Preis", set_price,
          lambda: _felder_stimmen(page, Artikelpreis=_betrag(listing["preis"])))

    # --- Preisvorschläge zulassen (feste Vorgabe: immer an) ---
    def enable_best_offer():
        if _formularwerte(page).get("bestOfferEnabled") == "true":
            return
        # Vorher: `input[type=checkbox][aria-label*='Preisvorschläge']` mit
        # `if kasten.count() and ...` — traf der Selektor nicht, tat der Schritt
        # nichts, warf nichts und galt als erledigt. Real heißt das Feld
        # `input[name='bestOfferEnabled']` und ist ein role=switch.
        schalter = page.locator("input[name='bestOfferEnabled']").first
        if not schalter.count():
            raise RuntimeError("Schalter 'Preisvorschläge zulassen' nicht gefunden")
        schalter.scroll_into_view_if_needed(timeout=8000)
        _dialoge_schliessen(page)
        schalter.click(timeout=8000)
        page.wait_for_timeout(1500)
    _step(warnings, "Preisvorschläge zulassen", enable_best_offer,
          lambda: _felder_stimmen(page, bestOfferEnabled="true"))

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
        # Beides zusammen: der Schalter muss an sein UND der eigene Tarif
        # eingetragen. Die frühere Rückfallebene prüfte, ob "2 %" irgendwo im
        # Abschnittstext steht — "2 %" steht aber in "12 %", das die
        # Schnellauswahl ohnehin anzeigt. Sie meldete damit praktisch immer
        # Erfolg, auch bei eBays voreingestellten 10 %.
        return _felder_stimmen(
            page,
            promotedListingSelection="true",
            customAdRateField=config.ANZEIGENTARIF_PROZENT)
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

        def waehle(beschreibung: str, muster, feldname: str, sollwert: str) -> None:
            """Eine Option im Rücknahme-Dialog setzen — und sagen, wenn es nicht ging.

            Vorher hingen Frist und Rückversandzahler je an
            `if count() and is_visible()` ohne else-Zweig: passte der Textanker
            nicht mehr, wurden sie **still** übersprungen, und die Kontrolle
            merkte nichts. Ein Entwurf mit eBays Voreinstellung (30 Tage,
            Verkäufer zahlt) galt damit als erledigt.
            """
            if _formularwerte(page).get(feldname) == sollwert:
                return  # steht schon richtig
            treffer = page.get_by_text(muster).first
            try:
                if treffer.count() and treffer.is_visible():
                    treffer.click(timeout=5000)
                    page.wait_for_timeout(700)
                    if _formularwerte(page).get(feldname) == sollwert:
                        return
            except Exception:
                pass
            liste = page.locator("select[name='%s']" % feldname).first
            try:
                if liste.count():
                    liste.select_option(sollwert, timeout=5000)
                    page.wait_for_timeout(700)
                    return
            except Exception:
                pass
            warnings.append("Rücknahme: %s nicht gesetzt — eBays Voreinstellung "
                            "bleibt stehen" % beschreibung)

        # 2) Frist auf 14 Tage (Vorgabe des Nutzers)
        waehle("Frist %d Tage" % config.RUECKNAHME_TAGE,
               re.compile(r"^\s*%d Tage\s*$" % config.RUECKNAHME_TAGE),
               "returnDuration", "Days_%d" % config.RUECKNAHME_TAGE)

        # 3) Rückversand zahlt der Käufer
        if config.RUECKVERSAND_ZAHLT_KAEUFER:
            waehle("'Käufer zahlt Rückversand'",
                   re.compile(r"Käufer (zahlt|trägt).*(Rückversand|Rücksendung)", re.I),
                   "returnShippingPayer", "Buyer")

        fertig = page.locator("button.btn--primary", has_text=re.compile(
            r"^\s*(Fertig|Übernehmen|Speichern|OK)\s*$", re.I)).first
        if not fertig.count():
            fertig = page.get_by_role(
                "button", name=re.compile(r"^(Fertig|Übernehmen|Speichern|OK)$", re.I)).first
        if fertig.count() and fertig.is_visible():
            _safe_click(page, fertig, warnings, "Rücknahme übernehmen")
            page.wait_for_timeout(2500)

    def ruecknahme_kontrolle():
        # Alle drei Vorgaben prüfen, nicht nur eine. Vorher stand hier
        # `"Keine Rücknahme" not in text` — ein Entwurf mit eBays
        # Voreinstellung (30 Tage, Verkäufer zahlt) bestand das mühelos, und
        # der Nutzer hätte ab dann jede Rücksendung selbst bezahlt.
        return _felder_stimmen(
            page,
            returnPolicy="true",
            returnDuration="Days_%d" % config.RUECKNAHME_TAGE,
            returnShippingPayer="Buyer" if config.RUECKVERSAND_ZAHLT_KAEUFER else "Seller")
    _step(warnings, "Rücknahme aktivieren", enable_returns, ruecknahme_kontrolle)

    # --- Keine internationale Rücknahme / kein Auslandsversand ---
    # Vorgabe des Nutzers, die es im Code bisher überhaupt nicht gab: ein
    # `grep` nach "international" lieferte null Treffer. Dass der Schalter im
    # ersten Echtlauf aus war, ist eBays Voreinstellung — aus einer früheren
    # Vorlage kann er aktiv sein, und niemand hätte es bemerkt.
    def kein_auslandsversand():
        if _formularwerte(page).get("isInternationalShippingOn") == "false":
            return
        schalter = page.locator("input[name='isInternationalShippingOn']").first
        if not schalter.count():
            raise RuntimeError("Schalter 'Internationaler Versand' nicht gefunden")
        schalter.scroll_into_view_if_needed(timeout=8000)
        _dialoge_schliessen(page)
        schalter.click(timeout=8000)
        page.wait_for_timeout(1500)
    _step(warnings, "Kein internationaler Versand", kein_auslandsversand,
          lambda: _felder_stimmen(page, isInternationalShippingOn="false"))

    # --- Versandkosten (DHL-Stufe) ---
    def set_shipping():
        # eBay bietet Versanddienste als Kacheln mit festen Preisen an. Für
        # einen eigenen Betrag (60 € Spedition) führt der Weg über
        # "Versandkosten bearbeiten" unter "Wer zahlt?".
        # Bewusst ohne Default: fehlt der Wert, soll das als Warnung auffallen,
        # statt stillschweigend 0,00 € einzutragen — die Kontrolle unten hätte
        # dann gegen denselben falschen Wert geprüft und ihn bestätigt.
        preis = _betrag(listing["versandpreis"])
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
        return _betrag(listing["versandpreis"]) in _abschnitt_text(page, r"Wer zahlt")
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
    # Am echten Formular heißt der gewollte Knopf exakt
    # `button[aria-label="Speichern"]`; direkt daneben steht
    # `button[aria-label="Zu genannten Gebühren einstellen"]` (btn--primary),
    # der veröffentlicht.
    #
    # Die frühere Rückfallebene war `page.locator("button",
    # has_text="Speichern").last` — eine Teilstring-Suche, die auch in
    # Kindelementen sucht, auf den LETZTEN Treffer in DOM-Reihenfolge, ohne
    # Sichtbarkeitsprüfung. In einer klebrigen Fußleiste steht dort
    # typischerweise der Veröffentlichen-Knopf. Jetzt wird nur noch auf
    # verankerte, vollständige Beschriftungen gegriffen.
    saved = False
    for selektor in ("button[aria-label='Speichern']",
                     "button[aria-label='Entwurf speichern']"):
        knoepfe = page.locator(selektor)
        for i in range(knoepfe.count()):
            btn = knoepfe.nth(i)
            try:
                if not btn.is_visible():
                    continue
            except Exception:
                continue
            if _safe_click(page, btn, warnings, "Speichern"):
                saved = True
                break
        if saved:
            break
    if not saved:
        btn = page.get_by_role(
            "button", name=re.compile(r"^\s*(Entwurf speichern|Speichern)\s*$")).first
        if btn.count() and _safe_click(page, btn, warnings, "Speichern (Rückfallebene)"):
            saved = True
    if not saved:
        raise DraftError("Speichern-Button nicht gefunden — Entwurf existiert aber "
                         "vermutlich schon unter ebay.de/sh/lst/drafts (Auto-Save).")
    page.wait_for_timeout(4000)

    # --- Kontrolle: ist es ein Entwurf GEBLIEBEN? ---
    # Ohne diese Prüfung sähe ein versehentlich eingestelltes Inserat im Bericht
    # exakt aus wie ein gespeicherter Entwurf.
    if not _ist_noch_entwurf(page):
        warnings.append(
            "ACHTUNG: Nach dem Speichern ließ sich nicht mehr feststellen, dass es "
            "ein Entwurf ist (URL: %s). Bitte umgehend unter "
            "ebay.de/sh/lst/active nachsehen, ob versehentlich ein Angebot online "
            "gegangen ist." % (page.url or "")[:120])

    return {"draft_url": draft_url, "screenshot": str(shot) if shot.exists() else None}
