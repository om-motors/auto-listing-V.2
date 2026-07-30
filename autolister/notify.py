"""Berichte schreiben + macOS-Benachrichtigungen."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import config


def notify(title: str, message: str) -> None:
    try:
        script = 'display notification "%s" with title "%s" sound name "Glass"' % (
            message.replace('"', "'")[:200], title.replace('"', "'")[:60])
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except Exception:
        pass


def write_report(vision: Dict, listing: Dict, research: Dict,
                 draft: Dict, photos: List[Path]) -> Path:
    config.ensure_dirs()
    nr = vision.get("teilenummer_kompakt", "unbekannt")
    path = config.BERICHTE / ("%s_%s.md" % (time.strftime("%Y-%m-%d_%H%M"), nr))

    spanne = listing.get("preisspanne")
    lines = [
        "# Entwurf: %s" % listing.get("titel", nr),
        "",
        "- **Teilenummer:** %s (Konfidenz: %s)" % (
            vision.get("teilenummer"), vision.get("konfidenz_teilenummer")),
        "- **Hersteller:** %s" % (vision.get("hersteller") or "—"),
        "- **Preis:** %s €%s" % (
            ("%.2f" % listing["preis"]) if listing.get("preis") else "MANUELL SETZEN",
            ("  (Markt: %.0f–%.0f €)" % spanne) if spanne else ""),
        "- **Preisquelle:** %s" % listing.get("preisquelle", "—"),
        "- **Versand:** %s (%.2f €) — geschätzt, bitte prüfen" % (
            listing.get("versandstufe"), listing.get("versandpreis", 0)),
        "- **Entwurf:** %s" % draft.get("draft_url", "—"),
        "- **Fotos:** %d Stück" % len(photos),
        "- **Betriebsart:** %s%s" % (
            config.aktiver_modus(),
            " (kostenlos)" if config.aktiver_modus() == "lokal" else ""),
        "",
        "## Preisbasis (Suche: \"%s\")" % research.get("query", ""),
    ]
    for c in listing.get("preisbasis", []):
        lines.append("- %.2f € — %s" % (c["preis"], c["titel"]))
    if not listing.get("preisbasis"):
        lines.append("- keine —")

    kandidaten = vision.get("_kandidaten") or []
    if len(kandidaten) > 1:
        lines += ["", "## Gelesene Teilenummer-Kandidaten"]
        for k in kandidaten[:5]:
            marker = " **<- gewählt**" if k.nummer == nr else ""
            lines.append("- `%s` (%.1f Punkte, aus %r)%s" % (
                k.nummer, k.punkte, k.quelle, marker))

    punkte = list(listing.get("hinweise_fuer_nutzer", []))
    punkte += list(vision.get("unsicherheiten", []))
    punkte += ["(Automation) " + w for w in draft.get("warnings", [])]
    if punkte:
        lines += ["", "## Bitte prüfen"]
        lines += ["- %s" % p for p in punkte]

    if draft.get("screenshot"):
        lines += ["", "![Vorschau](%s)" % draft["screenshot"]]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_error(group_name: str, error: str, screenshot: Optional[str] = None) -> Path:
    config.ensure_dirs()
    path = config.FEHLER / ("%s_%s.md" % (time.strftime("%Y-%m-%d_%H%M"), group_name))
    body = "# Fehler bei '%s'\n\n```\n%s\n```\n" % (group_name, error)
    if screenshot:
        body += "\n![Screenshot](%s)\n" % screenshot
    path.write_text(body, encoding="utf-8")
    return path
