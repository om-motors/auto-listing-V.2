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


# Die Texterkennung mischt bei gestanzten Zeichen die Alphabete: aus einem
# lateinischen K wird ein kyrillisches К, aus A ein А. Sieht gleich aus, ist
# aber ein anderer Zeichencode und lässt jedes Muster ins Leere laufen.
FREMDALPHABET = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M",
    "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y", "З": "3", "Ѕ": "S",
    "а": "a", "в": "B", "е": "e", "к": "K", "о": "o", "р": "p", "с": "c",
    "у": "y", "х": "x", "и": "N", "И": "N", "Α": "A", "Β": "B", "Ε": "E",
    "Κ": "K", "Μ": "M", "Ο": "O", "Ρ": "P", "Τ": "T", "Χ": "X",
})

# Zeichen, die auf gestanztem Metall regelmäßig verwechselt werden.
# Schlüssel = gelesenes Zeichen, Wert = plausible Ziffern, wahrscheinlichste
# zuerst. Gemessen an einem Audi-Träger: die 8 in "8K0" wurde je nach Foto
# als S, 3, З oder B gelesen, die 0 als O oder D, das Suffix-A als 4.
ZIFFER_ALTERNATIVEN = {
    "O": "0", "D": "0", "Q": "0", "o": "0",
    "I": "1", "l": "1", "i": "1",
    "Z": "2", "z": "2",
    "3": "38",   # eine 3 ist oft eine 8, der die linke Hälfte fehlt
    "A": "4",
    "S": "58",   # S kann 5 sein oder eine 8 mit offenen Bögen
    "s": "58",
    "G": "6", "b": "6",
    "T": "7",
    "B": "8", "&": "8",
    "g": "9", "q": "9",
}

# Umgekehrt: was in einer Buchstabenposition steht, aber als Ziffer gelesen wurde
BUCHSTABE_ALTERNATIVEN = {
    "4": "A", "0": "O", "1": "I", "5": "S", "8": "B", "6": "G", "2": "Z", "7": "T",
}


def _begradigen(text: str) -> str:
    """Typische Lesefehler bei gestanzten Zeichen korrigieren.

    Die Texterkennung setzt zwischen Zifferngruppen gern Satzzeichen, wo auf
    dem Bauteil nur eine Lücke ist — aus "351 00" wird "351.00" oder "351:00".
    Außerdem wird das Herstellerlogo (Mercedes-Stern, Audi-Ringe) regelmäßig
    als ©, ®, @, • oder S gelesen, und einzelne Zeichen landen im kyrillischen
    Alphabet.
    """
    text = text.translate(FREMDALPHABET)
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


def _moeglichkeiten(zeichen: str, als_ziffer: bool) -> str:
    """Plausible Interpretationen eines gelesenen Zeichens.

    Passt das Zeichen schon zur erwarteten Art (Ziffer bzw. Buchstabe), bleibt
    es wie es ist. Sonst werden die bekannten Verwechslungen angeboten —
    wahrscheinlichste zuerst.
    """
    if als_ziffer:
        if zeichen.isdigit():
            # Auch eine gelesene Ziffer kann daneben liegen: 3 ist oft eine 8
            return ZIFFER_ALTERNATIVEN.get(zeichen, zeichen)
        return ZIFFER_ALTERNATIVEN.get(zeichen, ZIFFER_ALTERNATIVEN.get(zeichen.upper(), ""))
    if zeichen.isalpha():
        return zeichen.upper()
    return BUCHSTABE_ALTERNATIVEN.get(zeichen, "")


def _varianten(block: str, muster: str, grenze: int = 8) -> List[str]:
    """Alle plausiblen Lesarten eines Blocks erzeugen.

    `muster` beschreibt je Stelle, was dort stehen muss: "z" = Ziffer,
    "b" = Buchstabe. Beispiel: die Audi-Typnummer "8K0" hat das Muster "zbz".

    Aus "SK0" wird damit ["5K0", "8K0"] — welche davon stimmt, entscheidet
    anschließend eBay in `research.pruefe_kandidaten()`.
    """
    if len(block) != len(muster):
        return []
    pro_stelle = []
    for zeichen, art in zip(block, muster):
        moeglich = _moeglichkeiten(zeichen, art == "z")
        if not moeglich:
            return []
        pro_stelle.append(moeglich)
    ergebnis = [""]
    for moeglich in pro_stelle:
        ergebnis = [vorher + z for vorher in ergebnis for z in moeglich]
        if len(ergebnis) > grenze * 4:
            ergebnis = ergebnis[:grenze * 4]
    return ergebnis[:grenze]


# --- Herstellerspezifische Muster -------------------------------------------
# Bewusst tolerant: die Zeichenart wird NICHT im Muster erzwungen, sondern
# hinterher aufgelöst. Sonst fällt "SK0 807 832 4" durchs Raster, obwohl dort
# klar "8K0 807 832 A" steht.
ZEICHEN = r"[0-9A-Za-z]"

MUSTER: List[Tuple[str, re.Pattern, float]] = [
    # Mercedes: A + 10 Ziffern, meist als 3-3-2-2 gruppiert
    ("Mercedes-Benz",
     re.compile(r"\bA\s*(%s{3})\s*(%s{3})\s*(%s{2})\s*(%s{2})\b"
                % (ZEICHEN, ZEICHEN, ZEICHEN, ZEICHEN), re.IGNORECASE),
     3.0),
    # Audi/VW/Skoda/Seat: Typnummer (Ziffer-Buchstabe-Ziffer) + 3 + 3 + Suffix
    ("Audi/VW",
     re.compile(r"\b(%s{3})\s*(%s{3})\s*(%s{3})\s*([0-9A-Za-z]{0,2})\b"
                % (ZEICHEN, ZEICHEN, ZEICHEN), re.IGNORECASE),
     2.5),
    # BMW: 7-8 zusammenhängende Ziffern
    ("BMW",
     re.compile(r"\b([6-9]\d{6,7})\b"),
     1.0),
]


def _pruefe_mercedes(m: re.Match) -> List[Tuple[str, str]]:
    gruppen = [_varianten(g, "z" * len(g)) for g in m.groups()]
    if not all(gruppen):
        return []
    ergebnis = []
    for a in gruppen[0][:2]:
        for b in gruppen[1][:2]:
            for c in gruppen[2][:2]:
                for d in gruppen[3][:2]:
                    ergebnis.append(("A" + a + b + c + d,
                                     "A %s %s %s %s" % (a, b, c, d)))
    return ergebnis[:8]


def _pruefe_audi(m: re.Match) -> List[Tuple[str, str]]:
    typ, haupt, unter, suffix = m.groups()
    # ⚠️ Der Typcode hat DREI Muster, nicht eines. Bis zum 14.08.2026 stand
    # hier nur "zbz" (8K0, 4H0, 3G0) — damit waren zwei ganze Familien
    # unerreichbar:
    #
    #   zbb   1EA, 5NA   (neuere VW/Audi)
    #   zzb   80A, 11A, 83A
    #
    # Eine Nummer aus diesen Familien konnte in der Kandidatenliste gar nicht
    # vorkommen, egal wie gut das Foto war. Der Fehlschlag sah dann aus wie
    # ein Leseproblem und war keines.
    typen = []
    for muster in ("zbz", "zbb", "zzb"):    # z.B. "SK0" -> "5K0", "8K0"
        for v in _varianten(typ, muster):
            if v not in typen:
                typen.append(v)
    hauptgruppen = _varianten(haupt, "zzz")
    untergruppen = _varianten(unter, "zzz")
    if not (typen and hauptgruppen and untergruppen):
        return []
    suffixe = _varianten(suffix, "b" * len(suffix)) if suffix else [""]
    if not suffixe:
        suffixe = [""]

    ergebnis = []
    for t in typen[:3]:
        for h in hauptgruppen[:2]:
            for u in untergruppen[:2]:
                for s in suffixe[:2]:
                    kompakt = t + h + u + s
                    formatiert = " ".join(x for x in (t, h, u, s) if x)
                    ergebnis.append((kompakt, formatiert))
    return ergebnis[:12]


def _pruefe_bmw(m: re.Match) -> List[Tuple[str, str]]:
    return [(m.group(1), m.group(1))]


PRUEFER = {
    "Mercedes-Benz": _pruefe_mercedes,
    "Audi/VW": _pruefe_audi,
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
                lesarten = PRUEFER[name](treffer)
                for rang, (kompakt, formatiert) in enumerate(lesarten):
                    if len(kompakt) < 6:
                        continue

                    punkte = bonus + zuverlaessigkeit
                    # Je weiter hinten in der Variantenliste, desto unwahr-
                    # scheinlicher die Lesart (erste = direkteste Deutung).
                    punkte -= rang * 0.35
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


def aus_vorgabe(text: str) -> Optional[Kandidat]:
    """Eine vom Nutzer vorgegebene Teilenummer übernehmen.

    Gedacht für den Fall, dass die Fotos die Nummer nicht hergeben: Der Nutzer
    benennt den Ordner (oder das Feld auf der Upload-Website) einfach nach der
    Teilenummer, und die wird dann ohne Raten verwendet.
    """
    roh = (text or "").strip()
    if len(roh) < 6:
        return None
    sauber = re.sub(r"[^A-Za-z0-9 ]", "", roh).strip()
    kompakt = sauber.replace(" ", "").upper()
    # Muss wie eine Teilenummer aussehen: Ziffern dominieren, keine Wörter
    if not re.fullmatch(r"[A-Z0-9]{6,17}", kompakt):
        return None
    if sum(c.isdigit() for c in kompakt) < 6:
        return None

    marke = None
    if kompakt.startswith("A") and len(kompakt) == 11 and kompakt[1:].isdigit():
        marke = "Mercedes-Benz"
    elif re.match(r"^\d[A-Z]\d\d{6}", kompakt):
        marke = "Audi/VW"
    return Kandidat(nummer=kompakt, formatiert=sauber.upper(), hersteller=marke,
                    punkte=99.0, quelle="vom Nutzer vorgegeben")
