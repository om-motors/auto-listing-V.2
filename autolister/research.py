"""Schritt 2: Marktrecherche auf eBay.de — vergleichbare Angebote sammeln."""
from __future__ import annotations

import re
import urllib.parse
from typing import Dict, List

from . import config


def _query_variants(teilenummer: str, kompakt: str) -> List[str]:
    """Suchvarianten: mit/ohne Leerzeichen, mit/ohne Suffix-Buchstabe."""
    variants = []
    for q in (kompakt, teilenummer):
        if q and q not in variants:
            variants.append(q)
    # Suffix-Buchstabe am Ende entfernen (z.B. "8T0807284C" -> "8T0807284")
    m = re.match(r"^(.*\d)([A-Za-z]{1,2})$", kompakt or "")
    if m and m.group(1) not in variants:
        variants.append(m.group(1))
    return variants


def _parse_price(text: str) -> float:
    """'EUR 24,90' / '24,90 €' / '1.234,56' -> float. 0.0 wenn nicht lesbar."""
    m = re.search(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)", text or "")
    if not m:
        return 0.0
    return float(m.group(1).replace(".", "").replace(",", "."))


# Bedientext, den eBay in die Titel einbaut und der kein Teil des Titels ist
TITEL_MUELL = re.compile(
    r"\s*(wird in neuem fenster oder tab geöffnet"
    r"|opens in a new window or tab"
    r"|neues angebot|new listing|anzeige|sponsored)\s*",
    re.IGNORECASE,
)


def _clean_title(text: str) -> str:
    """eBay-Bedientext aus dem Titel entfernen.

    eBay hängt an jeden Treffer einen Hinweis für Screenreader an
    ("Wird in neuem Fenster oder Tab geöffnet"). Ungefiltert landet der im
    Titel und verfälscht die Auswertung — der Teilname wurde dadurch einmal
    als "Geöffnet" bestimmt.
    """
    erste_zeile = (text or "").split("\n")[0]
    return TITEL_MUELL.sub(" ", erste_zeile).strip()


def _extract_items(page) -> List[Dict]:
    """Titel + Preis aus der Trefferliste ziehen (mehrere Layout-Varianten)."""
    items = []
    # eBay fährt zwei Markups: klassisch li.s-item und neu .s-card
    for selector, title_sel, price_sel in (
        ("li.s-item", ".s-item__title", ".s-item__price"),
        (".s-card", ".s-card__title", ".s-card__price"),
    ):
        for el in page.query_selector_all(selector):
            title_el = el.query_selector(title_sel)
            price_el = el.query_selector(price_sel)
            if not title_el or not price_el:
                continue
            title = _clean_title(title_el.inner_text() or "")
            price = _parse_price(price_el.inner_text() or "")
            if not title or title.lower().startswith("shop on ebay") or price <= 0:
                continue
            items.append({"titel": title, "preis": price})
        if items:
            break
    return items


def _suche(page, query: str, verkauft: bool = False) -> List[Dict]:
    """Auf eBay.de suchen. `verkauft=True` liefert abgeschlossene Verkäufe."""
    url = (
        "https://www.ebay.de/sch/i.html?_nkw="
        + urllib.parse.quote(query)
        + "&LH_ItemCondition=3000&_sop=12"  # Gebraucht, beste Ergebnisse
    )
    if verkauft:
        url += "&LH_Sold=1&LH_Complete=1"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    # Verkaufte Artikel zeigt eBay nur eingeloggt. Ohne Login landet man auf
    # der Anmeldeseite — dann sofort abbrechen, statt auf Treffer zu warten,
    # die nie kommen (spart rund 12 Sekunden pro Suche).
    if "signin" in (page.url or "").lower():
        return []
    try:
        page.wait_for_selector("li.s-item, .s-card", timeout=12000)
    except Exception:
        pass
    return _extract_items(page)


def _nummer_im_titel(nummer: str, titel: str) -> bool:
    """Steht die Teilenummer wirklich im Titel?

    Verglichen wird ohne Leer- und Trennzeichen, damit "8K0 807 832 A" und
    "8K0807832A" als gleich gelten. Zusätzlich zählt die Nummer ohne
    Nachsatzbuchstaben, weil Verkäufer den oft weglassen.
    """
    sauber = re.sub(r"[^A-Z0-9]", "", titel.upper())
    nummer = re.sub(r"[^A-Z0-9]", "", nummer.upper())
    if nummer and nummer in sauber:
        return True
    ohne_suffix = re.match(r"^(.*\d)[A-Z]{1,2}$", nummer)
    return bool(ohne_suffix and ohne_suffix.group(1) in sauber)


def _mit_verkauften_ergaenzen(page, query: str, ergebnis: Dict) -> Dict:
    """Tatsächlich erzielte Verkaufspreise nachladen.

    Laufende Inserate zeigen Wunschpreise — auch solche, die sich seit Monaten
    nicht verkaufen. Abgeschlossene Verkäufe zeigen, was Käufer wirklich
    gezahlt haben, und sind damit die ehrlichere Grundlage für den eigenen
    Preis. Kostet nichts, ist nur ein zusätzlicher Suchparameter.
    """
    try:
        verkauft = _suche(page, query, verkauft=True)
    except Exception:
        verkauft = []
    ergebnis["verkaufte"] = verkauft[:25]
    return ergebnis


def search_comparables(page, teilenummer: str, kompakt: str) -> Dict:
    """Aktive gebrauchte Angebote für die Teilenummer suchen.

    Nutzt eine bereits geöffnete Playwright-Page. Gibt Angebote + verwendete
    Suchanfrage zurück.
    """
    for query in _query_variants(teilenummer, kompakt):
        items = _suche(page, query)
        if items:
            return _mit_verkauften_ergaenzen(
                page, query, {"query": query, "angebote": items[:25]})
    return {"query": teilenummer, "angebote": [], "verkaufte": []}


def pruefe_kandidaten(page, kandidaten) -> Dict:
    """eBay als kostenlosen Prüfstein für die Teilenummer benutzen.

    Eine falsch gelesene Ziffer liefert auf eBay keine oder kaum Treffer, eine
    echte Teilenummer dagegen sofort mehrere Angebote. Deshalb werden die
    Kandidaten der Reihe nach durchprobiert und der erste mit echten Treffern
    gewinnt — das korrigiert Lesefehler, die die Mustererkennung allein nicht
    auflösen kann.

    Rückgabe: {kandidat, query, angebote, geprueft}
    """
    gesucht = set()
    for kandidat in kandidaten[:config.KANDIDATEN_PRUEFEN]:
        for query in _query_variants(kandidat.formatiert, kandidat.nummer):
            if query in gesucht or len(gesucht) >= config.SUCHEN_MAXIMAL:
                continue          # dieselbe Suche nicht zweimal, und nicht endlos
            gesucht.add(query)
            items = _suche(page, query)
            # Nur Treffer zählen, die die Nummer wirklich im Titel führen.
            # Ohne diese Prüfung gilt jede Suche als Erfolg — eBay liefert
            # immer irgendetwas zurück, im Zweifel iMacs zu "5K0807032".
            passend = [
                i for i in items
                if _nummer_im_titel(kandidat.nummer, i["titel"])
            ]
            # EIN Treffer genügt. Dass eine 9- bis 11-stellige Teilenummer
            # zufällig in einem fremden Titel steht, ist praktisch
            # ausgeschlossen. Zwei zu verlangen ließ ein real existierendes
            # Audi-Teil durchfallen, das nur einmal angeboten war.
            if passend:
                return _mit_verkauften_ergaenzen(page, query, {
                    "kandidat": kandidat, "query": query,
                    "angebote": items[:25], "treffer_mit_nummer": len(passend),
                    "geprueft": True})
    return {"kandidat": kandidaten[0] if kandidaten else None,
            "query": "", "angebote": [], "verkaufte": [], "geprueft": False}


def bestaetige_kandidaten(page, kandidaten):
    """Eine OCR-Lesart nur dann als Gruppengrenze zulassen, wenn eBay sie kennt.

    Anders als `pruefe_kandidaten` wird hier keine Preisrecherche und keine
    Suche nach verkauften Artikeln gestartet. Die Gruppierung braucht nur die
    Ja/Nein-Antwort, ob die Nummer exakt in mindestens einem Titel vorkommt.
    """
    gesucht = set()
    for kandidat in kandidaten[:config.KANDIDATEN_PRUEFEN]:
        for query in _query_variants(kandidat.formatiert, kandidat.nummer):
            if query in gesucht or len(gesucht) >= config.SUCHEN_MAXIMAL:
                continue
            gesucht.add(query)
            items = _suche(page, query)
            if any(_nummer_im_titel(kandidat.nummer, i["titel"]) for i in items):
                return kandidat
    return None
