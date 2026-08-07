"""Kostenlose Texterkennung über das in macOS eingebaute Vision-Framework.

Läuft komplett auf dem Mac — keine Internetverbindung, keine API, keine Kosten.
Damit lässt sich die Teilenummer von den Fotos lesen, ohne ein Sprachmodell zu
bezahlen.

Der wichtige Kniff: eingestanzte Teilenummern stehen oft **hochkant** auf dem
Bauteil, die Texterkennung liest aber waagerecht. Deshalb wird jedes Foto in
allen vier Drehungen geprüft und alles Gefundene eingesammelt. Gemessen an
einem echten Mercedes-Differential: bei 0° kam nur "205" / "351 00 05" heraus,
bei 270° die vollständige Nummer "A 205 351 00 05".
"""
from __future__ import annotations

import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageEnhance, ImageOps

from . import config, images

log = logging.getLogger("autolister")

DREHUNGEN = (0, 270, 90, 180)  # 270 zuerst: dort sitzen Stanzungen am häufigsten


def verfuegbar() -> bool:
    """Ist die macOS-Texterkennung nutzbar?"""
    try:
        import Quartz  # noqa: F401
        import Vision  # noqa: F401
        return True
    except Exception:
        return False


def _ocr_datei(path: Path) -> List[Tuple[str, float]]:
    """Eine Bilddatei durch die Vision-Texterkennung schicken."""
    import Quartz
    import Vision
    from Foundation import NSURL

    url = NSURL.fileURLWithPath_(str(path))
    quelle = Quartz.CGImageSourceCreateWithURL(url, None)
    if quelle is None:
        return []
    bild = Quartz.CGImageSourceCreateImageAtIndex(quelle, 0, None)
    if bild is None:
        return []

    anfrage = Vision.VNRecognizeTextRequest.alloc().init()
    anfrage.setRecognitionLevel_(0)          # 0 = genau, 1 = schnell
    anfrage.setUsesLanguageCorrection_(False)  # Teilenummern sind keine Wörter
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(bild, None)
    handler.performRequests_error_([anfrage], None)

    treffer: List[Tuple[str, float]] = []
    for beobachtung in (anfrage.results() or []):
        kandidaten = beobachtung.topCandidates_(1)
        if kandidaten and len(kandidaten):
            treffer.append((str(kandidaten[0].string()),
                            float(kandidaten[0].confidence())))
    return treffer


def _verstaerken(bild: Image.Image) -> Image.Image:
    """Kontrast und Schärfe anheben.

    Eingestanzte Zeichen auf blankem Aluminium haben kaum Kontrast — es sind
    Schattenkanten, keine Farbunterschiede. An einem Audi-Träger las die
    Erkennung im Original nur "807 832. A", nach dieser Aufbereitung dagegen
    "SK0 807 832 4", also die vollständige Nummer inklusive Typnummer.
    """
    grau = ImageOps.grayscale(bild)
    kontrastreich = ImageEnhance.Contrast(grau).enhance(2.0)
    return ImageEnhance.Sharpness(kontrastreich).enhance(2.5)


def _foto_alle_drehungen(foto: Path, arbeitsordner: Path,
                         gruendlich: bool = False) -> List[Tuple[str, float]]:
    """Ein Foto in allen vier Drehungen lesen.

    Mit `gruendlich=True` wird zusätzlich eine kontrastverstärkte Fassung
    gelesen. Das verdoppelt die Laufzeit und wird deshalb nur nachgeschoben,
    wenn der schnelle Durchgang keine Teilenummer hergab.
    """
    gefunden: List[Tuple[str, float]] = []
    try:
        original = images.open_normalized(foto)
    except Exception as exc:
        log.warning("OCR: %s nicht lesbar (%s)", foto.name, exc)
        return gefunden

    if max(original.size) > config.OCR_MAX_EDGE:
        original.thumbnail((config.OCR_MAX_EDGE, config.OCR_MAX_EDGE), Image.LANCZOS)

    aufbereitungen = [("", lambda b: b)]
    if gruendlich:
        aufbereitungen.append(("_v", _verstaerken))

    for kuerzel, aufbereiten in aufbereitungen:
        for grad in DREHUNGEN:
            bild = original.rotate(grad, expand=True) if grad else original
            ziel = arbeitsordner / ("%s%s_%d.jpg" % (foto.stem, kuerzel, grad))
            try:
                aufbereiten(bild).convert("RGB").save(ziel, "JPEG", quality=92)
                gefunden.extend(_ocr_datei(ziel))
            except Exception as exc:
                log.debug("OCR: Drehung %d bei %s fehlgeschlagen: %s",
                          grad, foto.name, exc)
            finally:
                ziel.unlink(missing_ok=True)
    return gefunden


def lies_fotos_einzeln(fotos: List[Path],
                       gruendlich: bool = False) -> List[List[Tuple[str, float]]]:
    """Wie `lies_fotos`, aber **je Foto getrennt**.

    Die Zuordnung „welcher Text stand auf welchem Bild" wird ohnehin berechnet;
    `lies_fotos` wirft sie beim Zusammenführen nur weg. Getrennt kostet sie
    keine zusätzliche Erkennungszeit — und sie verrät, welches Foto die
    Teilenummer zeigt. Genau das braucht die Fotoreihenfolge: die
    Nahaufnahme des Aufklebers taugt nicht als Hauptbild.
    """
    if not verfuegbar():
        raise RuntimeError(
            "macOS-Texterkennung nicht verfügbar. Bitte installieren:\n"
            "  .venv/bin/pip install pyobjc-framework-Vision pyobjc-framework-Quartz"
        )
    with tempfile.TemporaryDirectory(prefix="autolister-ocr-") as tmp:
        ordner = Path(tmp)
        arbeiter = min(4, max(1, len(fotos)))
        with ThreadPoolExecutor(max_workers=arbeiter) as pool:
            return list(pool.map(
                lambda f: _foto_alle_drehungen(f, ordner, gruendlich), fotos))


def lies_fotos(fotos: List[Path], gruendlich: bool = False) -> List[Tuple[str, float]]:
    """Alle Fotos lesen und sämtliche Textfunde zurückgeben.

    Rückgabe: Liste aus (Text, Zuverlässigkeit 0..1), Duplikate entfernt.
    `gruendlich=True` liest zusätzlich eine kontrastverstärkte Fassung.
    """
    if not verfuegbar():
        raise RuntimeError(
            "macOS-Texterkennung nicht verfügbar. Bitte installieren:\n"
            "  .venv/bin/pip install pyobjc-framework-Vision pyobjc-framework-Quartz"
        )

    with tempfile.TemporaryDirectory(prefix="autolister-ocr-") as tmp:
        ordner = Path(tmp)
        arbeiter = min(4, max(1, len(fotos)))
        with ThreadPoolExecutor(max_workers=arbeiter) as pool:
            ergebnisse = list(pool.map(
                lambda f: _foto_alle_drehungen(f, ordner, gruendlich), fotos))

    # Duplikate zusammenführen, jeweils die beste Zuverlässigkeit behalten
    beste: dict = {}
    for liste in ergebnisse:
        for text, zuverlaessigkeit in liste:
            text = text.strip()
            if not text:
                continue
            if text not in beste or zuverlaessigkeit > beste[text]:
                beste[text] = zuverlaessigkeit
    return sorted(beste.items(), key=lambda kv: -kv[1])
