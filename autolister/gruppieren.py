"""Fotos mehrerer Teile automatisch auseinandersortieren.

Der Nutzer lädt alle Fotos eines Rundgangs auf einmal hoch — Schmutzfänger,
Feststellbremse, Armaturenbrett — und das Programm soll selbst erkennen,
welche Bilder zu welchem Teil gehören.

**Das verlässlichste Merkmal ist die Aufnahmezeit.** Wer ein Teil
fotografiert, macht drei, vier Bilder in wenigen Sekunden, geht dann zum
nächsten Teil und braucht dafür mindestens eine halbe Minute. Diese Pausen
sind die Trennlinien. Das funktioniert ohne KI, ohne Kosten und ohne dass
auf jedem Foto die Teilenummer lesbar sein muss.

Warum nicht über die Teilenummer gruppieren? Weil sie nur auf einem oder zwei
Bildern je Teil überhaupt zu sehen ist. Die Übersichtsfotos ließen sich damit
nicht zuordnen — und gerade die braucht das Inserat.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

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
