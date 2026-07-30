"""Bildaufbereitung — der größte Geschwindigkeitshebel der Pipeline.

Zwei getrennte Wege:

* `prepare_for_vision()` verkleinert Fotos stark (Standard 1568 px lange Kante,
  JPEG). Ein iPhone-Foto schrumpft damit von ~4 MB auf ~200 KB — das macht den
  API-Upload um ein Vielfaches schneller und senkt die Token-Kosten pro Bild
  von ~4800 auf ~1600.
* `prepare_for_upload()` lässt die Auflösung unangetastet und wandelt nur HEIC
  nach JPEG um, damit die eBay-Fotos in voller Qualität hochgeladen werden.

HEIC ist Pflicht, kein Extra: iPhone-Fotos sind standardmäßig HEIC, und weder
die Anthropic-API noch der eBay-Uploader nehmen dieses Format an.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List

from PIL import Image, ImageOps

from . import config

log = logging.getLogger("autolister")

try:  # HEIC/HEIF-Unterstützung für iPhone-Fotos registrieren
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except Exception:  # pragma: no cover - nur wenn pillow-heif fehlt
    HEIC_SUPPORTED = False
    log.warning("pillow-heif nicht verfügbar — HEIC-Fotos können nicht gelesen werden")


def _open_normalized(path: Path) -> Image.Image:
    """Bild öffnen, EXIF-Drehung anwenden, nach RGB konvertieren."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # sonst liegen Hochkant-Fotos quer
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def _resize_one(src: Path, dest_dir: Path, max_edge: int, quality: int) -> Path:
    dest = dest_dir / (src.stem + ".jpg")
    try:
        img = _open_normalized(src)
    except Exception as exc:
        log.warning("Bild %s nicht lesbar (%s) — wird übersprungen", src.name, exc)
        raise
    if max_edge and max(img.size) > max_edge:
        img.thumbnail((max_edge, max_edge), Image.LANCZOS)
    img.save(dest, "JPEG", quality=quality, optimize=True)
    return dest


def _prepare(photos: List[Path], dest_dir: Path, max_edge: int,
             quality: int) -> Dict[Path, Path]:
    """Fotos parallel aufbereiten (Pillow gibt die GIL frei -> echte Parallelität).

    Gibt eine Zuordnung Original -> aufbereitete Datei zurück; nicht lesbare
    Dateien fehlen darin, statt die Reihenfolge zu verschieben.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    workers = min(8, max(1, len(photos)))
    out: Dict[Path, Path] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_resize_one, p, dest_dir, max_edge, quality): p for p in photos
        }
        for future, src in futures.items():
            try:
                out[src] = future.result()
            except Exception:
                continue  # bereits in _resize_one geloggt
    if not out:
        raise RuntimeError("Keine der %d Bilddateien konnte gelesen werden" % len(photos))
    return out


def prepare_for_vision(photos: List[Path], work_dir: Path,
                       max_edge: int = 0) -> List[Path]:
    """Kleine JPEG-Kopien für die Bildanalyse erzeugen (Reihenfolge bleibt erhalten)."""
    mapping = _prepare(
        photos,
        work_dir / "_analyse",
        max_edge or config.VISION_MAX_EDGE,
        config.VISION_JPEG_QUALITY,
    )
    return [mapping[p] for p in photos if p in mapping]


def prepare_for_upload(photos: List[Path], work_dir: Path) -> List[Path]:
    """Fotos für den eBay-Upload vorbereiten: volle Auflösung, aber garantiert JPEG.

    Bereits vorhandene JPEGs werden unverändert durchgereicht (kein
    Qualitätsverlust durch erneutes Encodieren).
    """
    need_convert = [p for p in photos if p.suffix.lower() not in (".jpg", ".jpeg")]
    if not need_convert:
        return list(photos)
    converted = _prepare(need_convert, work_dir / "_upload", 0,
                         config.UPLOAD_JPEG_QUALITY)
    result = []
    for p in photos:
        if p.suffix.lower() in (".jpg", ".jpeg"):
            result.append(p)
        elif p in converted:
            result.append(converted[p])
    return result
