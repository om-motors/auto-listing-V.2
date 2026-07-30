"""Schritt 1: Produktfotos analysieren — Teilenummer, Marke, Position etc.

Zweistufig für Tempo: erst alle Fotos klein (1568 px, schneller Upload).
Nur wenn Claude die Teilenummer dabei nicht sicher lesen konnte, läuft ein
zweiter Durchgang mit hochauflösenden Bildern. So zahlt der Normalfall nicht
für den Ausnahmefall.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from . import config, images, llm

log = logging.getLogger("autolister")

PROMPT = """Du analysierst Fotos eines gebrauchten Kfz-Teils für ein eBay-Inserat.

Die Teilenummer ist auf dem Teil eingestanzt oder steht auf einem Etikett
(Beispiele: "8T0 807 284 C", "A 205 351 00 05"). Lies sie SORGFÄLTIG —
0/O und 8/B sind leicht zu verwechseln. Prüfe alle Fotos gegeneinander.

Erfasse außerdem: Markenlogo (Audi-Ringe, VW, Mercedes-Stern ...),
Materialkennzeichnung, Herkunft ("Germany" o.ä.), Links/Rechts-Hinweise,
und um was für ein Teil es sich vermutlich handelt.

Setze "konfidenz_teilenummer" ehrlich: "niedrig", wenn die Zeichen unscharf
oder nur teilweise lesbar sind — dann bekommst du die Fotos in höherer
Auflösung noch einmal.

Antworte NUR mit einem JSON-Objekt, ohne Erklärtext:
{
  "teilenummer": "Teilenummer wie auf dem Teil, mit Leerzeichen",
  "teilenummer_kompakt": "gleiche Nummer ohne Leerzeichen",
  "hersteller": "Audi | VW | Mercedes-Benz | BMW | ... oder null",
  "teil_vermutung": "z.B. Halter Stoßfänger, Differential, Zierleiste",
  "position": "vorne links | hinten rechts | ... oder null",
  "material": "z.B. Kunststoff, Aluminium oder null",
  "ursprungsland": "z.B. Germany oder null",
  "unsicherheiten": ["Liste von Punkten, bei denen du unsicher bist"],
  "konfidenz_teilenummer": "hoch | mittel | niedrig"
}"""


def _normalize(result: dict) -> dict:
    kompakt = result.get("teilenummer_kompakt") or result.get("teilenummer", "")
    kompakt = kompakt.replace(" ", "").replace("/", "").replace(".", "").replace("-", "")
    result["teilenummer_kompakt"] = kompakt
    return result


def analyze_photos(photos: List[Path], work_dir: Path) -> dict:
    """Fotos analysieren. `work_dir` nimmt die aufbereiteten Kopien auf."""
    if not photos:
        raise ValueError("Keine Fotos übergeben")

    small = images.prepare_for_vision(photos, work_dir)
    log.info("Analysiere %d Foto(s) (%d px)", len(small), config.VISION_MAX_EDGE)
    result = _normalize(llm.ask_json(PROMPT, images=small, model=config.VISION_MODEL))

    unsicher = (
        not result.get("teilenummer")
        or str(result.get("konfidenz_teilenummer", "")).lower() in ("niedrig", "low")
    )
    if unsicher and config.VISION_MAX_EDGE_RETRY > config.VISION_MAX_EDGE:
        log.info("Teilenummer unsicher — zweiter Durchgang mit %d px",
                 config.VISION_MAX_EDGE_RETRY)
        big = images.prepare_for_vision(photos, work_dir,
                                        max_edge=config.VISION_MAX_EDGE_RETRY)
        retry = _normalize(llm.ask_json(PROMPT, images=big, model=config.VISION_MODEL))
        if retry.get("teilenummer"):
            retry.setdefault("unsicherheiten", []).append(
                "Teilenummer erst im hochauflösenden Durchgang lesbar — bitte prüfen"
            )
            result = retry

    if not result.get("teilenummer"):
        raise llm.LLMError("Keine Teilenummer erkannt: %s" % result)
    return result


def collect_photos(folder: Path) -> List[Path]:
    return sorted(
        p for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in config.IMAGE_EXTENSIONS
        and not p.name.startswith(".")
    )
