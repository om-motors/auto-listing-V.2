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
            title = (title_el.inner_text() or "").strip()
            price = _parse_price(price_el.inner_text() or "")
            if not title or title.lower().startswith("shop on ebay") or price <= 0:
                continue
            items.append({"titel": title, "preis": price})
        if items:
            break
    return items


def search_comparables(page, teilenummer: str, kompakt: str) -> Dict:
    """Aktive gebrauchte Angebote für die Teilenummer suchen.

    Nutzt eine bereits geöffnete Playwright-Page. Gibt Angebote + verwendete
    Suchanfrage zurück.
    """
    for query in _query_variants(teilenummer, kompakt):
        url = (
            "https://www.ebay.de/sch/i.html?_nkw="
            + urllib.parse.quote(query)
            + "&LH_ItemCondition=3000&_sop=12"  # Gebraucht, beste Ergebnisse
        )
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        items = _extract_items(page)
        if items:
            return {"query": query, "angebote": items[:25]}
    return {"query": teilenummer, "angebote": []}
