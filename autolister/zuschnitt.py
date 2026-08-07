"""Fotos automatisch auf das Teil zuschneiden.

Warum lokal und nicht in eBays Foto-Editor: Zuschneiden ist reine
Bildrechnung, dafür braucht es keine zwanzig Klicks in einem Formular, das
sich jederzeit ändern kann. Und der Zuschnitt ist hier nachvollziehbar,
prüfbar und jederzeit abschaltbar.

**Nur die Upload-Kopien werden beschnitten, nie die Originale.** Die liegen
unverändert in `Erledigt/`. Und der Zuschnitt passiert *nach* der
Texterkennung — die arbeitet auf den Originalen, ihr fehlt also nichts.

Das Verfahren kommt ohne KI aus und kostet nichts:

  1. Hintergrundfarbe aus dem Bildrand schätzen (Tisch, Boden, Werkbank)
  2. alles markieren, was deutlich davon abweicht — das ist das Teil
  3. Ausreißer wegputzen und den umschließenden Kasten bilden
  4. großzügigen Rand dazugeben und quadratisch aufziehen (eBay zeigt
     Vorschaubilder quadratisch)

**Im Zweifel wird nicht geschnitten.** Sieht das Ergebnis unplausibel aus —
der Kasten füllt fast das ganze Bild oder nur einen Krümel —, bleibt das Foto,
wie es war. Ein zu eng geschnittenes Teil im Inserat ist schlimmer als ein
ungeschnittenes.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageFilter

log = logging.getLogger("autolister")

# Zuschneiden lässt sich abschalten, falls es bei ungewöhnlichen Fotos stört.
AKTIV = os.environ.get("AUTOLISTER_ZUSCHNEIDEN", "1") != "0"

# Ab dieser Farbabweichung (0..255) gilt ein Bildpunkt als "gehört zum Teil".
# 34 ist bewusst unempfindlich: Schattenwurf auf hellem Holz liegt darunter,
# ein schwarzes Steuergerät weit darüber.
SCHWELLE = int(os.environ.get("AUTOLISTER_ZUSCHNITT_SCHWELLE", "34"))

# Rand um das erkannte Teil, als Anteil der Kastenkante. Lieber etwas mehr:
# Ein knapp geschnittenes Teil wirkt gedrängt, und die Erkennung liegt am
# Rand naturgemäß am unsichersten.
RAND = 0.12

# Plausibilitätsgrenzen: Der Kasten muss zwischen 4 % und 92 % der Bildfläche
# einnehmen. Darunter ist es Bildrauschen, darüber lohnt der Zuschnitt nicht
# und die Gefahr steigt, dass in Wahrheit der Hintergrund erkannt wurde.
MIN_ANTEIL = 0.04
MAX_ANTEIL = 0.92

# Auf dieser Kantenlänge wird gerechnet — schnell und robust gegen Rauschen.
ANALYSE_KANTE = 400


def _hintergrundfarbe(klein: Image.Image) -> Tuple[int, int, int]:
    """Hintergrund aus dem Bildrand schätzen.

    Das Teil liegt praktisch immer in der Bildmitte; der Rand zeigt Tisch oder
    Boden. Genommen wird der Median je Farbkanal — der ist unempfindlich
    gegen einzelne Ausreißer wie eine Hand oder eine Kante am Bildrand.
    """
    breite, hoehe = klein.size
    dicke = max(2, min(breite, hoehe) // 12)
    streifen = [
        klein.crop((0, 0, breite, dicke)),                 # oben
        klein.crop((0, hoehe - dicke, breite, hoehe)),     # unten
        klein.crop((0, 0, dicke, hoehe)),                  # links
        klein.crop((breite - dicke, 0, breite, hoehe)),    # rechts
    ]
    punkte = []
    for teil in streifen:
        punkte.extend(teil.getdata())
    if not punkte:
        return (255, 255, 255)
    return tuple(  # type: ignore[return-value]
        sorted(p[kanal] for p in punkte)[len(punkte) // 2] for kanal in range(3))


def _kasten_aus_dichte(maske: Image.Image) -> Optional[Tuple[int, int, int, int]]:
    """Kasten über die Zeilen- und Spaltendichte bestimmen.

    **Nicht `getbbox()` verwenden.** Das nimmt die äußersten markierten Punkte,
    und ein paar Streupunkte in den Ecken — Holzmaserung, Schattenkante,
    Bildrauschen — ziehen den Kasten auf das ganze Bild auf. An echten Fotos
    gemessen (2026-08-07): Kasten 98–100 % der Fläche, während nur 32–55 % der
    Punkte wirklich zum Teil gehörten. Fünf von acht Fotos fielen deshalb
    durch die Plausibilitätsprüfung.

    Stattdessen wird je Zeile und Spalte gezählt, wie viel markiert ist, und
    nur der Bereich behalten, in dem diese Dichte deutlich über null liegt.
    Einzelne Streupunkte fallen dabei unter die Schwelle.
    """
    breite, hoehe = maske.size
    punkte = list(maske.getdata())

    zeilen = [sum(1 for x in range(breite) if punkte[y * breite + x])
              for y in range(hoehe)]
    spalten = [sum(1 for y in range(hoehe) if punkte[y * breite + x])
               for x in range(breite)]

    def spanne(werte):
        hoechste = max(werte) if werte else 0
        if hoechste <= 0:
            return None
        grenze = hoechste * 0.18       # 18 % der dichtesten Zeile/Spalte
        treffer = [i for i, w in enumerate(werte) if w >= grenze]
        return (treffer[0], treffer[-1] + 1) if treffer else None

    senkrecht, waagerecht = spanne(zeilen), spanne(spalten)
    if not senkrecht or not waagerecht:
        return None
    return (waagerecht[0], senkrecht[0], waagerecht[1], senkrecht[1])


def finde_kasten(bild: Image.Image) -> Optional[Tuple[int, int, int, int]]:
    """Den umschließenden Kasten des Teils finden — oder None.

    None heißt: nicht sicher genug, Finger weg vom Original.
    """
    klein = bild.convert("RGB").copy()
    klein.thumbnail((ANALYSE_KANTE, ANALYSE_KANTE), Image.BILINEAR)
    hintergrund = _hintergrundfarbe(klein)

    # Maske: Abstand zur Hintergrundfarbe, als Graustufenbild
    maske = Image.new("L", klein.size)
    maske.putdata([
        255 if (abs(p[0] - hintergrund[0]) + abs(p[1] - hintergrund[1])
                + abs(p[2] - hintergrund[2])) // 3 > SCHWELLE else 0
        for p in klein.getdata()
    ])
    # Einzelne Störpunkte wegputzen (Maserung im Holz, Bildrauschen)
    maske = maske.filter(ImageFilter.MedianFilter(size=5))

    kasten = _kasten_aus_dichte(maske)
    if not kasten:
        return None

    flaeche = (kasten[2] - kasten[0]) * (kasten[3] - kasten[1])
    anteil = flaeche / float(klein.size[0] * klein.size[1])
    if not (MIN_ANTEIL <= anteil <= MAX_ANTEIL):
        return None

    # Zurückrechnen auf die Originalgröße
    faktor = bild.size[0] / float(klein.size[0])
    return tuple(int(round(wert * faktor)) for wert in kasten)  # type: ignore[return-value]


def _quadratisch(kasten, bildgroesse) -> Tuple[int, int, int, int]:
    """Kasten mit Rand versehen und quadratisch aufziehen.

    eBay zeigt Vorschaubilder quadratisch. Wer nicht quadratisch liefert,
    überlässt eBay den Beschnitt — und das trifft dann gern die Kante des
    Teils.
    """
    links, oben, rechts, unten = kasten
    breite, hoehe = rechts - links, unten - oben
    rand = int(max(breite, hoehe) * RAND)
    kante = max(breite, hoehe) + 2 * rand

    if kante > min(bildgroesse):
        # Das Quadrat passt nicht ins Bild. **Dann lieber nicht quadratisch.**
        #
        # Vorher wurde die Kante auf die kurze Bildseite gestutzt — und das
        # schnitt die lange Achse des Teils an. An einem Differential-Foto vom
        # 2026-08-07 gut zu sehen: Das Teil ragte anschließend über den
        # Bildrand hinaus. Ein angeschnittenes Teil im Inserat ist schlimmer
        # als ein nicht ganz quadratisches Bild.
        return (max(0, links - rand), max(0, oben - rand),
                min(bildgroesse[0], rechts + rand),
                min(bildgroesse[1], unten + rand))

    mitte_x, mitte_y = (links + rechts) // 2, (oben + unten) // 2
    x = min(max(0, mitte_x - kante // 2), bildgroesse[0] - kante)
    y = min(max(0, mitte_y - kante // 2), bildgroesse[1] - kante)
    return (x, y, x + kante, y + kante)


def zuschneiden(bild: Image.Image) -> Tuple[Image.Image, bool]:
    """Bild auf das Teil zuschneiden. Gibt (Bild, wurde_geschnitten) zurück."""
    if not AKTIV:
        return bild, False
    try:
        kasten = finde_kasten(bild)
    except Exception as fehler:  # noqa: BLE001 — Zuschnitt darf nie den Lauf kippen
        log.debug("Zuschnitt fehlgeschlagen: %s", fehler)
        return bild, False
    if not kasten:
        return bild, False
    return bild.crop(_quadratisch(kasten, bild.size)), True
