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

# Baureihenkürzel: W205, S205, X253, 8T0, B8, F30, A5, Q7 ...
# Kurze Kürzel wie "A5" oder "B8" gehören dazu — ohne sie blieb der Titel
# eines Audi-Teils ohne jede Modellangabe.
MODELLCODE = re.compile(r"\b(?:[A-Z]{1,2}\d{2,3}[A-Z]?|\d[A-Z]\d|[A-Z]\d)\b")

# Markennamen, wie sie in eBay-Titeln stehen
MARKEN = {
    "MERCEDES": "Mercedes-Benz", "BENZ": "Mercedes-Benz", "AUDI": "Audi",
    "VOLKSWAGEN": "VW", "VW": "VW", "BMW": "BMW", "PORSCHE": "Porsche",
    "SKODA": "Skoda", "SEAT": "Seat", "OPEL": "Opel", "FORD": "Ford",
}

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
# Wörter, die ein Teil als Hülle/Anbauteil eines größeren Teils ausweisen.
# Steht so eines vorn, entscheidet es allein über die Versandstufe.
# Achtung: Nur als EIGENES Wort. Zusammengesetzte wie "Türverkleidung"
# bleiben unberührt und gehen weiterhin per Spedition.
HUELLWOERTER = ("abdeckung", "verkleidung", "blende", "rahmen", "halter",
                "leiste", "zierleiste", "kappe", "deckel", "gitter")

VERSAND_STICHWOERTER = [
    ("Spedition", ("tür", "tuer", "haube", "kotflügel", "kotfluegel", "sitz",
                   "motor", "getriebe", "differential", "differenzial",
                   "hinterachs", "vorderachs", "achse", "fahrwerk", "klappe",
                   "heckklappe", "dach", "rahmen", "träger komplett")),
    # Früher eine eigene Stufe "Groß" zu 79,90 € — laut Nutzervorgabe geht
    # alles Sperrige per Spedition zu 60 €, deshalb hier zusammengelegt.
    ("Spedition", ("stoßstange", "stossstange", "stoßfänger", "stossfaenger",
                   "türverkleidung", "tuerverkleidung", "verkleidung komplett",
                   "armaturenbrett", "kühlerpaket", "kuehlerpaket", "tank",
                   # Querträger/Aufprallträger sind gut einen Meter lang — die
                   # gingen anfangs als "Standard" durch, weil nur "halter" traf
                   "querträger", "quertraeger", "aufprallträger", "aufpralltraeger",
                   "prallträger", "pralltraeger", "stoßstangenträger",
                   "stossstangentraeger", "träger", "traeger", "schweller",
                   "auspuff", "endschalldämpfer", "endschalldaempfer")),
    ("Mittel", ("scheinwerfer", "spiegel", "rücklicht", "ruecklicht",
                "verkleidung", "grill", "kühler", "kuehler", "lüfter",
                "luefter", "airbag", "display", "kompressor", "lenkrad")),
    # "steuergerät" stand bis 2026-08-07 unter "Mittel" (23,99 €). Ein
    # Steuergerät passt aber in einen kleinen Karton, und die Vorgabe des
    # Nutzers beschreibt Mittel als "Scheinwerfer, Spiegel, größere
    # Verkleidungen". Aufgefallen ist es, als der Teilname 8K0907801J
    # richtigerweise zu "Steuergerät Feststellbremse" wurde und der Versand
    # dadurch von 7,69 € auf 23,99 € sprang — ohne dass sich am Teil etwas
    # geändert hätte. Ebenso "pumpe": eine Wasserpumpe ist ein Handteil.
    ("Standard", ("halter", "sensor", "schalter", "clip", "leiste", "zierleiste",
                  "kappe", "deckel", "blende", "schraube", "dichtung", "relais",
                  "stecker", "kabel", "griff", "düse", "duese",
                  "steuergerät", "steuergeraet", "pumpe", "ventil", "modul")),
]


def _woerter(titel: str) -> List[str]:
    return re.findall(r"[A-Za-zÄÖÜäöüß]{4,}", titel)


def _normiert(text: str) -> str:
    """Leer- und Trennzeichen weg, damit '8K0 907 801 J' == '8K0907801J'."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# Ab wie vielen Treffern die genaue Teilenummer für sich allein steht.
MINDEST_EXAKT = 3


def treffer_nach_nummer(angebote: List[Dict], teilenummer: str):
    """(genau, gelockert) — Indexlisten der Angebote zu dieser Teilenummer.

    **genau**: die Nummer steht vollständig im Titel, mit Nachsatzbuchstaben.
    **gelockert**: zusätzlich die Angebote, bei denen nur der Stamm ohne den
    Nachsatzbuchstaben passt — also andere Ausführungen desselben Teils.

    Die zweite Liste ist eine Notlösung, keine Verbesserung. Der
    Nachsatzbuchstabe unterscheidet echte Varianten, und die kosten
    unterschiedlich viel. Am Steuergerät `8K0907801J` gemessen (Bericht vom
    2026-08-07): von 23 Vergleichsangeboten führten nur 13 diese Nummer, die
    übrigen 10 waren `…801H`, `…801M`, `…801N`, `…801D`, `…801E`, `…801F`.
    Der Preis stieg dadurch von 22,90 € auf 24,90 €.
    """
    nummer = _normiert(teilenummer)
    ohne_suffix = re.match(r"^(.*\d)[a-z]{1,2}$", nummer)
    kurz = ohne_suffix.group(1) if ohne_suffix else None

    genau, gelockert = [], []
    for i, a in enumerate(angebote):
        titel = _normiert(a.get("titel", ""))
        if nummer and nummer in titel:
            genau.append(i)
            gelockert.append(i)
        elif kurz and kurz in titel:
            gelockert.append(i)
    return genau, gelockert


def vergleichbare_angebote(angebote: List[Dict], teilenummer: str,
                           mindestens: int = MINDEST_EXAKT) -> List[int]:
    """Indizes der Angebote, die dieselbe Teilenummer im Titel führen.

    Das ist der verlässlichste Filter, den es ohne KI gibt: Wer die Nummer
    hinschreibt, verkauft mit hoher Wahrscheinlichkeit genau dieses Teil.

    Wenige Treffer sind ein ehrliches Ergebnis, kein Grund zum Aufweichen.
    Eine frühere Fassung nahm bei unter drei Treffern *alle* Suchergebnisse —
    für einen Audi-Querträger (129 €) landeten so komplette Stoßstangen zu
    1450 € in der Preisbasis. Lieber ein dünner, richtiger Vergleich als ein
    breiter, falscher.

    Deshalb zählt die **genaue** Nummer zuerst. Erst wenn davon weniger als
    drei Angebote existieren, kommen die Varianten mit anderem
    Nachsatzbuchstaben dazu — sonst bliebe ein selten angebotenes Teil ganz
    ohne Preis. Ein Angebot vom 2026-07-30 zu `8K0807832A` hatte genau einen
    Vergleich, und der schrieb die Nummer ohne das `A`. Wer die Lockerung
    ersatzlos streicht, verliert diesen Preis.
    """
    genau, gelockert = treffer_nach_nummer(angebote, teilenummer)
    if len(genau) >= mindestens:
        return genau
    return gelockert


def fremde_nummern(angebote: List[Dict], indizes: List[int],
                   teilenummer: str) -> List[str]:
    """Welche abweichenden Varianten stehen in diesen Titeln?

    Für den Bericht: „mitgerechnet wurden auch 8K0907801N, 8K0907801H".
    Damit sieht der Nutzer, worauf der Preis wirklich beruht.
    """
    nummer = _normiert(teilenummer)
    ohne_suffix = re.match(r"^(.*\d)[a-z]{1,2}$", nummer)
    if not ohne_suffix:
        return []
    muster = re.compile(re.escape(ohne_suffix.group(1)) + r"[a-z]{0,2}")
    gefunden: List[str] = []
    for i in indizes:
        for treffer in muster.findall(_normiert(angebote[i].get("titel", ""))):
            gross = treffer.upper()
            if treffer != nummer and gross not in gefunden:
                gefunden.append(gross)
    return gefunden


# Wörter, die vor den eigentlichen Teilnamen gehören, wenn die Vergleichstitel
# sie mittragen. Bewusst kurz gehalten und auf Gerätearten beschränkt: Wörter
# wie „Halter" oder „Blende" hier aufzunehmen würde vor fast jedes Teil etwas
# schreiben.
GERAETEWOERTER = ("steuergerät", "steuergeraet", "sensor", "schalter",
                  "relais", "pumpe", "ventil", "modul",
                  # Auch Hüllteile brauchen das Bestimmungswort: 8K0857085B
                  # hieß im Inserat schlicht "Armaturenbrett" — verkauft wurde
                  # aber die **Abdeckung** dafür, ein Kunststoffteil für 19 €.
                  # Wer ein Armaturenbrett sucht, erwartet etwas anderes.
                  "abdeckung", "verkleidung", "blende", "rahmen", "halter")


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

    # Gerätewort voranstellen, wenn es die Vergleichstitel deutlich mittragen.
    #
    # Das längste Wort allein reicht bei Elektronik nicht: Für 8K0907801J
    # lieferten die Titel „Steuergerät Feststellbremse", und weil
    # „Feststellbremse" (15 Zeichen) länger ist als „Steuergerät" (11), hieß
    # das Teil im Inserat schlicht „Feststellbremse" — also die Bremse selbst
    # statt ihres Steuergeräts. Ein Käufer, der danach sucht, findet das Teil
    # nicht, und wer es kauft, erwartet etwas anderes.
    haeufig = max(2, hoechste * 0.5)
    for wort in GERAETEWOERTER:
        if zaehler.get(wort, 0) >= haeufig and wort not in beste:
            return "%s %s" % (wort.capitalize(), beste.capitalize())
    return beste.capitalize()


def hersteller(angebote: List[Dict], indizes: List[int]) -> Optional[str]:
    """Marke aus den Vergleichstiteln ableiten.

    Nötig, weil auf vielen Teilen nur das Logo steht und kein Markenname —
    die Audi-Ringe liest keine Texterkennung als "Audi". In den eBay-Titeln
    steht die Marke dagegen praktisch immer.
    """
    zaehler: Counter = Counter()
    for i in indizes:
        gross = angebote[i]["titel"].upper()
        for wort, name in MARKEN.items():
            if re.search(r"\b%s\b" % wort, gross):
                zaehler[name] += 1
    return zaehler.most_common(1)[0][0] if zaehler else None


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
    # Bei wenigen Vergleichsangeboten kann nichts zweimal vorkommen — dann
    # genügt ein Fund, sonst bliebe der Titel ganz ohne Modellangabe.
    schwelle = 2 if len(indizes) >= 3 else 1
    haeufig = [c for c, n in zaehler.most_common(maximal * 2) if n >= schwelle]
    return " ".join(haeufig[:maximal])


def position(angebote: List[Dict], indizes: List[int]) -> Optional[str]:
    """Einbauposition aus den Titeln ableiten."""
    text = " ".join(angebote[i]["titel"].lower() for i in indizes)
    for name, stichwoerter in POSITIONEN:
        if any(s in text for s in stichwoerter):
            return name
    return None


def _stufe_aus_text(text: str) -> Optional[str]:
    """Erstes passendes Stichwort gewinnt (Liste läuft von groß nach klein)."""
    text = (text or "").lower()
    for stufe, stichwoerter in VERSAND_STICHWOERTER:
        if any(s in text for s in stichwoerter):
            return stufe
    return None


def versandstufe(teil: Optional[str], zusatztext: str = "") -> str:
    """Versandstufe über Stichwörter schätzen; im Zweifel die kleinste.

    **Der Teilname entscheidet allein.** Die Titel der Vergleichsangebote
    kommen erst zum Zug, wenn der Teilname gar kein Stichwort enthält.

    Vorher lagen beide in einem String, und weil die Stufenliste von groß nach
    klein läuft, schlug ein einzelnes Wort aus einem fremden Titel den echten
    Teilnamen. Im Echtlauf am 2026-08-01 bekam eine **Sonnenblende** dadurch
    die Stufe „Mittel" (23,99 €) statt „Standard" (7,69 €) — ein „Spiegel" in
    einem Vergleichstitel genügte. Bei 29,90 € Artikelpreis kostet ein solcher
    Aufschlag Verkäufe.

    Zusätzlich entscheidet **das erste Wort des Teilnamens** vor dem Rest.
    Deutsche Teilebezeichnungen stellen das eigentliche Teil voran und hängen
    an, wofür es ist: ein „Halter Stoßfänger" ist ein Halter (Standard, 7,69 €),
    keine Stoßstange (Spedition, 60 €). Ohne diese Regel gewann „stoßfänger",
    weil die Stufenliste von groß nach klein läuft — und CLAUDE.md führt
    „Halter" ausdrücklich unter Standard.
    """
    kopf = (teil or "").strip().split()
    erstes = kopf[0].lower() if kopf else ""

    # **Hüllteile erben die Größe ihres Bezugsteils nicht.** Eine „Abdeckung
    # Armaturenbrett" ist ein Kunststoffteil für 19 €, kein Armaturenbrett.
    # Ohne diese Regel zog das Wort „armaturenbrett" im Namen die Stufe auf
    # Spedition (60 €) — derselbe Fehler wie beim Schmutzfänger am
    # 2026-08-07, nur andersherum eingefädelt. Das folgende Hauptwort darf
    # also nicht mehr hochstufen; es entscheidet allein das Hüllwort.
    if erstes in HUELLWOERTER:
        return _stufe_aus_text(erstes) or "Standard"

    aus_teil = _stufe_aus_text(erstes) or _stufe_aus_text(teil)
    if aus_teil:
        return aus_teil

    # **Ein bekannter Teilname ohne Treffer ist selbst eine Aussage.** Die
    # Stichwortliste führt die sperrigen Teile auf — Tür, Haube, Kotflügel,
    # Stoßstange, Träger, Sitz, Motor. Steht der Teilname dort nicht drin, ist
    # das ein Hinweis auf ein kleines Teil, kein Zweifelsfall. Fremde
    # Vergleichstitel dürfen dann gar nicht mitreden.
    #
    # Am 2026-08-02 bekam ein **Schmutzfänger** für 38,90 € über genau diesen
    # Weg die Stufe „Spedition" zu 60 € — aus den Vergleichstiteln, in denen
    # „Kotflügel" und „Träger" vorkamen. Bei dem Preis verkauft sich nichts.
    if (teil or "").strip():
        return "Standard"

    # Nur wenn gar kein Teilname vorliegt (Texterkennung ohne Ergebnis), helfen
    # die Vergleichstitel aus — dann die **kleinste** gefundene Stufe, gemäß
    # der Vorgabe aus CLAUDE.md: „Im Zweifel die kleinste Stufe wählen."
    text = (zusatztext or "").lower()
    gefunden = {stufe for stufe, worte in VERSAND_STICHWOERTER
                if any(w in text for w in worte)}
    if not gefunden:
        return "Standard"
    rang = {"Standard": 0, "Mittel": 1, "Spedition": 2}
    return min(gefunden, key=lambda s: rang.get(s, 99))


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
