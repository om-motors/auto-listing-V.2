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

    # Ankerpunkte: Stellen, an denen eine neue Nummer auftaucht
    anker = [(i, n) for i, n in enumerate(nummern) if n]
    gruppen_von_nummer: dict = {}
    for _, nummer in anker:
        gruppen_von_nummer.setdefault(nummer, [])

    # **Alles nach einer Teilenummer gehört zu diesem Teil, bis die nächste
    # Nummer auftaucht.** Fotos vor der allerersten Nummer gehören zum ersten
    # Teil — davor gibt es ja nichts anderes.
    #
    # Vorher wurde der *nächstgelegene* Anker genommen, egal ob davor oder
    # dahinter. Das ging am 2026-08-07 daneben: Ein Foto der
    # Lautsprecherabdeckung lag ein Bild vor der Armaturenbrett-Nummer und
    # zwei hinter der eigenen — es landete im falschen Inserat, und zwar als
    # Hauptfoto. An den neun Fotos jenes Uploads nachgerechnet: die
    # Rückwärtsregel trifft 9 von 9, das Abstandsverfahren 8 von 9.
    for i, foto in enumerate(fotos):
        if nummern[i]:
            gruppen_von_nummer[nummern[i]].append(foto)
            continue
        davor = [a for a in anker if a[0] < i]
        zustaendig = davor[-1][1] if davor else anker[0][1]
        gruppen_von_nummer[zustaendig].append(foto)

    # Reihenfolge der Gruppen: wie sie zuerst auftauchen
    reihenfolge = []
    for _, nummer in anker:
        if nummer not in reihenfolge:
            reihenfolge.append(nummer)
    gruppen = [gruppen_von_nummer[n] for n in reihenfolge]

    log.info("Gruppierung über Teilenummern: %d Fotos -> %d Teile (%s)",
             len(fotos), len(gruppen), ", ".join(reihenfolge))
    return gruppen


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
