"""Entwurfsdaten ohne KI aus den eBay-Vergleichstiteln ableiten.

Die Idee: Wer dasselbe Teil verkauft, schreibt es in den Titel. Aus 20
Angeboten zur selben Teilenummer lässt sich der übliche Teilname und die
passenden Modellcodes einfach auszählen — dafür braucht es kein Sprachmodell.

Beispiel aus dem Echtbetrieb (Teilenummer A2053510005):
    "Mercedes-Benz C W205 2017 Hinterachsdifferential A2053510005 Benzin"
    "MERCEDES C-KLASSE W205 S205 Differential Differenzial hinten A2053510005"
    "Mercedes W205 S205 200d Hinterachsgetriebe 2,47 Differential A2053510005"
  -> Teilname   "Hinterachsdifferential"  (häufigstes Sachwort)
  -> Modellcodes "W205 S205"              (häufigste Baureihenkürzel)
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional

# Wörter, die in Titeln stehen, aber nichts über das Teil aussagen
STOPPWOERTER = {
    "original", "originale", "originaler", "orig", "oem", "neu", "neuwertig",
    "gebraucht", "top", "gut", "sehr", "und", "oder", "für", "fuer", "mit",
    "ohne", "von", "aus", "der", "die", "das", "den", "dem", "ein", "eine",
    "bj", "baujahr", "km", "nr", "teilenummer", "artikelnummer", "art",
    "benzin", "diesel", "automatik", "schaltgetriebe", "kombi", "limousine",
    "coupe", "cabrio", "avantgarde", "amg", "line", "paket", "eur", "inkl",
    "mwst", "versand", "garantie", "monate", "rechnung", "händler", "haendler",
    "mercedes", "benz", "audi", "vw", "volkswagen", "bmw", "porsche", "seat",
    "skoda", "opel", "ford", "klasse", "cklasse", "eklasse", "sklasse",
    "clase", "classe", "berlina", "modell", "modelo", "coupe",
    # Positionsangaben: stehen in fast jedem Titel, sagen aber nichts über die
    # Art des Teils. Ohne sie hier landete einmal "Hinten" als Teilname.
    "hinten", "vorne", "vorn", "links", "rechts", "hinter", "vorder",
    "hinteres", "hintere", "vorderes", "vordere", "oben", "unten",
    "front", "heck", "left", "right", "rear",
    # eBay-Bedientext und Versandfloskeln
    "geöffnet", "geoeffnet", "fenster", "tab", "wird", "neuem", "angebot",
}

# Baureihenkürzel: W205, S205, X253, 8T0, B8, F30, 4F, 3C ...
MODELLCODE = re.compile(r"\b(?:[A-Z]{1,2}\d{2,3}[A-Z]?|\d[A-Z]\d)\b")

# Einbaupositionen, wie sie in Titeln vorkommen
POSITIONEN = [
    ("vorne links", ("vorne links", "vorn links", "vl ")),
    ("vorne rechts", ("vorne rechts", "vorn rechts", "vr ")),
    ("hinten links", ("hinten links", "hl ")),
    ("hinten rechts", ("hinten rechts", "hr ")),
    ("vorne", ("vorne", "vorn", "front")),
    ("hinten", ("hinten", "heck", "hinterachs")),
    ("links", ("links", "left")),
    ("rechts", ("rechts", "right")),
]

# Versandstufe nach Stichwort im Teilnamen. Reihenfolge = Prüfreihenfolge,
# das erste passende Stichwort gewinnt. Im Zweifel die kleinere Stufe.
VERSAND_STICHWOERTER = [
    ("Spedition", ("tür", "tuer", "haube", "kotflügel", "kotfluegel", "sitz",
                   "motor", "getriebe", "differential", "differenzial",
                   "hinterachs", "vorderachs", "achse", "fahrwerk", "klappe",
                   "heckklappe", "dach", "rahmen", "träger komplett")),
    ("Groß", ("stoßstange", "stossstange", "stoßfänger", "stossfaenger",
              "türverkleidung", "tuerverkleidung", "verkleidung komplett",
              "armaturenbrett", "kühlerpaket", "kuehlerpaket", "tank")),
    ("Mittel", ("scheinwerfer", "spiegel", "rücklicht", "ruecklicht",
                "verkleidung", "grill", "kühler", "kuehler", "lüfter",
                "luefter", "airbag", "display", "steuergerät", "steuergeraet",
                "pumpe", "kompressor", "lenkrad")),
    ("Standard", ("halter", "sensor", "schalter", "clip", "leiste", "zierleiste",
                  "kappe", "deckel", "blende", "schraube", "dichtung", "relais",
                  "stecker", "kabel", "griff", "düse", "duese")),
]


def _woerter(titel: str) -> List[str]:
    return re.findall(r"[A-Za-zÄÖÜäöüß]{4,}", titel)


def vergleichbare_angebote(angebote: List[Dict], teilenummer: str) -> List[int]:
    """Indizes der Angebote, die dieselbe Teilenummer im Titel führen.

    Das ist der verlässlichste Filter, den es ohne KI gibt: Wer die Nummer
    hinschreibt, verkauft mit hoher Wahrscheinlichkeit genau dieses Teil.
    """
    nummer = teilenummer.lower().replace(" ", "")
    passend = [
        i for i, a in enumerate(angebote)
        if nummer in a["titel"].lower().replace(" ", "").replace("-", "")
    ]
    # Führt kaum ein Angebot die Nummer, ist der Filter zu streng — dann
    # lieber alle nehmen und über die Ausreißerkappung im Preis abfedern.
    return passend if len(passend) >= 3 else list(range(len(angebote)))


def teilname(angebote: List[Dict], indizes: List[int]) -> Optional[str]:
    """Häufigstes Sachwort in den Vergleichstiteln."""
    zaehler: Counter = Counter()
    for i in indizes:
        # je Angebot jedes Wort nur einmal werten, sonst gewinnen Wiederholungen
        for wort in {w.lower() for w in _woerter(angebote[i]["titel"])}:
            if wort in STOPPWOERTER:
                continue
            zaehler[wort] += 1
    if not zaehler:
        return None
    hoechste = max(zaehler.values())
    # Unter den ausreichend häufigen Wörtern das längste nehmen: deutsche
    # Komposita sind spezifischer, und danach suchen Käufer auch.
    # "Hinterachsdifferential" schlägt "Differenzial".
    schwelle = max(2, hoechste * 0.3)
    kandidaten = [w for w, n in zaehler.items() if n >= schwelle]
    if not kandidaten:
        kandidaten = list(zaehler)
    beste = max(kandidaten, key=lambda w: (len(w), zaehler[w]))
    return beste.capitalize()


def modellcodes(angebote: List[Dict], indizes: List[int], maximal: int = 3) -> str:
    """Häufigste Baureihenkürzel aus den Vergleichstiteln."""
    zaehler: Counter = Counter()
    for i in indizes:
        titel = angebote[i]["titel"].upper()
        for code in set(MODELLCODE.findall(titel)):
            # Jahreszahlen und Preise aussortieren
            if code.isdigit():
                continue
            zaehler[code] += 1
    haeufig = [c for c, n in zaehler.most_common(maximal * 2) if n >= 2]
    return " ".join(haeufig[:maximal])


def position(angebote: List[Dict], indizes: List[int]) -> Optional[str]:
    """Einbauposition aus den Titeln ableiten."""
    text = " ".join(angebote[i]["titel"].lower() for i in indizes)
    for name, stichwoerter in POSITIONEN:
        if any(s in text for s in stichwoerter):
            return name
    return None


def versandstufe(teil: Optional[str], zusatztext: str = "") -> str:
    """Versandstufe über Stichwörter schätzen; im Zweifel die kleinste."""
    text = ((teil or "") + " " + zusatztext).lower()
    for stufe, stichwoerter in VERSAND_STICHWOERTER:
        if any(s in text for s in stichwoerter):
            return stufe
    return "Standard"


def baue_titel(hersteller: Optional[str], codes: str, teil: Optional[str],
               pos: Optional[str], nummer: str, maximal: int = 80) -> str:
    """Titel nach der Vorgabe bauen und auf die Zeichengrenze kürzen.

    Gekürzt wird von hinten nach Wichtigkeit: zuerst die Position, dann
    Modellcodes. Teilenummer und Teilname bleiben immer erhalten — danach
    sucht der Käufer.
    """
    def zusammen(teile: List[Optional[str]]) -> str:
        return " ".join(t for t in teile if t)

    varianten = [
        ["Original", hersteller, codes, teil, pos, nummer],
        ["Original", hersteller, codes, teil, nummer],
        ["Original", hersteller, " ".join(codes.split()[:2]), teil, nummer],
        ["Original", hersteller, " ".join(codes.split()[:1]), teil, nummer],
        [hersteller, teil, nummer],
        [teil, nummer],
    ]
    for variante in varianten:
        titel = zusammen(variante)
        if len(titel) <= maximal:
            return titel
    return zusammen([teil, nummer])[:maximal].rstrip()
