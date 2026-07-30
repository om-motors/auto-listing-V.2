"""Teilenummern aus erkanntem Text herausfiltern — ohne KI, rein über Muster.

Die deutschen Hersteller haben feste Nummernformate. Das reicht, um aus dem
Textsalat der Texterkennung die richtige Nummer zu fischen und gleich den
Hersteller mitzubestimmen.

    Mercedes-Benz   A 205 351 00 05      A + 10 Ziffern
    Audi / VW       8T0 807 284 C        Ziffer-Buchstabe-Ziffer + 6 Ziffern + optional Buchstabe
    BMW             7 123 456            7-8 Ziffern, oft mit führender 6/7/8
    Porsche         958 501 021 A        3+3+3 Ziffern + optional Buchstabe

Typische Lesefehler der Texterkennung werden vorher begradigt: bei
eingestanzten Nummern verschwimmen O/0, I/1 und B/8 regelmäßig.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Wörter, die auf den Hersteller hinweisen (auf dem Teil aufgedruckt/gestanzt)
HERSTELLER_WOERTER = {
    "MERCEDES": "Mercedes-Benz", "BENZ": "Mercedes-Benz", "DAIMLER": "Mercedes-Benz",
    "AUDI": "Audi", "VW": "VW", "VOLKSWAGEN": "VW", "SKODA": "Skoda", "SEAT": "Seat",
    "BMW": "BMW", "PORSCHE": "Porsche", "OPEL": "Opel", "FORD": "Ford",
}

# Störtext, der nie eine Teilenummer ist
AUSSCHLUSS = re.compile(
    r"EN[-\s]?AC|DIN|ISO|MADE\s*IN|GERMANY|GMBH|^\d{1,2}[.,]\d{1,3}$",
    re.IGNORECASE,
)


@dataclass
class Kandidat:
    nummer: str          # normiert, ohne Leerzeichen (z.B. A2053510005)
    formatiert: str      # wie auf dem Teil (z.B. A 205 351 00 05)
    hersteller: Optional[str]
    punkte: float        # höher = wahrscheinlicher die echte Teilenummer
    quelle: str          # Originaltext der Texterkennung


def _begradigen(text: str) -> str:
    """Typische Lesefehler bei gestanzten Zeichen korrigieren.

    Die Texterkennung setzt zwischen Zifferngruppen gern Satzzeichen, wo auf
    dem Bauteil nur eine Lücke ist — aus "351 00" wird "351.00" oder "351:00".
    Außerdem wird das Herstellerlogo (Mercedes-Stern, VW-Zeichen) regelmäßig
    als ©, ®, @, • oder S gelesen.
    """
    for zeichen in "©®@•*'\"`":
        text = text.replace(zeichen, " ")
    for zeichen in ".,:;_/":
        text = text.replace(zeichen, " ")
    return text.replace("|", "1").replace("—", "-").replace("–", "-")


def _ziffern_reparieren(rohtext: str) -> str:
    """In einem reinen Zifferblock Buchstaben zurückübersetzen, die die
    Texterkennung typischerweise verwechselt (O->0, I->1, B->8, S->5)."""
    tabelle = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1",
                             "B": "8", "S": "5", "Z": "2", "G": "6"})
    return rohtext.translate(tabelle)


# --- Herstellerspezifische Muster -------------------------------------------
# Jeweils: (Name, Regex, Punktebonus)
MUSTER: List[Tuple[str, re.Pattern, float]] = [
    # Mercedes: A + 10 Ziffern, meist als 3-3-2-2 gruppiert
    ("Mercedes-Benz",
     re.compile(r"\b[A]\s*(\d{3})\s*(\d{3})\s*(\d{2})\s*(\d{2})\b", re.IGNORECASE),
     3.0),
    # Audi/VW/Skoda/Seat: 3 + 3 + 3 Zeichen, letztes Feld darf Buchstaben haben
    ("Audi/VW",
     re.compile(r"\b(\d[A-Z0-9]\d)\s*(\d{3})\s*(\d{3})\s*([A-Z]{0,2})\b", re.IGNORECASE),
     2.5),
    # Porsche: 3-3-3 Ziffern + optionaler Buchstabe
    ("Porsche",
     re.compile(r"\b(\d{3})\s*(\d{3})\s*(\d{3})\s*([A-Z]?)\b", re.IGNORECASE),
     1.5),
    # BMW: 7-8 zusammenhängende Ziffern
    ("BMW",
     re.compile(r"\b([6-9]\d{6,7})\b"),
     1.0),
]


def _pruefe_mercedes(m: re.Match) -> Tuple[str, str]:
    ziffern = "".join(_ziffern_reparieren(g) for g in m.groups())
    return "A" + ziffern, "A %s %s %s %s" % m.groups()


def _pruefe_audi(m: re.Match) -> Tuple[str, str]:
    g = [x.upper() for x in m.groups()]
    kompakt = (g[0] + _ziffern_reparieren(g[1]) + _ziffern_reparieren(g[2]) + g[3])
    formatiert = " ".join(x for x in g if x)
    return kompakt, formatiert


def _pruefe_porsche(m: re.Match) -> Tuple[str, str]:
    g = [x.upper() for x in m.groups()]
    return "".join(g), " ".join(x for x in g if x)


def _pruefe_bmw(m: re.Match) -> Tuple[str, str]:
    return m.group(1), m.group(1)


PRUEFER = {
    "Mercedes-Benz": _pruefe_mercedes,
    "Audi/VW": _pruefe_audi,
    "Porsche": _pruefe_porsche,
    "BMW": _pruefe_bmw,
}


def hersteller_aus_text(texte: List[str]) -> Optional[str]:
    """Herstellername aus dem erkannten Text ableiten."""
    gross = " ".join(texte).upper()
    for wort, name in HERSTELLER_WOERTER.items():
        if wort in gross:
            return name
    return None


def finde_kandidaten(texte: List[Tuple[str, float]]) -> List[Kandidat]:
    """Alle plausiblen Teilenummern aus den Textfunden ziehen, beste zuerst."""
    marke = hersteller_aus_text([t for t, _ in texte])
    kandidaten: List[Kandidat] = []

    for rohtext, zuverlaessigkeit in texte:
        if AUSSCHLUSS.search(rohtext):
            continue
        text = _begradigen(rohtext)

        for name, muster, bonus in MUSTER:
            for treffer in muster.finditer(text):
                kompakt, formatiert = PRUEFER[name](treffer)
                if len(kompakt) < 6:
                    continue

                punkte = bonus + zuverlaessigkeit
                # Nummer, die den ganzen Textblock ausmacht, ist verlässlicher
                # als eine, die zufällig in einem längeren String steckt
                if len(treffer.group(0).strip()) >= len(text.strip()) * 0.7:
                    punkte += 1.0
                # Passt das Format zum aufgedruckten Herstellernamen?
                if marke and (marke in name or name in marke):
                    punkte += 1.5
                # Gruppierte Schreibweise spricht für eine echte Teilenummer
                if " " in treffer.group(0).strip():
                    punkte += 0.5

                zugehoerig = marke if (marke and (marke in name or name in marke)) else None
                if zugehoerig is None and name != "Audi/VW":
                    zugehoerig = name
                kandidaten.append(Kandidat(
                    nummer=kompakt, formatiert=formatiert.strip(),
                    hersteller=zugehoerig or marke, punkte=punkte, quelle=rohtext))

    # Mehrheitsentscheid: eine Nummer, die aus mehreren Fotos oder Drehungen
    # gleich gelesen wurde, ist verlässlicher als ein Einzelfund. Das trennt
    # die echte Nummer von Lesefehlern einzelner Ziffern (205 gegen 203).
    haeufigkeit: dict = {}
    for k in kandidaten:
        haeufigkeit[k.nummer] = haeufigkeit.get(k.nummer, 0) + 1

    beste: dict = {}
    for k in kandidaten:
        k.punkte += (haeufigkeit[k.nummer] - 1) * 1.2
        if k.nummer not in beste or k.punkte > beste[k.nummer].punkte:
            beste[k.nummer] = k
    return sorted(beste.values(), key=lambda k: -k.punkte)


def beste_nummer(texte: List[Tuple[str, float]]) -> Optional[Kandidat]:
    kandidaten = finde_kandidaten(texte)
    return kandidaten[0] if kandidaten else None
