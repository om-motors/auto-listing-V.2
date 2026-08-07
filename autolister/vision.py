"""Schritt 1: Fotos auswerten — Teilenummer, Hersteller, Position.

Zwei Betriebsarten:

* **lokal** (Standard, kostenlos): die in macOS eingebaute Texterkennung liest
  den Text von den Fotos, feste Muster ziehen daraus die Teilenummer. Kostet
  nichts und dauert rund eine Sekunde. Mehrere Kandidaten werden später von
  eBay selbst gegengeprüft (`research.pruefe_kandidaten`).
* **KI** (optional): ein Bildmodell schaut sich die Fotos an. Genauer bei
  schwierigen Fotos, aber kostenpflichtig bzw. auf ein Abo angewiesen.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List

from . import config, images, llm, ocr, partnumber

log = logging.getLogger("autolister")

PROMPT = """Du analysierst Fotos eines gebrauchten Kfz-Teils für ein eBay-Inserat.

Die Teilenummer ist auf dem Teil eingestanzt oder steht auf einem Etikett
(Beispiele: "8T0 807 284 C", "A 205 351 00 05"). Lies sie SORGFÄLTIG —
0/O und 8/B sind leicht zu verwechseln. Prüfe alle Fotos gegeneinander.
Achtung: Die Nummer steht oft HOCHKANT auf dem Bauteil.

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


class KeineTeilenummer(RuntimeError):
    """Auf den Fotos war keine Teilenummer zu finden."""


def _normalize(result: Dict) -> Dict:
    kompakt = result.get("teilenummer_kompakt") or result.get("teilenummer", "")
    for zeichen in " /.-":
        kompakt = kompakt.replace(zeichen, "")
    result["teilenummer_kompakt"] = kompakt.upper()
    return result


# --- Kostenlose Variante -----------------------------------------------------

def analysiere_lokal(photos: List[Path], vorgabe: str = "") -> Dict:
    """Fotos mit der macOS-Texterkennung auswerten. Kostet nichts.

    Es wird immer gründlich gelesen (Original **und** kontrastverstärkte
    Fassung). Ein früherer Versuch, die Verstärkung nur bei Misserfolg
    nachzuschieben, ging schief: der schnelle Durchgang lieferte an einem
    Audi-Träger *falsche* Kandidaten (5K0807032 statt 8K0807832A) und wurde
    deshalb nie eskaliert. Die zusätzliche Sekunde ist billiger als eine
    falsche Teilenummer im Inserat.
    """
    # Je Foto getrennt lesen: kostet keine zusätzliche Zeit, verrät aber,
    # welches Bild die Teilenummer zeigt. Das entscheidet später über die
    # Reihenfolge beim Upload — eine Nahaufnahme des Aufklebers ist ein
    # schlechtes Hauptbild.
    je_foto = ocr.lies_fotos_einzeln(photos, gruendlich=True)
    beste_je_text: dict = {}
    for liste in je_foto:
        for text, guete in liste:
            text = text.strip()
            if text and guete > beste_je_text.get(text, -1):
                beste_je_text[text] = guete
    texte = sorted(beste_je_text.items(), key=lambda p: -p[1])
    kandidaten = partnumber.finde_kandidaten(texte) if texte else []

    # Vom Nutzer vorgegebene Nummer (Ordnername) schlägt jede Lesart
    vorgegeben = partnumber.aus_vorgabe(vorgabe)
    if vorgegeben:
        log.info("Teilenummer aus Vorgabe übernommen: %s", vorgegeben.nummer)
        kandidaten = [vorgegeben] + [k for k in kandidaten
                                     if k.nummer != vorgegeben.nummer]
        texte = texte or [(vorgegeben.formatiert, 1.0)]

    if not texte:
        raise KeineTeilenummer(
            "Die Texterkennung hat auf den Fotos keinen Text gefunden. "
            "Bitte ein scharfes Foto der eingestanzten Teilenummer ergänzen."
        )
    if not kandidaten:
        gefunden = ", ".join(repr(t) for t, _ in texte[:10])
        raise KeineTeilenummer(
            "Keine Teilenummer im erkannten Text gefunden. Gelesen wurde: %s. "
            "Bitte ein schärferes Foto der Nummer ergänzen." % gefunden
        )

    beste = kandidaten[0]
    log.info("Texterkennung: %d Kandidat(en), bester %s (%s)",
             len(kandidaten), beste.nummer, beste.hersteller)
    return {
        "teilenummer": beste.formatiert,
        "teilenummer_kompakt": beste.nummer,
        "hersteller": beste.hersteller,
        "teil_vermutung": None,
        "position": None,
        "material": None,
        "ursprungsland": None,
        "unsicherheiten": [],
        "konfidenz_teilenummer": "hoch" if beste.punkte >= 5 else "mittel",
        "_kandidaten": kandidaten,
        "_ocr_text": [t for t, _ in texte],
        # Welche Fotos zeigen die Teilenummer? Die gehören ans Ende der
        # Bilderstrecke, nicht nach vorne.
        "_nummer_fotos": _fotos_mit_nummer(photos, je_foto, beste.nummer),
    }


def _fotos_mit_nummer(photos: List[Path], je_foto, nummer: str) -> List[str]:
    """Fotos heraussuchen, auf denen die gefundene Teilenummer steht."""
    gesucht = re.sub(r"[^A-Z0-9]", "", (nummer or "").upper())
    if not gesucht:
        return []
    treffer = []
    for foto, texte in zip(photos, je_foto):
        for text, _ in texte:
            if gesucht in re.sub(r"[^A-Z0-9]", "", text.upper()):
                treffer.append(str(foto))
                break
    return treffer


# --- Variante mit Bildmodell -------------------------------------------------

def analysiere_mit_ki(photos: List[Path], work_dir: Path) -> Dict:
    """Fotos von einem Bildmodell auswerten (kostenpflichtig bzw. Abo)."""
    klein = images.prepare_for_vision(photos, work_dir)
    log.info("Bildmodell: %d Foto(s) bei %d px", len(klein), config.VISION_MAX_EDGE)
    ergebnis = _normalize(llm.ask_json(PROMPT, images=klein, model=config.VISION_MODEL))

    unsicher = (
        not ergebnis.get("teilenummer")
        or str(ergebnis.get("konfidenz_teilenummer", "")).lower() in ("niedrig", "low")
    )
    if unsicher and config.VISION_MAX_EDGE_RETRY > config.VISION_MAX_EDGE:
        log.info("Teilenummer unsicher — zweiter Durchgang mit %d px",
                 config.VISION_MAX_EDGE_RETRY)
        gross = images.prepare_for_vision(photos, work_dir,
                                          max_edge=config.VISION_MAX_EDGE_RETRY)
        zweiter = _normalize(llm.ask_json(PROMPT, images=gross,
                                          model=config.VISION_MODEL))
        if zweiter.get("teilenummer"):
            zweiter.setdefault("unsicherheiten", []).append(
                "Teilenummer erst im hochauflösenden Durchgang lesbar — bitte prüfen")
            ergebnis = zweiter

    if not ergebnis.get("teilenummer"):
        raise KeineTeilenummer("Das Bildmodell hat keine Teilenummer erkannt.")
    return ergebnis


def analyze_photos(photos: List[Path], work_dir: Path, vorgabe: str = "") -> Dict:
    """Fotos auswerten — je nach Betriebsart lokal oder mit Bildmodell.

    Fällt die KI aus (kein Guthaben, kein Netz), wird automatisch auf die
    kostenlose lokale Auswertung zurückgeschaltet, statt abzubrechen.
    """
    if not photos:
        raise ValueError("Keine Fotos übergeben")

    modus = config.aktiver_modus()
    if modus == "lokal":
        return analysiere_lokal(photos, vorgabe)

    try:
        return analysiere_mit_ki(photos, work_dir)
    except Exception as exc:  # noqa: BLE001
        log.warning("Bildmodell nicht nutzbar (%s) — weiche auf Texterkennung aus", exc)
        ergebnis = analysiere_lokal(photos, vorgabe)
        ergebnis.setdefault("unsicherheiten", []).append(
            "Bildmodell nicht erreichbar (%s) — lokal per Texterkennung gelesen." % exc)
        return ergebnis


def collect_photos(folder: Path) -> List[Path]:
    return sorted(
        p for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in config.IMAGE_EXTENSIONS
        and not p.name.startswith(".")
    )
