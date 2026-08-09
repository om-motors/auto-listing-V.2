"""Schritt 4: eBay-Entwurf per Browser-Automation anlegen (Playwright).

Nutzt ein persistentes Browser-Profil, in dem der Nutzer einmalig bei eBay
eingeloggt ist (python -m autolister.login).

SICHERHEIT: Es wird ausschließlich "Entwurf speichern"/"Speichern" geklickt.
Buttons, die veröffentlichen würden ("Artikel anbieten", "... einstellen",
"Verkaufen"), sind hart gesperrt.
"""
from __future__ import annotations

import logging
import os
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


# Wie großzügig gewartet wird. eBay reagiert träge, deshalb stehen im Formular
# an vielen Stellen feste Pausen — zusammen rund 45 Sekunden, einige davon
# mehrfach je Durchlauf (allein die Artikelmerkmale viermal).
#
# Sie alle hängen an diesem Regler. **1.0 sind die Werte, unter denen die
# Trockenläufe vom 2026-08-02 sauber durchliefen.** Kleiner ist schneller und
# riskanter.
#
# Woran man merkt, dass man zu weit gekürzt hat: Im Bericht tauchen wieder
# Punkte unter „Im eBay-Entwurf noch von Hand setzen" auf, obwohl die Werte
# im Entwurf stehen. Dann liest die Kontrolle, bevor eBay den Wert übernommen
# hat. In dem Fall über AUTOLISTER_TEMPO in der .env wieder hochsetzen.
TEMPO = max(0.3, float(os.environ.get("AUTOLISTER_TEMPO", "0.6")))


def _pause(page, millisekunden: int) -> None:
    """Feste Wartezeit, über TEMPO regelbar. Nie unter 200 ms."""
    page.wait_for_timeout(max(200, int(millisekunden * TEMPO)))


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


# Was die letzte fehlgeschlagene Kontrolle tatsächlich gelesen hat. `_step`
# hängt es an die Warnung, damit im Bericht steht, ob ein Wert falsch war oder
# das Feld gar nicht gefunden wurde. Ein Modulwert genügt: die Browser-Phase
# läuft bewusst seriell (siehe pipeline.py), es gibt hier keine Nebenläufigkeit.
_LETZTE_ABWEICHUNG = ""

# Zählt die am Formular hängenden Fotos. Bewusst EINE Fassung für das Warten
# beim Upload und für die spätere Kontrolle — liefen die auseinander, wartete
# der Upload auf ein Signal, das die Kontrolle gar nicht prüft. Genau so war
# es bis zum 2026-08-03: der Upload wartete auf <img>-Elemente mit blob:- oder
# ebayimg-Adresse, die es nie gibt, und lief bei JEDEM Teil in seinen
# 90-Sekunden-Timeout. Rund 110 Sekunden verschenkt, obwohl die Fotos längst
# oben waren — der größte Zeitfresser im ganzen Ablauf.
#
# eBay führt über dem Fotofeld einen eigenen Zähler ("3/25"). Der kommt von
# eBay selbst und überlebt jeden Umbau der Vorschaudarstellung.
ZAEHLE_FOTOS_JS = """() => {
  const treffer = (document.body.innerText || "")
                    .match(/\\b(\\d{1,2})\\s*\\/\\s*2[0-9]\\b/);
  if (treffer) return parseInt(treffer[1], 10);
  let n = 0;                       // Rückfallebene, falls der Zähler fehlt
  for (const b of document.querySelectorAll('img')) {
    const s = b.currentSrc || b.src || '';
    if (s.startsWith('blob:') || s.startsWith('data:')
        || s.includes('ebayimg')) n++;
  }
  return n;
}"""


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
    global _LETZTE_ABWEICHUNG
    _LETZTE_ABWEICHUNG = ""
    try:
        if pruefen():
            return True
        # Warum das Detail wichtig ist: "der Wert steht nicht im Formular" allein
        # sagt nicht, ob der Wert falsch war oder das Feld gar nicht gefunden
        # wurde. Ohne diesen Unterschied wurde am 2026-08-02 zweimal am falschen
        # Ende repariert — die Werte standen sauber im Entwurf, nur die Kontrolle
        # las ins Leere.
        warnings.append("%s: kein Fehler, aber der Wert steht nicht im Formular%s"
                        % (name, " [%s]" % _LETZTE_ABWEICHUNG
                           if _LETZTE_ABWEICHUNG else ""))
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
            const schalter = el.type === 'checkbox'
                             || el.getAttribute('role') === 'switch';
            const wert = schalter ? String(el.checked) : String(el.value ?? '');
            // Unter JEDEM Namen ablegen, den das Element trägt. eBay rendert
            // ein frisch angelegtes Formular anders als ein nachgeladenes:
            // mal steht die Bezeichnung in `name`, mal nur im `aria-label`.
            // Genau daran scheiterten am 2026-08-02 die Kontrollen für Preis
            // und Beschreibung — die Werte standen sauber im Entwurf, nur
            // unter einem anderen Schlüssel, als die Kontrolle suchte.
            for (const s of [el.getAttribute('name'), el.getAttribute('aria-label')]) {
              if (s && !(s in out)) out[s] = wert;
            }
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
    global _LETZTE_ABWEICHUNG
    abweichung = ""
    for versuch in range(3):
        werte = _formularwerte(page)
        fehler = []
        for feld, soll in erwartet.items():
            if feld not in werte:
                # Die vorhandenen Schlüssel mitgeben: ohne sie sagt "Feld nicht
                # gefunden" nicht, ob es fehlt oder nur anders heißt.
                vorhanden = ", ".join(sorted(werte)[:12]) or "keine"
                fehler.append("%s: Feld war nicht im Formular (gefunden: %s)"
                              % (feld, vorhanden))
            elif werte[feld].strip() != soll:
                fehler.append("%s: erwartet %r, gelesen %r"
                              % (feld, soll, werte[feld].strip()[:40]))
        if not fehler:
            _LETZTE_ABWEICHUNG = ""
            return True
        abweichung = "; ".join(fehler)
        if versuch < 2:
            _pause(page, 1200)
    _LETZTE_ABWEICHUNG = abweichung
    return False


def _ist_noch_entwurf(page) -> bool:
    """Nach dem Speichern belegen, dass NICHTS veröffentlicht wurde.

    Ohne diese Prüfung sähe ein versehentlich eingestelltes Inserat im Bericht
    exakt aus wie ein gespeicherter Entwurf — der Bericht würde lügen, und zwar
    an der einzigen Stelle, an der das wirklich teuer ist.
    """
    # Nach dem Speichern landet man auf der Entwurfsliste — das ist selbst der
    # Beweis. Am 2026-08-07 fehlte diese Zeile, und die Kontrolle schlug
    # Alarm ("prüfen, ob versehentlich ein Angebot online gegangen ist"),
    # obwohl alles in Ordnung war. Ein Fehlalarm ausgerechnet hier ist
    # schädlich: Wer diese Meldung zweimal umsonst liest, liest sie beim
    # dritten Mal nicht mehr.
    url = (page.url or "").lower()
    if "draftid" in url or "/lst/drafts" in url:
        return True

    text = ""
    for _ in range(3):          # die Seite lädt nach dem Speichern noch
        try:
            text = (page.locator("body").inner_text(timeout=8000) or "").lower()
            if text:
                break
        except Exception:
            pass
        _pause(page, 1500)
    if not text:
        return False            # nichts lesbar -> lieber melden als schweigen

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
                        _pause(page, 600)
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

        _pause(page, 500)
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
            await new Promise(r => setTimeout(r, 140));
          }
          window.scrollTo(0, 0);
          await new Promise(r => setTimeout(r, 300));
        }""")
    except Exception:
        pass
    # Kurz halten: `_settle` wartet auf Netzwerkruhe, und die tritt auf dem
    # Verkaufsformular nie ein — eBay fragt dauernd nach. Die Wartezeit lief
    # deshalb praktisch immer voll ab. 10 s waren hier reine Verschwendung.
    _settle(page, 3000)
    return True


def _merkmal_gleich(name: str, label: str) -> bool:
    """Meint dieser Feldname genau dieses Merkmal?

    Klammerzusätze zählen nicht mit: Das Feld heißt real
    „OE/OEM Referenznummer(n)", gesucht wird „OE/OEM Referenznummer".

    Ein reiner **Präfixvergleich wäre falsch** — er lässt „Hersteller" das
    Feld „Herstellernummer" greifen. Genau das ist am 2026-08-09 passiert: In
    der Kategorie „ECUs & Steuergeräte" gibt es überhaupt kein Merkmal
    „Hersteller". Also wurde „Audi" in die Herstellernummer geschrieben und
    die Teilenummer gleich hinterher — das Feld zeigte am Ende „Audi (+1)",
    und die echte Herstellernummer fehlte im Angebot.
    """
    def ohne_klammern(text: str) -> str:
        return re.sub(r"\(.*?\)", "", text or "").strip().lower()
    return bool(name) and ohne_klammern(name) == ohne_klammern(label)


def _merkmal_aria(page) -> List[str]:
    """Die `aria-label` **aller** Aufklapp-Knöpfe, in Dokumentreihenfolge.

    Auch die leeren — sonst stimmen die Positionen nicht mehr. Gefüllte
    Merkmale und die Abschnittsknöpfe (Fotos, Titel, Preis, Lieferung) tragen
    keines und erscheinen hier als leerer Eintrag.
    """
    try:
        return page.evaluate("""() => [...document.querySelectorAll(
            'button.se-expand-button__button')]
            .map(f => (f.getAttribute('aria-label') || '').trim());""") or []
    except Exception:
        return []


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
    # Über die Position, verglichen mit `_merkmal_gleich` — also exakt, nur
    # Klammerzusätze verziehen. Ein `[aria-label^='...']` stand hier bis zum
    # 2026-08-09 und war der Grund, warum „Hersteller" das Feld
    # „Herstellernummer" traf.
    stelle = _merkmal_position(page, label)
    if stelle >= 0:
        return page.locator("button.se-expand-button__button").nth(stelle)
    # Ist bereits ein Wert gesetzt, entfernt eBay das aria-label. Dann bleibt
    # nur der Weg über die Beschriftung links daneben. Die ist ein
    # `button.fake-link.tooltip__host` und behält ihren Text — am echten
    # Formular verifiziert (2026-08-01). Auch hier exakt vergleichen.
    knopf = page.locator(
        "xpath=//button[contains(@class,'tooltip__host')]"
        "[normalize-space(.)='%s']/following::button"
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
    # Die Stelle merken, SOLANGE das Feld noch leer ist und seinen Namen
    # trägt. Nach dem Füllen entfernt eBay das aria-label, und dann ist das
    # Feld über den Namen nicht mehr auffindbar — siehe _merkmal_wert().
    position = _merkmal_position(page, label)
    knopf.scroll_into_view_if_needed(timeout=8000)
    _dialoge_schliessen(page)
    knopf.click(timeout=8000)
    _pause(page, 1500)

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
        # **Niemals auf ein globales `input.textbox__control:visible` ausweichen.**
        # Nach einem gesetzten Merkmal ist das oft noch dessen Feld: Am
        # 2026-08-09 landete die Herstellernummer dadurch im Feld „Hersteller",
        # das danach „Audi (+1)" zeigte — zwei Werte in einem Feld, und das
        # eigentliche Feld blieb leer. Stattdessen die eigene Zeile neu suchen
        # und noch einmal öffnen; klappt auch das nicht, soll der Schritt
        # ehrlich scheitern.
        page.keyboard.press("Escape")
        _pause(page, 800)
        knopf = _merkmal_knopf(page, label)
        if knopf is None:
            raise RuntimeError("Aufklapp-Knopf nach dem Neuaufbau nicht mehr da")
        knopf.scroll_into_view_if_needed(timeout=6000)
        knopf.click(timeout=8000)
        _pause(page, 1500)
        zeile = knopf.locator("xpath=..")
        feld = zeile.locator("input.textbox__control").first
        feld.wait_for(state="visible", timeout=8000)
    feld.click(timeout=6000)
    feld.press_sequentially(str(wert), delay=110)
    _pause(page, 1800)

    # Exakten Vorschlag anklicken, falls einer angeboten wird
    vorschlag = page.get_by_role("option", name=str(wert), exact=True).first
    if vorschlag.count() and vorschlag.is_visible():
        vorschlag.click(timeout=4000)
    else:
        feld.press("Enter")
    _pause(page, 1800)
    page.keyboard.press("Escape")
    _pause(page, 800)

    # Wert zurücklesen — mit Rückfallebenen, weil beides schiefgehen kann:
    # sobald ein Merkmal gefüllt ist, entfernt eBay dessen aria-label (das
    # erneute Suchen findet nichts), und beim Pflichtfeld "Hersteller" baut
    # eBay die Merkmalliste neu auf (der alte Elementzeiger wird ungültig).
    _pause(page, 1200)
    for versuch in range(3):
        try:
            text = (knopf.inner_text(timeout=3000) or "").strip()
            if text:
                return text
        except Exception:
            pass
        # Über die gemerkte Stelle — der einzige Weg, der auch bei einem
        # gefüllten Feld noch trägt.
        text = _merkmal_wert(page, label, position)
        if text:
            return text
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
        _pause(page, 900)

    # Hier stand früher eine letzte Rückfallebene, die fragte, ob IRGENDEIN
    # Merkmalknopf diesen Wert anzeigt — und bei einem Treffer "gesetzt"
    # meldete. Das war wertlos und gefährlich zugleich: "Herstellernummer" und
    # "OE/OEM Referenznummer" bekommen denselben Wert (die Teilenummer) und
    # werden nacheinander abgearbeitet. Scheiterte die zweite Zeile, bestätigte
    # sie sich am Knopf der ersten, und der Bericht schwieg — während im
    # Entwurf ausgerechnet das OE-Feld leer blieb, über das Käufer suchen.
    # Eine Bestätigung durch ein fremdes Feld ist keine Bestätigung.
    return ""


def _merkmal_position(page, label: str) -> int:
    """An welcher Stelle steht das Wertfeld dieses Merkmals? (-1 = nicht da)

    Gezählt wird über **alle** `button.se-expand-button__button` der Seite, in
    Dokumentreihenfolge. Gesucht wird über das `aria-label` — das trägt ein
    Merkmal, **solange es leer ist**.

    Exakter Vergleich zuerst: „Hersteller" darf nicht „Herstellernummer"
    greifen. Erst danach der Präfixvergleich — das Feld heißt real
    „OE/OEM Referenznummer(n)".
    """
    for i, name in enumerate(_merkmal_aria(page)):
        if _merkmal_gleich(name, label):
            return i
    return -1


def _merkmal_wert(page, label: str, position: int = -1) -> str:
    """Gesetzten Wert eines Merkmals ablesen.

    **Sobald ein Merkmal einen Wert trägt, entfernt eBay dessen `aria-label`**
    (am echten Formular nachgemessen, 2026-08-02). Ein gefülltes Merkmal ist
    über seinen Namen also grundsätzlich nicht mehr auffindbar — beim Setzen
    greift der Selektor noch, beim Kontrollieren nie.

    Bis zum 2026-08-09 wurde deshalb über die Position gepaart: Beschriftungen
    (`button.tooltip__host`) und Wertfelder standen in derselben Reihenfolge.
    **Diese Annahme trägt nicht mehr.** eBay hat auch den Abschnitten Fotos,
    Titel, Preis und Lieferung Aufklapp-Knöpfe gegeben; gemessen wurden
    5 Beschriftungen gegen 16 Wertfelder, und die Kontrolle las für
    „Hersteller" prompt „Foto-Optionen ansehen".

    Ebenso wenig taugt das Eingabefeld `search-box-attributes…`: Es ist das
    Suchfeld der Auswahlliste und steht auch nach dem Setzen leer (geprüft am
    2026-08-09 mit gesetztem „Audi").

    Verlässlich ist allein die **Stelle in der Liste**, gemerkt zu dem
    Zeitpunkt, an dem das Feld noch leer war und seinen Namen trug. Deshalb
    reicht `_merkmal_setzen()` sie hier durch. Ohne sie bleibt nur der Versuch
    über das `aria-label` — der greift nur bei leeren Feldern.
    """
    if position is None or position < 0:
        position = _merkmal_position(page, label)
    if position < 0:
        return ""
    try:
        return page.evaluate("""(i) => {
          const felder = [...document.querySelectorAll(
                            'button.se-expand-button__button')];
          return felder[i] ? (felder[i].innerText || '').trim() : '';
        }""", position) or ""
    except Exception:
        return ""


def _merkmal_namen(page) -> List[str]:
    """Welche Artikelmerkmale bietet die gewählte Kategorie überhaupt an?

    Gelesen werden die `aria-label` der noch leeren Wertfelder. Nötig, um
    „gibt es hier nicht" von „nicht gefunden" zu unterscheiden: In der
    Kategorie „Sonstige" existiert kein Merkmal **Produktart**, und der
    Bericht verlangte dafür Handarbeit, die im Formular gar nicht möglich ist.
    """
    return [name for name in _merkmal_aria(page) if name]


def _merkmale_ausklappen(page, warnings: List[str]) -> None:
    """„Mehr anzeigen" bei den Artikelmerkmalen klicken.

    eBay zeigt seit dem 2026-08-09 nur die Pflichtmerkmale und versteckt den
    Rest hinter „Mehr anzeigen" — darunter **„OE/OEM Referenznummer(n)"**, das
    Feld, über das Käufer Kfz-Teile suchen. Ohne diesen Klick meldete der
    Schritt „Aufklapp-Knopf nicht gefunden", und das Feld blieb leer.
    """
    knopf = page.get_by_role(
        "button", name=re.compile(r"^\s*Mehr anzeigen\s*$", re.I)).first
    try:
        if not knopf.count() or not knopf.is_visible():
            return
    except Exception:
        return
    if _safe_click(page, knopf, warnings, "Merkmale ausklappen"):
        _pause(page, 2000)


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


def _kachel_adressen(page) -> list:
    """Die Bildadressen der Vorschaukacheln — der einzige echte Nachweis.

    Die Kacheln tragen ihr Bild als CSS-`background-image` von
    `i.ebayimg.com`. Bearbeitet eBay ein Foto, liegt danach eine **andere**
    Adresse dort. Damit lässt sich prüfen, was wirklich passiert ist, statt
    Klicks zu zählen: Am 2026-08-07 meldete der Schritt „3 von 3", während im
    Inserat nur ein Foto freigestellt war.
    """
    try:
        return page.evaluate("""() =>
          [...document.querySelectorAll('button.uploader-thumbnails-ux__image')]
            .map(b => getComputedStyle(b).backgroundImage || '')
            .filter(s => s.includes('ebayimg'))""") or []
    except Exception:
        return []


def _hintergrund_entfernen(page, anzahl: int, warnings: List[str]) -> int:
    """Bei jedem Foto eBays „Hintergrund entfernen" auslösen.

    Aufbau, am echten Formular gemessen (2026-08-07):

        button.uploader-thumbnails-ux__image      Vorschaukachel, öffnet Editor
        div[role=dialog].uploader-editor          der Editor
          button.icon-btn[title='Hintergrund entfernen']
          button.btn--primary  "Speichern"        erscheint ERST danach
          button.btn--primary  "Fertig"           im Normalzustand

    **Zwei Fallen, beide teuer bezahlt:**

    1. Nach dem Freistellen wechselt der Editor in einen Bestätigungszustand:
       Die Blätterpfeile verschwinden, und „Fertig" wird durch „Abbrechen"
       und „Speichern" ersetzt. Ohne den Klick auf „Speichern" wird das
       Ergebnis **verworfen** — der Hintergrund war im Editor sichtbar weg,
       im Inserat unverändert.
    2. Das Blättern über die Pfeile ist nicht verlässlich: Der Schritt
       meldete „3 von 3", tatsächlich wurde dreimal dasselbe Foto bearbeitet.
       Deshalb wird jedes Foto **gezielt über seine Kachel** geöffnet. Mehr
       Klicks, dafür weiß man, welches Foto man vor sich hat.

    Gezählt wird nicht, wie oft geklickt wurde, sondern **wie viele
    Kacheladressen sich geändert haben**.
    """
    kacheln = page.locator("button.uploader-thumbnails-ux__image")
    if not kacheln.count():
        warnings.append("Hintergrund entfernen: keine Fotokacheln gefunden")
        return 0

    vorher = _kachel_adressen(page)
    editor = page.locator("div[role=dialog].uploader-editor")

    def geduldig(ziel, sekunden: int = 45) -> bool:
        beschriftung = _beschriftung(ziel)
        if beschriftung and FORBIDDEN.search(beschriftung):
            warnings.append("Foto-Editor: Klick auf %r blockiert "
                            "(würde veröffentlichen!)" % beschriftung[:60])
            return False
        ende = time.time() + sekunden
        while time.time() < ende:
            try:
                ziel.click(timeout=4000)
                return True
            except Exception:
                page.wait_for_timeout(900)
        return False

    def warte_auf(ziel, sekunden: int = 60) -> bool:
        ende = time.time() + sekunden
        while time.time() < ende:
            try:
                if ziel.count() and ziel.first.is_visible():
                    return True
            except Exception:
                pass
            page.wait_for_timeout(600)
        return False

    for i in range(min(anzahl, kacheln.count())):
        kachel = kacheln.nth(i)
        try:
            kachel.scroll_into_view_if_needed(timeout=6000)
        except Exception:
            pass
        _dialoge_schliessen(page)
        if not geduldig(kachel, sekunden=20):
            warnings.append("Hintergrund entfernen: Foto %d ließ sich nicht "
                            "öffnen" % (i + 1))
            continue
        if not warte_auf(editor, sekunden=15):
            warnings.append("Hintergrund entfernen: Editor ging bei Foto %d "
                            "nicht auf" % (i + 1))
            continue

        knopf = editor.locator("button.icon-btn[title='Hintergrund entfernen']")
        speichern = editor.locator(
            "button.btn--primary", has_text=re.compile(r"^\s*Speichern\s*$"))
        fertig = editor.locator(
            "button.btn--primary", has_text=re.compile(r"^\s*Fertig\s*$"))

        if not knopf.count():
            warnings.append("Hintergrund entfernen: Knopf fehlt bei Foto %d"
                            % (i + 1))
        elif not geduldig(knopf.first):
            warnings.append("Hintergrund entfernen: Foto %d ließ sich nicht "
                            "freistellen" % (i + 1))
        elif not warte_auf(speichern, sekunden=60):
            warnings.append("Hintergrund entfernen: bei Foto %d kam kein "
                            "Speichern-Knopf" % (i + 1))
        else:
            # **Der Speichern-Knopf erscheint, BEVOR eBay fertig gerechnet
            # hat.** Wer sofort drückt, speichert das unbearbeitete Bild —
            # genau das passierte am 2026-08-07: Die Kacheladresse änderte
            # sich (also wurde gespeichert), der Hintergrund war aber noch da.
            # Im Handversuch mit acht Sekunden Pause davor saß es sofort.
            page.wait_for_timeout(8000)
            if not geduldig(speichern.first):
                warnings.append("Hintergrund entfernen: Foto %d ließ sich "
                                "nicht speichern" % (i + 1))
            else:
                page.wait_for_timeout(6000)   # eBay lädt das Bild neu
                warte_auf(fertig, sekunden=60)

        # Editor wieder zumachen, damit die nächste Kachel anklickbar ist.
        for _ in range(3):
            if not editor.count() or not editor.first.is_visible():
                break
            if fertig.count() and geduldig(fertig.first, sekunden=15):
                _pause(page, 1500)
                continue
            page.keyboard.press("Escape")
            _pause(page, 1200)
        _pause(page, 1500)

    nachher = _kachel_adressen(page)
    geaendert = sum(1 for a, b in zip(vorher, nachher) if a != b)
    log.info("Hintergrund entfernt: %d von %d Foto(s) (an den Kacheladressen "
             "nachgeprüft)", geaendert, anzahl)
    return geaendert


def _warnung_einmal(warnings: List[str], text: str) -> None:
    """Dieselbe Warnung nur einmal in den Bericht schreiben.

    Die Warteschleife vor dem Formular fragt im Halbsekundentakt nach. Eine
    Warnung, die dort ungeprüft angehängt wird, steht am Ende rund 120-mal im
    Bericht und begräbt alles andere unter sich.
    """
    if text not in warnings:
        warnings.append(text)


# Zustandsschlüssel von eBay, an der echten Seite abgelesen (2026-08-09):
# 1000 Neu · 1500 Neu: Sonstige · 2500 Generalüberholt · 3000 Gebraucht ·
# 7000 Als Ersatzteil/defekt. Für dieses Projekt ist es immer "Gebraucht" —
# so steht es in CLAUDE.md unter den fachlichen Vorgaben.
ZUSTAND_GEBRAUCHT = "3000"

# So heißt der Schalter im Abschnitt „Angebot bewerben" seit dem 2026-08-09.
# Es gibt zwei Modelle: „Basis" kostet pro Verkauf, „Premium" pro Klick. Für
# dieses Projekt gilt Basis — der Nutzer zahlt nur, wenn etwas verkauft wird.
ANZEIGE_BASIS = "Basis auswählen"


def _ruecknahme_text(page) -> str:
    """Was die Rücknahme-Zusammenfassung sagt — klein geschrieben.

    Seit dem 2026-08-09 gibt es weder die Karte „Details zur Lieferung" noch
    den Rücknahme-Dialog. eBay merkt sich die Einstellung am Konto und zeigt
    sie nur noch an: `div.returns-field-display__container`, zweimal —
    einmal Inland, einmal Ausland.
    """
    stuecke = []
    for el in page.locator("div.returns-field-display__container").all():
        try:
            stuecke.append(" ".join((el.inner_text(timeout=3000) or "").split()))
        except Exception:  # noqa: BLE001 — ein unlesbarer Block darf nicht alles kippen
            continue
    return " ".join(stuecke).lower()


def _zustand_vorab_waehlen(page, warnings: List[str]) -> bool:
    """Die Zwischenseite „Bestätigen Sie die Details" beantworten.

    eBay fragt den Artikelzustand seit dem 2026-08-09 **vor** dem Formular ab,
    unter derselben Adresse wie die Kategoriewahl — erkennbar allein am
    Anhängsel `view=sellnode-condition`. Die Pipeline kannte den Schritt
    nicht, wartete 60 Sekunden auf ein Formular, das nie kam, und meldete
    „Verkaufsformular wurde nicht erreicht". Daran scheiterten alle drei Teile
    des ersten Laufs unter `om.motors`.

    Aufbau, an der echten Seite ausgemessen:

        legend                             "Wählen Sie den Artikelzustand aus"
        input[type=radio][name=condition]  value 1000/1500/2500/3000/7000
        button                             "Weiter zum Angebot"

    Das `input` liegt unter einer gestalteten Hülle und gilt damit als
    unsichtbar — ein gewöhnliches `check()` scheitert daran. Deshalb erst der
    normale Weg, dann `force`, und in beiden Fällen wird über `is_checked()`
    nachgesehen, ob die Auswahl wirklich sitzt.
    """
    if "sellnode-condition" not in (page.url or ""):
        return False

    ziel = page.locator("input[type=radio][name='condition'][value='%s']"
                        % ZUSTAND_GEBRAUCHT).first
    if not ziel.count():
        _warnung_einmal(warnings, "Zustand vorab: Auswahl 'Gebraucht' nicht "
                                  "gefunden — bitte selbst im Entwurf setzen")
        return False

    gesetzt = False
    for erzwingen in (False, True):
        try:
            ziel.check(force=erzwingen, timeout=4000)
            gesetzt = ziel.is_checked()
        except Exception:  # noqa: BLE001 — der zweite Versuch darf noch greifen
            gesetzt = False
        if gesetzt:
            break
    if not gesetzt:
        _warnung_einmal(warnings, "Zustand vorab: 'Gebraucht' ließ sich nicht "
                                  "auswählen — bitte selbst im Entwurf setzen")
        return False
    _pause(page, 800)

    weiter = page.get_by_role(
        "button", name=re.compile(r"^\s*Weiter zum Angebot\s*$", re.I)).first
    if not weiter.count():
        _warnung_einmal(warnings, "Zustand vorab: Knopf 'Weiter zum Angebot' "
                                  "nicht gefunden")
        return False
    _safe_click(page, weiter, warnings, "Zustand bestätigen")
    _settle(page)

    # Kontrolle: Die Zwischenseite muss weg sein. Ohne sie gälte der Schritt
    # als erledigt, sobald keine Ausnahme flog — und die Warteschleife liefe
    # weiter im Kreis, genau wie vorher.
    if "sellnode-condition" in (page.url or ""):
        _warnung_einmal(warnings, "Zustand vorab: Seite blieb nach dem "
                                  "Bestätigen stehen")
        return False
    log.info("Zustand vorab gewählt: Gebraucht")
    return True


# Wie der Knopf heißt, mit dem man die Produktbibliothek übergeht. Am
# 2026-08-09 trug er „Ohne passendes Produkt fortfahren"; die übrigen
# Schreibweisen sind ältere Fassungen, die eBay schon benutzt hat.
PRODUKT_UEBERGEHEN = ("Ohne passendes Produkt fortfahren",
                      "Ohne Übereinstimmung fortfahren",
                      "Ohne Produktübereinstimmung fortfahren")


def _produktauswahl_uebergehen(page, warnings: List[str]) -> bool:
    """Die Seite „Passendes Produkt finden" übergehen.

    Kennt eBay die Kategorie bereits, bietet es stattdessen Produkte aus
    seiner Bibliothek an („Top-Auswahl aus der Produktbibliothek",
    „Vergleichbare Angebote von anderen Verkäufern"). Auch diese Seite läuft
    unter `/sl/prelist/identify`, hat aber weder Kategoriekarten noch
    Zustandsauswahl — die Pipeline lief hier ins Leere. Am 2026-08-09
    scheiterte das Steuergerät `8K0907801J` genau daran, während die beiden
    anderen Teile an der Zustandsabfrage hingen.

    **Ein Katalogprodukt wird bewusst nicht gewählt.** Es brächte Titel und
    Artikelmerkmale von eBay mit und würde damit überschreiben, was
    `compose.py` aus den Vergleichsangeboten hergeleitet hat.

        button.product-button...      Katalogprodukte  <- nicht anfassen
        button "Ohne passendes Produkt fortfahren"     <- dieser hier
    """
    if "/prelist/identify" not in (page.url or ""):
        return False

    vorher = page.url
    for name in PRODUKT_UEBERGEHEN:
        knopf = page.get_by_role(
            "button", name=re.compile(r"^\s*%s\s*$" % re.escape(name), re.I)).first
        try:
            if not knopf.count() or not knopf.is_visible():
                continue
        except Exception:  # noqa: BLE001 — nächste Schreibweise probieren
            continue
        if not _safe_click(page, knopf, warnings, "Produktauswahl übergehen"):
            continue
        _settle(page)
        # Kontrolle: Der Klick muss die Seite weiterbringen. Bleibt die
        # Adresse stehen, hat er nichts bewirkt — dann soll die Schleife es
        # anders versuchen, statt den Schritt als erledigt zu verbuchen.
        if page.url == vorher:
            continue
        log.info("Produktauswahl übergangen ('%s')", name)
        return True
    return False


def _kategorie_waehlen(page, warnings: List[str]) -> bool:
    """Die Kategorieabfrage auf `/sl/prelist/identify` beantworten.

    eBay schiebt diese Seite **nur manchmal** ein: Findet es zum Titel keine
    eindeutige Kategorie, fragt es nach ("Geben Sie eine Kategorie für Ihren
    Artikel an"), sonst springt es direkt ins Formular. Deshalb fiel der
    Schritt bei den Sonnenblenden-Testläufen nie auf — beim ersten echten
    Auftrag vom Handy (Schmutzfänger, 2026-08-02) blieb der Lauf hier stehen
    und meldete "Verkaufsformular wurde nicht erreicht".

    Aufbau der Seite, am echten Formular ermittelt:

        button.se-field-card__body   "Auto & Motorrad: Teile > … > Sonstige"
                                     <- Empfehlungen, voller Pfad mit " > "
        button.se-field-card__body   "Baby" / "Briefmarken" / …
                                     <- Gesamtliste, nur ein Name
        button                       "Fertig"

    Beide Sorten tragen dieselbe Klasse. Unterschieden werden sie am `>` im
    Text: nur die Empfehlungen enthalten den vollen Pfad. Genommen wird die
    erste — das ist eBays eigener bester Vorschlag zum Titel.
    """
    url = page.url or ""
    if "/prelist/identify" not in url or "sellnode-condition" in url:
        # Die Zustandsabfrage läuft unter derselben Adresse. Ohne diese
        # Ausnahme sucht die Funktion dort vergeblich nach Kategoriekarten und
        # legt bei jedem Schleifendurchlauf eine Warnung nach — am 2026-08-09
        # rund 120 Stück je Auftrag.
        return False

    karten = page.locator("button.se-field-card__body")
    if not karten.count():
        # Gar keine Kategoriekarten heißt: Das ist nicht die Kategorieseite,
        # sondern eine der Schwestern (Produktbibliothek, Zustandsabfrage).
        # Hier zu warnen wäre schlicht falsch und schickt den Nutzer an die
        # falsche Stelle.
        return False

    gewaehlt = ""
    for i in range(min(karten.count(), 40)):
        karte = karten.nth(i)
        try:
            text = " ".join((karte.inner_text(timeout=2000) or "").split())
            if ">" not in text or not karte.is_visible():
                continue
        except Exception:
            continue
        if _safe_click(page, karte, warnings, "Kategorie wählen"):
            gewaehlt = text
            break

    if not gewaehlt:
        _warnung_einmal(warnings, "Kategorieauswahl: keine empfohlene Kategorie "
                                  "gefunden — im Entwurf von Hand setzen")
        return False

    _pause(page, 1500)
    fertig = page.get_by_role(
        "button", name=re.compile(r"^\s*Fertig\s*$", re.I)).first
    if fertig.count():
        _safe_click(page, fertig, warnings, "Kategorie übernehmen")
        _pause(page, 2000)
    log.info("Kategorie gewählt: %s", gewaehlt[:80])
    return True


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

    # Ggf. "Weiter" / "Ohne Übereinstimmung fortfahren".
    #
    # Zwei Fallen, beide am 2026-08-02 im Echtbetrieb aufgelaufen:
    #
    # 1. `exact=False` ist eine Teilstringsuche. "Weiter" trifft damit auch
    #    "Zum erWEITERten Verkaufsformular wechseln" — einen Knopf, der auf ein
    #    ganz anderes Formular führt und den FORBIDDEN zu Recht sperrt.
    # 2. Genommen wurde nur `.first`. Stand der gesperrte Knopf vorn, war die
    #    Sache erledigt: der echte "Weiter"-Knopf wurde nie geklickt, und der
    #    Lauf endete auf /sl/prelist/identify mit "Formular nicht erreicht".
    #
    # Deshalb: verankerter Namensvergleich und über ALLE Treffer gehen, bis
    # einer sitzt. Ein gesperrter Knopf beendet die Suche nicht mehr.
    EINSTIEG = ("Weiter", "Ohne Übereinstimmung fortfahren", "Weiter zum Angebot",
                "Fortfahren", "Neues Angebot erstellen")
    for label in EINSTIEG:
        if "draftId" in page.url or "/lstng" in page.url:
            break  # Formular ist schon da
        knoepfe = page.get_by_role(
            "button", name=re.compile(r"^\s*%s\s*$" % re.escape(label), re.I))
        for i in range(min(knoepfe.count(), 5)):
            btn = knoepfe.nth(i)
            try:
                if not btn.is_visible():
                    continue
            except Exception:
                continue
            if _safe_click(page, btn, warnings, "Einstieg '%s'" % label):
                _settle(page)
                break

    # Warten bis das Verkaufsformular (mit draftId) geladen ist. Unterwegs kann
    # eBay noch die Kategorieauswahl dazwischenschieben — siehe unten.
    deadline = time.time() + 60
    while time.time() < deadline:
        _check_captcha(page)
        if "draftId" in page.url or "/lstng" in page.url:
            break
        # Drei verschiedene Zwischenseiten, alle unter `/sl/prelist/identify`:
        # Produktbibliothek, Kategoriewahl, Zustandsabfrage. Welche kommt,
        # hängt davon ab, wie sicher eBay den Titel einordnen kann — beim
        # Steuergerät erschien die erste, bei den beiden anderen Teilen des
        # Laufs vom 2026-08-09 die dritte. Jede Funktion prüft selbst, ob sie
        # zuständig ist, und meldet mit `True`, dass sich etwas bewegt hat.
        if _produktauswahl_uebergehen(page, warnings):
            continue
        if _zustand_vorab_waehlen(page, warnings):
            continue
        _kategorie_waehlen(page, warnings)
        _pause(page, 500)
    if "draftId" not in page.url and "/lstng" not in page.url:
        # Sagen, WO es hing. "Formular nicht erreicht" allein hat am
        # 2026-08-09 nach kaputten Selektoren ausgesehen, während in Wahrheit
        # eine unbekannte Zwischenseite davorstand.
        stelle = ""
        if "sellnode-condition" in page.url:
            stelle = " — die Zustandsabfrage ließ sich nicht beantworten"
        elif "/prelist/identify" in page.url:
            stelle = " — die Kategorieabfrage ließ sich nicht beantworten"
        raise DraftError("Verkaufsformular wurde nicht erreicht%s (URL: %s)"
                         % (stelle, page.url))
    draft_url = page.url

    def _entwurfsadresse() -> str:
        """Die Adresse mit `draftId` — die einzige, die den Entwurf öffnet.

        Die Warteschleife oben bricht schon ab, sobald `/lstng` in der URL
        steht; die `draftId` vergibt eBay teils erst kurz danach. Beim ersten
        Auftrag vom Handy (2026-08-02) landete deshalb eine Adresse ohne
        `draftId` im Bericht — der Link führte auf die Seite *vor* dem Entwurf.
        """
        for _ in range(10):
            if "draftId" in (page.url or ""):
                return page.url
            _pause(page, 700)
        return page.url

    # Der Wechsel-Dialog erscheint sofort nach dem Anlegen des Entwurfs und
    # blockiert alles Weitere — deshalb hier schon wegräumen, nicht erst
    # nach dem Foto-Upload.
    _settle(page, 8000)
    _dialoge_schliessen(page)

    # --- Fotos hochladen (direkt ins File-Input, kein CORS-Server nötig) ---
    def upload_photos():
        file_input = page.locator("input[type='file']").first
        file_input.set_input_files([str(p) for p in photos])
        # Auf eBays eigenen Zähler warten ("3/25") — dieselbe Zählung wie in
        # der Kontrolle darunter.
        #
        # Vorher wartete diese Stelle darauf, dass Vorschaubilder als <img>
        # mit blob:- oder ebayimg-Adresse erscheinen. Das tun sie nie, wie am
        # 2026-08-02 am echten Formular gemessen. Die Wartefunktion lief damit
        # **bei jedem Teil** in ihren 90-Sekunden-Timeout und hängte noch 20 s
        # Netzwerkruhe an: rund 110 Sekunden verschenkt, obwohl die Fotos
        # längst oben waren. Das war der größte Zeitfresser im ganzen Ablauf.
        try:
            page.wait_for_function("n => (%s)() >= n" % ZAEHLE_FOTOS_JS,
                                   arg=len(photos), timeout=60000)
        except Exception:
            _settle(page, 8000)
    def upload_kontrolle():
        # Die Wartefunktion oben schluckt ihren eigenen Timeout — ohne diese
        # Kontrolle galt der Upload als erledigt, auch wenn kein einziges Bild
        # ankam.
        global _LETZTE_ABWEICHUNG
        try:
            gefunden = page.evaluate(ZAEHLE_FOTOS_JS)
            if int(gefunden) >= len(photos):
                return True
            _LETZTE_ABWEICHUNG = ("%d Vorschaubilder gefunden, %d erwartet"
                                  % (int(gefunden), len(photos)))
            return False
        except Exception as exc:
            _LETZTE_ABWEICHUNG = "Zählung fehlgeschlagen: %s" % str(exc).splitlines()[0]
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

    # Entwurfsadresse JETZT festhalten, nicht am Ende.
    #
    # `_entwurfsadresse()` wartet darauf, dass `draftId` in der URL steht. Am
    # Ende des Ablaufs ist das zu spät: Nach dem Speichern leitet eBay auf die
    # Entwurfsliste `/sh/lst/drafts` um, und genau die landete am 2026-08-07 im
    # Bericht und auf dem Handy — ein Link, der den Entwurf nicht öffnet.
    draft_url = _entwurfsadresse() or draft_url
    anzahl = _tooltips_schliessen(page)
    log.info("Formular geladen, %d Hinweisfenster geschlossen", anzahl)

    # --- Hintergrund der Fotos entfernen (Vorgabe des Nutzers 2026-08-07) ---
    if photos and config.HINTERGRUND_ENTFERNEN:
        entfernt = {"anzahl": 0}

        def hintergrund():
            entfernt["anzahl"] = _hintergrund_entfernen(page, len(photos), warnings)

        def hintergrund_kontrolle():
            global _LETZTE_ABWEICHUNG
            if entfernt["anzahl"] >= len(photos):
                return True
            _LETZTE_ABWEICHUNG = ("%d von %d Foto(s) bearbeitet"
                                  % (entfernt["anzahl"], len(photos)))
            return False
        _step(warnings, "Hintergrund entfernen", hintergrund, hintergrund_kontrolle)

    # --- Zustand: Gebraucht ---
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

    def set_condition():
        # **Erst nachsehen, dann klicken.** Seit dem 2026-08-09 fragt eBay den
        # Zustand bereits vor dem Formular ab (`_zustand_vorab_waehlen`), er
        # steht hier also meist schon richtig. Der alte Klickversuch lief dann
        # in einen Timeout von 8 s, `_step` brach ab, ohne die Kontrolle je
        # auszuführen — und der Bericht verlangte Handarbeit für einen Wert,
        # der längst im Entwurf stand. So gemessen an allen drei Teilen des
        # Laufs vom 2026-08-09.
        if zustand_kontrolle():
            log.info("Zustand steht bereits auf 'Gebraucht'")
            return
        knopf = page.locator("button.condition-recommendation-value",
                             has_text=re.compile(r"^\s*Gebraucht\s*$")).first
        if not knopf.count():
            knopf = page.get_by_role("button", name="Gebraucht", exact=True).first
        knopf.click(timeout=8000)
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
        global _LETZTE_ABWEICHUNG
        werte = _formularwerte(page)
        # Das Feld heißt je nach Formularzustand anders — mal "Beschreibung"
        # (aria-label), mal "description" (name). Deshalb mehrere Schreibweisen
        # prüfen statt einer.
        # Zuerst im Editor selbst nachsehen — dort steht der Text sofort.
        #
        # Das versteckte Textfeld taugt hier nicht: eBay belegt es beim Anlegen
        # mit dem TITEL vor, und der Rich-Text-Editor schreibt seinen Inhalt
        # erst beim Verlassen zurück. Die Kontrolle las deshalb am 2026-08-02
        # "original audi a4 b8 a5 sonnenblende vorne 8k0857552" — den Titel —
        # und meldete die Beschreibung als fehlend, obwohl sie im Editor stand.
        roh = ""
        try:
            roh = page.frame_locator("iframe#se-rte-frame__summary") \
                      .locator("body").inner_text(timeout=6000) or ""
        except Exception:
            pass
        if not roh:
            # Rückfallebene: das versteckte Feld. Schalterwerte ausschließen,
            # sonst trifft das Muster auch `descriptionEditorMode` ("false").
            kandidaten = [w for s, w in werte.items()
                          if re.search(r"beschreib|description", s, re.I)
                          and w not in ("true", "false")]
            roh = max(kandidaten, key=len) if kandidaten else ""
        if not roh:
            _LETZTE_ABWEICHUNG = ("kein gefülltes Beschreibungsfeld gefunden "
                                  "(vorhanden: %s)" % (", ".join(sorted(werte)[:12]) or "keine"))
            return False
        klar = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", roh)).strip().lower()
        probe = re.sub(r"\s+", " ", description).strip().lower()[:30]
        if bool(probe) and probe in klar:
            return True
        _LETZTE_ABWEICHUNG = "gesucht %r, gelesen %r" % (probe, klar[:60])
        return False
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
    # Erst aufklappen: „OE/OEM Referenznummer(n)" liegt seit dem 2026-08-09
    # hinter „Mehr anzeigen" und war vorher schlicht nicht da.
    _merkmale_ausklappen(page, warnings)

    # Was diese Kategorie überhaupt anbietet. Ein Merkmal, das es hier nicht
    # gibt, ist keine offene Aufgabe für den Nutzer — er könnte sie gar nicht
    # erledigen. Es gehört als Meldung in den Bericht, nicht in die Hakenliste.
    vorhanden = _merkmal_namen(page)

    gesetzte_werte: Dict[str, str] = {}
    stellen: Dict[str, int] = {}   # wo das Feld stand, als es noch leer war
    for label, value in merkmale.items():
        if not value or label in config.MERKMALE_AUSLASSEN:
            continue
        if vorhanden and not any(_merkmal_gleich(n, label) for n in vorhanden):
            warnings.append("Kategorie kennt kein Merkmal '%s' — übersprungen "
                            "(vorhanden: %s)"
                            % (label, ", ".join(_merkmal_namen(page))[:150]))
            continue

        def set_specific(label=label, value=value):
            stellen[label] = _merkmal_position(page, label)
            gesetzte_werte[label] = _merkmal_setzen(page, label, value)

        def kontrolle(label=label, value=value):
            # Erst das Ergebnis des Setzens, dann bis zu dreimal frisch
            # zurücklesen — beim Pflichtfeld "Hersteller" baut eBay die
            # Merkmalliste neu auf, der Wert steht kurz danach noch nicht da.
            global _LETZTE_ABWEICHUNG
            if _gleich(gesetzte_werte.get(label, ""), value):
                return True
            gelesen = ""
            for versuch in range(3):
                gelesen = _merkmal_wert(page, label, stellen.get(label, -1))
                if _gleich(gelesen, value):
                    return True
                if versuch < 2:
                    _pause(page, 1000)
            _LETZTE_ABWEICHUNG = ("erwartet %r; beim Setzen %r, beim Zurücklesen %r"
                                  % (value, gesetzte_werte.get(label, ""), gelesen))
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
        _pause(page, 1200)
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
        _pause(page, 1500)
    _step(warnings, "Preisvorschläge zulassen", enable_best_offer,
          lambda: _felder_stimmen(page, bestOfferEnabled="true"))

    # --- Angebot bewerben: eigener Anzeigentarif 2 % ---
    def promote_2_percent():
        # **Umbau vom 2026-08-09.** Den Block `div.promoted-listing-simple`
        # mit der Schnellauswahl 8/10/12 % und dem Knopf „Eigenen
        # Anzeigentarif auswählen" gibt es nicht mehr. Stattdessen stehen dort
        # zwei Schalter: „Basis auswählen" (Kosten pro Verkauf) und „Premium
        # auswählen" (Kosten pro Klick).
        #
        # Reihenfolge bleibt zwingend: Erst wenn „Basis" an ist, existiert das
        # Tarif-Feld `adRate` — eBay legt es mit **11 %** an. Wer es nicht
        # überschreibt, zahlt das Fünfeinhalbfache der Vorgabe.
        schalter = page.locator(
            "input[role='switch'][name='%s']" % ANZEIGE_BASIS).first
        if not schalter.count():
            raise RuntimeError("Schalter '%s' nicht gefunden" % ANZEIGE_BASIS)
        schalter.scroll_into_view_if_needed(timeout=8000)
        if not schalter.is_checked():
            # Das echte input liegt unter einer gestalteten Hülle — ohne
            # `force` scheitert der Klick an der Sichtbarkeitsprüfung.
            schalter.check(force=True, timeout=8000)
            _pause(page, 2500)

        feld = page.locator("input[name='adRate']").first
        if not feld.count():
            raise RuntimeError("Tarif-Feld 'adRate' nicht gefunden")
        feld.scroll_into_view_if_needed(timeout=8000)
        feld.click(timeout=6000)
        feld.fill("")
        feld.press_sequentially(config.ANZEIGENTARIF_PROZENT, delay=120)
        page.keyboard.press("Tab")
        _pause(page, 1500)

    def bewerben_kontrolle():
        # Beides zusammen: Der Schalter muss an sein UND der Tarif stimmen.
        # Ein Textvergleich taugt hier nicht — „2 %" steht auch in „12 %",
        # und genau daran meldete die alte Kontrolle fast immer Erfolg.
        werte = _formularwerte(page)
        global _LETZTE_ABWEICHUNG
        if (werte.get("adRate") == config.ANZEIGENTARIF_PROZENT
                and werte.get(ANZEIGE_BASIS) == "true"):
            return True
        _LETZTE_ABWEICHUNG = ("Schalter=%r, Tarif=%r (erwartet 'true' / %r)"
                              % (werte.get(ANZEIGE_BASIS), werte.get("adRate"),
                                 config.ANZEIGENTARIF_PROZENT))
        return False
    _step(warnings, "Angebot bewerben (%s%%)" % config.ANZEIGENTARIF_PROZENT,
          promote_2_percent, bewerben_kontrolle)

    # --- Rücknahme prüfen (eBay merkt sie sich am Konto) ---
    def enable_returns():
        """Hier wird bewusst NICHT mehr geklickt — nur kontrolliert.

        Bis zum 2026-08-09 führte der Weg über die Karte „Details zur
        Lieferung" in einen Dialog mit Schalter, Frist und Rückversandzahler.
        Beides gibt es nicht mehr: `button.se-field-card__body` liefert null
        Treffer, und `returnPolicy`/`returnDuration`/`returnShippingPayer`
        stehen nicht mehr im Formular.

        eBay merkt sich die Rücknahme stattdessen am Verkäuferkonto. Am frisch
        angelegten Entwurf stand dort bereits genau die Vorgabe des Nutzers:
        „Akzeptiert innerhalb von 14 Tage / Käufer zahlt den Rückversand" und
        „Keine internationale Rücknahme".

        Einen Editor anzusteuern, den niemand ausgemessen hat, wäre geraten —
        und die Rücknahme ist rechtlich bindend. Deshalb: prüfen und, wenn es
        nicht stimmt, den Nutzer mit dem tatsächlichen Wortlaut darauf stoßen.
        """
        return

    def ruecknahme_kontrolle():
        global _LETZTE_ABWEICHUNG
        text = _ruecknahme_text(page)
        if not text:
            _LETZTE_ABWEICHUNG = "keine Rücknahme-Anzeige im Formular gefunden"
            return False
        fehlt = []
        if "%d tage" % config.RUECKNAHME_TAGE not in text:
            fehlt.append("%d Tage" % config.RUECKNAHME_TAGE)
        if config.RUECKVERSAND_ZAHLT_KAEUFER and "käufer zahlt" not in text:
            fehlt.append("Käufer zahlt Rückversand")
        if "keine internationale rücknahme" not in text:
            fehlt.append("keine internationale Rücknahme")
        if not fehlt:
            return True
        _LETZTE_ABWEICHUNG = "es fehlt: %s — im Entwurf steht: %s" % (
            ", ".join(fehlt), text[:120])
        return False
    _step(warnings, "Rücknahme", enable_returns, ruecknahme_kontrolle)

    # --- Kein internationaler Versand ---
    # Vorgabe des Nutzers. Der Schalter hieß bis zum 2026-08-09
    # `isInternationalShippingOn`; jetzt heißt er `intlShippingServicePref`
    # und sitzt im Abschnitt „Details zur Lieferung". Unter dem alten Namen
    # war er schlicht nicht auffindbar, und der Schritt meldete Handarbeit.
    def kein_auslandsversand():
        schalter = page.locator(
            "input[role='switch'][name='intlShippingServicePref']").first
        if not schalter.count():
            raise RuntimeError("Schalter 'Internationaler Versand' nicht gefunden")
        if not schalter.is_checked():
            return  # steht schon aus
        schalter.scroll_into_view_if_needed(timeout=8000)
        schalter.uncheck(force=True, timeout=8000)
        _pause(page, 1500)
    _step(warnings, "Kein internationaler Versand", kein_auslandsversand,
          lambda: _felder_stimmen(page, intlShippingServicePref="false"))

    # --- Versandkosten (DHL-Stufe) ---
    def set_shipping():
        # **Umbau vom 2026-08-09.** Der Umweg über „Versandkosten bearbeiten"
        # und einen Dialog ist weg; der Betrag steht direkt im Formular:
        # `input[name='domesticShippingPrice1']`.
        #
        # Wichtig: Das Feld ist am frisch angelegten Entwurf **vorbelegt** —
        # mit dem Versandpreis des zuletzt eingestellten Artikels (gemessen:
        # 23,99 €). Wer es nicht überschreibt, verkauft zum falschen
        # Versandpreis, und niemand sieht es dem Entwurf an.
        #
        # Bewusst ohne Default beim Lesen des Preises: fehlt der Wert, soll
        # das als Warnung auffallen, statt stillschweigend 0,00 € einzutragen.
        preis = _betrag(listing["versandpreis"])
        feld = page.locator("input[name='domesticShippingPrice1']").first
        if not feld.count():
            raise RuntimeError("Versandkostenfeld 'domesticShippingPrice1' "
                               "nicht gefunden")
        feld.scroll_into_view_if_needed(timeout=8000)
        _dialoge_schliessen(page)
        feld.click(timeout=6000)
        feld.fill("")
        feld.press_sequentially(preis, delay=110)
        page.keyboard.press("Tab")
        _pause(page, 1800)

    def versand_kontrolle():
        # Gegen das Feld selbst prüfen, nicht gegen den Abschnittstext. Die
        # frühere Kontrolle suchte den Betrag im Text unter „Wer zahlt" — der
        # stand dort auch dann, wenn er aus einem fremden Angebot stammte.
        return _felder_stimmen(
            page, domesticShippingPrice1=_betrag(listing["versandpreis"]))
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
    _pause(page, 4000)

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
