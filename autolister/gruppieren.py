"""Fotos mehrerer Teile automatisch auseinandersortieren.

Der Nutzer lädt alle Fotos eines Rundgangs auf einmal hoch — Schmutzfänger,
Feststellbremse, Armaturenbrett — und das Programm soll selbst erkennen,
welche Bilder zu welchem Teil gehören.

**Maßgeblich ist die Teilenummer**, nicht die Aufnahmezeit.

Ursprünglich lief das über die EXIF-Aufnahmezeit: Wer ein Teil fotografiert,
macht drei, vier Bilder in Sekunden und braucht dann eine halbe Minute bis zum
nächsten — diese Pausen wären die Trennlinien. **Das scheitert am
Handy-Upload.** Am 2026-08-07 nachgemessen: iOS streift beim Hochladen über
ein `<input type=file>` sämtliche EXIF-Daten ab. Alle neun Fotos eines Uploads
trugen nur noch die Downloadzeit im Sekundenabstand, und alle drei Teile
landeten in einem einzigen Inserat.

Die Teilenummer überlebt das, denn sie steht auf dem Teil selbst. Der Einwand
gegen dieses Verfahren war, dass sie nur auf ein, zwei Bildern je Teil zu
sehen ist — das stimmt, ist aber lösbar: Die Fotos mit Nummer sind die
Ankerpunkte, und jedes Übersichtsbild kommt zu dem Ankerpunkt, der ihm in der
Reihenfolge am nächsten liegt.

Die Zeitgruppierung bleibt als zweiter Weg erhalten. Sie greift bei Fotos, die
direkt vom Mac kommen — dort sind die EXIF-Daten noch da.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from pathlib import Path
from typing import List, Optional

from . import ocr

log = logging.getLogger("autolister")


class GruppierungUnsicher(RuntimeError):
    """Der App-Auftrag darf noch keinen eBay-Entwurf erzeugen."""


def fuer_app(fotos: List[Path], kandidat_bestaetigen) -> List[List[Path]]:
    """App-Fotos ueber bestaetigte Nummernbilder sicher gruppieren.

    Vertrag mit dem Mitarbeiter: Die Fotos eines Produkts werden
    hintereinander aufgenommen, die Teilenummer kommt immer zuletzt. Eine
    Nummer darf die Gruppe nur schliessen, wenn `kandidat_bestaetigen` sie
    gegen eBay bestaetigt. Damit erfinden Zuliefereraufkleber und Warnschilder
    keine zusaetzlichen Produkte.

    Der Auftrag wird fail-closed behandelt: Ohne bestaetigte Nummer oder mit
    Fotos hinter dem letzten Nummernbild entsteht kein Entwurf.
    """
    from . import partnumber

    if not fotos:
        raise GruppierungUnsicher("Der Auftrag enthält keine Fotos.")
    try:
        je_foto = ocr.lies_fotos_einzeln(fotos, gruendlich=False)
    except Exception as fehler:  # noqa: BLE001 - wird als Auftragsfehler gezeigt
        raise GruppierungUnsicher(
            "Die Nummernbilder konnten nicht gelesen werden: %s" % fehler) from fehler

    anker = []
    cache = {}
    for i, texte in enumerate(je_foto):
        kandidaten = partnumber.finde_kandidaten(texte) if texte else []
        if not kandidaten:
            continue
        schluessel = tuple(k.nummer for k in kandidaten)
        if schluessel not in cache:
            cache[schluessel] = kandidat_bestaetigen(kandidaten)
        bestaetigt = cache[schluessel]
        if bestaetigt:
            nummer = bestaetigt.nummer
            # Dieselbe Teilenummer kann schon auf einem Uebersichtsbild und
            # danach nochmals auf der gewollten Nahaufnahme lesbar sein. Der
            # letzte Fund ist der Abschluss; daraus duerfen nie zwei Produkte
            # und damit zwei Entwuerfe entstehen.
            if anker and anker[-1][1] == nummer and anker[-1][0] == i - 1:
                anker[-1] = (i, nummer)
            else:
                anker.append((i, nummer))

    abschluesse = [i for i, _nummer in anker]

    if not abschluesse:
        raise GruppierungUnsicher(
            "Gruppierung unsicher: keine bestätigte Teilenummer gefunden. "
            "Bitte jedes Produkt mit dem Nummernbild abschließen.")
    if abschluesse[-1] != len(fotos) - 1:
        raise GruppierungUnsicher(
            "Gruppierung unsicher: Nach der letzten bestätigten Teilenummer "
            "liegen noch Fotos. Bitte das Nummernbild jedes Produkts zuletzt fotografieren.")

    gruppen: List[List[Path]] = []
    start = 0
    for ende in abschluesse:
        gruppen.append(list(fotos[start:ende + 1]))
        start = ende + 1
    return gruppen

# Ab dieser Pause gilt das nächste Foto als neues Teil. 90 s ist bewusst
# großzügig: lieber zwei Teile versehentlich zusammen (fällt beim Gegenlesen
# sofort auf) als ein Teil zerrissen, denn dann entstehen zwei halbe Inserate.
PAUSE_SEKUNDEN = int(os.environ.get("AUTOLISTER_GRUPPEN_PAUSE", "90"))

# EXIF-Feld "DateTimeOriginal" — der Moment der Aufnahme, nicht des Kopierens.
_EXIF_AUFNAHMEZEIT = 36867
_EXIF_DIGITALISIERT = 36868


def _aufnahmezeit(pfad: Path) -> Optional[datetime]:
    """Aufnahmezeitpunkt aus den EXIF-Daten lesen.

    Fällt auf die Dateizeit zurück, wenn keine EXIF-Daten da sind. Die ist
    ungenauer (Kopieren setzt sie neu), aber besser als gar keine Ordnung.
    """
    try:
        from PIL import Image
        with Image.open(pfad) as bild:
            exif = bild.getexif()
            for feld in (_EXIF_AUFNAHMEZEIT, _EXIF_DIGITALISIERT):
                roh = exif.get(feld)
                if not roh:
                    # Die Aufnahmezeit steht oft im Unterverzeichnis "Exif"
                    try:
                        roh = exif.get_ifd(0x8769).get(feld)
                    except Exception:
                        roh = None
                if roh:
                    return datetime.strptime(str(roh)[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    try:
        return datetime.fromtimestamp(pfad.stat().st_mtime)
    except Exception:
        return None


def nach_teilenummer(fotos: List[Path]) -> Optional[List[List[Path]]]:
    """Fotos anhand der erkannten Teilenummern gruppieren.

    **Das ist der verlässliche Weg**, seit klar ist, dass die Aufnahmezeit beim
    Hochladen verlorengeht: iOS streift die EXIF-Daten ab (am 2026-08-07
    nachgemessen — alle neun Fotos eines Uploads trugen nur die Downloadzeit
    im Sekundenabstand). Die Teilenummer steht dagegen auf dem Teil selbst.

    Verfahren: Jedes Foto einzeln lesen. Fotos, auf denen eine Teilenummer
    steht, bilden die Ankerpunkte. Ein Foto ohne Nummer — die Übersichtsbilder
    — kommt zu dem Ankerpunkt, der ihm in der Aufnahmereihenfolge am nächsten
    liegt. Das trägt in beiden Richtungen: egal ob erst das Teil und dann das
    Typenschild fotografiert wird oder umgekehrt.

    Gibt None zurück, wenn keine oder nur eine Nummer gefunden wurde — dann
    weiß dieses Verfahren nichts Besseres, und der Aufrufer entscheidet.
    """
    from . import partnumber  # spät importiert, hält das Modul leichtgewichtig

    if len(fotos) <= 1:
        return None
    try:
        je_foto = ocr.lies_fotos_einzeln(fotos, gruendlich=False)
    except Exception as fehler:  # noqa: BLE001
        log.warning("Gruppierung über Teilenummern nicht möglich: %s", fehler)
        return None

    # Je Foto die beste Teilenummer bestimmen — und die übrigen Lesarten
    # aufheben, siehe die Zusammenführung weiter unten.
    nummern: List[Optional[str]] = []
    lesarten: List[set] = []
    for texte in je_foto:
        kandidaten = partnumber.finde_kandidaten(texte) if texte else []
        nummern.append(kandidaten[0].nummer if kandidaten else None)
        lesarten.append({k.nummer for k in kandidaten[:6]})

    # **Ein Lesefehler darf kein zweites Teil erfinden.**
    #
    # Am 2026-08-14 zerriss eine Sonnenblende in zwei Inserate: Auf einem Foto
    # las die Texterkennung eine andere Nummer als auf den beiden anderen, und
    # weil die Gruppen nach Nummer geschlüsselt sind, wurden daraus zwei Teile.
    # Dass eBay später beide auf `8K0857551` berichtigte, kam zu spät — die
    # Trennung war da längst passiert, und es entstanden zwei Entwürfe für ein
    # Teil. Genau davor warnt der Nutzer seit Wochen.
    #
    # Die Rettung steckt schon in `partnumber`: Es liefert nicht eine Nummer,
    # sondern alle plausiblen Lesarten. Überschneiden sich die Lesarten zweier
    # aufeinanderfolgender Anker, ist es dasselbe Teil — dann gewinnt die
    # Nummer des ERSTEN Ankers, und beide Fotos bleiben zusammen.
    for i in range(len(nummern)):
        if not nummern[i]:
            continue
        vorher = next((j for j in range(i - 1, -1, -1) if nummern[j]), None)
        if vorher is None:
            continue
        if nummern[i] != nummern[vorher] and (lesarten[i] & lesarten[vorher]):
            log.info("Gruppierung: %s und %s sind dieselbe Lesart — zusammengelegt",
                     nummern[vorher], nummern[i])
            nummern[i] = nummern[vorher]

    verschiedene = {n for n in nummern if n}
    if len(verschiedene) < 2:
        return None                      # ein Teil (oder gar keine Nummer)

    # ------------------------------------------------------------------
    # Ankerbloecke: aufeinanderfolgende Fotos mit DERSELBEN Nummer sind ein
    # Block. Die Zahl der Bloecke ist die Zahl der Teile — der Nutzer legt
    # in jede Gruppe genau ein Bild, auf dem die Nummer zu erkennen ist.
    bloecke: List[dict] = []
    for i, n in enumerate(nummern):
        if not n:
            continue
        if bloecke and bloecke[-1]["nummer"] == n and all(
                not nummern[j] for j in range(bloecke[-1]["ende"] + 1, i)):
            bloecke[-1]["ende"] = i
        else:
            bloecke.append({"nummer": n, "start": i, "ende": i})

    if len(bloecke) < 2:
        return None

    # ------------------------------------------------------------------
    # WO wird geschnitten?
    #
    # Die alte Regel lautete „alles nach einer Nummer gehoert zu ihr, bis die
    # naechste kommt". Sie setzt voraus, dass die Nummer am ANFANG einer
    # Gruppe steht. Tatsaechlich fotografiert der Nutzer die Nummer meist als
    # LETZTES — damit war jede Gruppe um genau ein Teil verschoben. Am
    # 2026-08-14 an 15 Fotos von 5 Teilen gemessen: sieben Gruppen, keine
    # einzige richtig; das Nummernfoto der Sonnenblende lag bei den Bildern
    # des Armaturenbretts.
    #
    # Eine feste Richtung ist also falsch, egal welche. Sicher ist nur: In
    # jedem Zwischenraum zwischen zwei Ankerbloecken liegt GENAU EINE Grenze.
    # Wo genau, entscheidet das Bild — beim Teilewechsel aendert sich der
    # ganze Bildinhalt, innerhalb eines Teils nur der Blickwinkel.
    #
    # ⚠️ Bildaehnlichkeit als Schiedsrichter ist GEMESSEN UNBRAUCHBAR und
    # deshalb nicht eingebaut. An 13 echten Fotos nachgerechnet: Zwei Bilder
    # desselben Teils erreichten Abstand 0,67, ein echter Teilewechsel nur
    # 0,31 — Nahaufnahme und Uebersichtsfoto desselben Teils sehen sich
    # weniger aehnlich als zwei verschiedene schwarze Kunststoffteile auf
    # demselben Tisch. Als Entscheider zwischen drei Kandidaten traf es 1 von
    # 4. Wer es erneut versuchen will: erst Vordergrund freistellen und
    # Silhouetten vergleichen, Farbe und Grobraster reichen nicht.
    #
    # Verlaesslich ist stattdessen die GEWOHNHEIT: Der Nutzer fotografiert
    # erst das Teil und zuletzt die eingepraegte Nummer. Dann faellt die
    # Grenze genau auf den Anker. `NUMMER_ZULETZT=0` dreht es um, wenn jemand
    # andersherum arbeitet; ohne Festlegung bleibt die Mitte, die bei beiden
    # Gewohnheiten nur halb danebenliegt statt ganz.
    wo = os.environ.get("AUTOLISTER_NUMMER_POSITION", "ende").strip().lower()
    grenzen: List[int] = []
    for a, b in zip(bloecke, bloecke[1:]):
        kandidaten = list(range(a["ende"], b["start"]))
        if not kandidaten:
            grenzen.append(a["ende"])
        elif wo == "ende":
            grenzen.append(kandidaten[0])         # Nummer schliesst die Gruppe ab
        elif wo == "anfang":
            grenzen.append(kandidaten[-1])        # Nummer eroeffnet die naechste
        else:
            grenzen.append(kandidaten[len(kandidaten) // 2])

    gruppen: List[List[Path]] = []
    start = 0
    for g in grenzen:
        gruppen.append(list(fotos[start:g + 1]))
        start = g + 1
    gruppen.append(list(fotos[start:]))
    gruppen = [g for g in gruppen if g]

    log.info("Gruppierung über Teilenummern: %d Fotos -> %d Teile (%s)",
             len(fotos), len(gruppen), ", ".join(b["nummer"] for b in bloecke))
    return gruppen


def _bildmerkmale(pfad: Path):
    """Farbhistogramm und Grobraster eines Fotos. None, wenn unlesbar."""
    try:
        from PIL import Image
    except ImportError:                      # ohne Pillow bleibt die Mitte
        return None
    try:
        with Image.open(pfad) as im:
            klein = im.convert("RGB").resize((48, 48))
    except Exception:                        # noqa: BLE001 - unlesbar ist unlesbar
        return None
    pixel = list(klein.getdata())
    hist = [0.0] * 216
    raster = [0.0] * 36
    zaehler = [0] * 36
    for idx, (r, g, b) in enumerate(pixel):
        hist[(r * 6 // 256) * 36 + (g * 6 // 256) * 6 + (b * 6 // 256)] += 1
        zelle = ((idx // 48) * 6 // 48) * 6 + ((idx % 48) * 6 // 48)
        raster[zelle] += 0.299 * r + 0.587 * g + 0.114 * b
        zaehler[zelle] += 1
    n = float(len(pixel))
    hist = [h / n for h in hist]
    raster = [raster[i] / zaehler[i] / 255.0 for i in range(36)]
    return hist, raster


def _bildbruch(a: Path, b: Path) -> float:
    """0 = dasselbe Bild, 1 = voellig verschieden. Negativ = nicht messbar."""
    ma, mb = _bildmerkmale(a), _bildmerkmale(b)
    if not ma or not mb:
        return -1.0
    ueberlapp = sum(min(x, y) for x, y in zip(ma[0], mb[0]))
    rasterabstand = sum(abs(x - y) for x, y in zip(ma[1], mb[1])) / 36.0
    return 0.7 * (1.0 - ueberlapp) + 0.3 * min(1.0, rasterabstand * 3.0)


def aufteilen(fotos: List[Path]) -> List[List[Path]]:
    """Fotos eines Uploads in Teile aufteilen — bester verfügbarer Weg.

    Zuerst über die Teilenummern (überlebt den Handy-Upload), danach über die
    Aufnahmezeit (greift bei Fotos vom Mac, wo die EXIF-Daten noch da sind).
    """
    ueber_nummer = nach_teilenummer(fotos)
    if ueber_nummer and len(ueber_nummer) > 1:
        return ueber_nummer
    return nach_aufnahmezeit(fotos)


def nach_aufnahmezeit(fotos: List[Path],
                      pause: Optional[int] = None) -> List[List[Path]]:
    """Fotos in Gruppen aufteilen — eine je Teil.

    Gibt immer mindestens eine Gruppe zurück. Lässt sich bei keinem Bild eine
    Zeit ermitteln, bleibt alles zusammen: dann ist die ursprüngliche
    Reihenfolge die einzige Information, die wir haben, und Raten macht es
    schlechter.
    """
    pause = PAUSE_SEKUNDEN if pause is None else pause
    if len(fotos) <= 1:
        return [list(fotos)]

    mit_zeit = [(pfad, _aufnahmezeit(pfad)) for pfad in fotos]
    if all(zeit is None for _, zeit in mit_zeit):
        log.info("Gruppierung: keine Aufnahmezeiten lesbar — alles ein Teil")
        return [list(fotos)]

    # Bilder ohne Zeit hinten anhängen statt wegwerfen; sie landen dann in der
    # letzten Gruppe und tauchen wenigstens im Inserat auf.
    ohne_zeit = [p for p, z in mit_zeit if z is None]
    sortiert = sorted([(z, p) for p, z in mit_zeit if z is not None])

    gruppen: List[List[Path]] = [[sortiert[0][1]]]
    for i in range(1, len(sortiert)):
        abstand = (sortiert[i][0] - sortiert[i - 1][0]).total_seconds()
        if abstand > pause:
            gruppen.append([])
        gruppen[-1].append(sortiert[i][1])

    if ohne_zeit:
        gruppen[-1].extend(ohne_zeit)

    if len(gruppen) > 1:
        log.info("Gruppierung: %d Fotos -> %d Teile (%s)", len(fotos), len(gruppen),
                 ", ".join("%d Foto(s)" % len(g) for g in gruppen))
    return gruppen
