"""Berichte schreiben + macOS-Benachrichtigungen."""
from __future__ import annotations

import re
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


# Formularschritt -> was der Nutzer stattdessen von Hand tun muss
SCHRITT_KLARTEXT = {
    "Zustand": "Zustand auf 'Gebraucht' stellen",
    "Beschreibung": "Beschreibung einfügen (steht unten in diesem Bericht)",
    "Merkmal 'Hersteller'": "Artikelmerkmal 'Hersteller': %(marke)s",
    "Merkmal 'Herstellernummer'": "Artikelmerkmal 'Herstellernummer': %(nummer)s",
    "Merkmal 'OE/OEM Referenznummer'": "Artikelmerkmal 'OE/OEM Referenznummer': %(nummer)s",
    "Merkmal 'Einbauposition'": "Artikelmerkmal 'Einbauposition': %(position)s",
    "Merkmal 'Produktart'": "Artikelmerkmal 'Produktart': %(teilname)s",
    "Angebot bewerben": "Anzeigentarif auf 2 %% setzen "
                        "(Knopf 'Eigenen Anzeigentarif auswählen')",
    "Rücknahme": "Rücknahme einschalten: 14 Tage Inland, Käufer zahlt Rückversand",
    "Angebotsformat": "Format auf 'Sofort-Kaufen' stellen (nicht Auktion)",
    "Kein internationaler Versand": "Schalter 'Internationaler Versand' ausschalten",
    "Versandkosten": "Versand auf %(versand)s stellen",
    "Preis": "Preis eintragen: %(preis)s",
    "Titel": "Titel eintragen",
    "Foto-Upload": "Fotos hochladen",
    "Hintergrund entfernen": "Hintergrund der Fotos im eBay-Editor entfernen",
    "Preisvorschläge": "Preisvorschläge zulassen einschalten",
}


def _ist_formularschritt(warnung: str) -> bool:
    return any(warnung.startswith(k) for k in SCHRITT_KLARTEXT)


def _grund(warnung: str, schluessel: str) -> str:
    """Den erklärenden Teil einer Warnung herausziehen.

    Ohne ihn steht im Bericht nur „Fotos hochladen" — und niemand sieht, ob
    wirklich kein Bild ankam oder ob bloß die Kontrolle ins Leere gelesen hat.
    Genau dieser Unterschied hat am 2026-08-02 zwei Reparaturen am falschen
    Ende gekostet: die Werte standen sauber im Entwurf, gemeldet wurde
    trotzdem Handarbeit.
    """
    rest = warnung[len(schluessel):].lstrip(" :").strip()
    klammer = re.search(r"\[(.+)\]\s*$", rest)
    if klammer:
        return klammer.group(1).strip()
    return rest.replace(
        "kein Fehler, aber der Wert steht nicht im Formular", "").strip(" —-–")


def _offene_schritte(warnungen: List[str], listing: Dict) -> List[str]:
    """Fehlgeschlagene Formularschritte in verständliche Aufgaben übersetzen."""
    werte = {
        "marke": listing.get("_marke") or "siehe oben",
        "nummer": listing.get("_nummer") or "siehe oben",
        "position": listing.get("einbauposition") or "—",
        "teilname": listing.get("teilname") or "—",
        "versand": "%s (%.2f €)" % (listing.get("versandstufe"),
                                    listing.get("versandpreis", 0)),
        "preis": ("%.2f €" % listing["preis"]) if listing.get("preis") else "manuell",
    }
    offen, gesehen = [], set()
    for warnung in warnungen:
        for schluessel, klartext in SCHRITT_KLARTEXT.items():
            if warnung.startswith(schluessel) and schluessel not in gesehen:
                gesehen.add(schluessel)
                try:
                    eintrag = klartext % werte
                except (KeyError, ValueError):
                    eintrag = klartext
                grund = _grund(warnung, schluessel)
                if grund:
                    eintrag = "%s — %s" % (eintrag, grund[:170])
                offen.append(eintrag)
                break
    return offen


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
    if punkte:
        lines += ["", "## Bitte prüfen"]
        lines += ["- %s" % p for p in punkte]

    # Formularschritte, die nicht durchliefen, als abhakbare Liste — das ist
    # die Arbeit, die im eBay-Entwurf noch von Hand zu erledigen ist.
    offen = _offene_schritte(draft.get("warnings", []), listing)
    if offen:
        lines += ["", "## Im eBay-Entwurf noch von Hand setzen", ""]
        lines += ["- [ ] %s" % o for o in offen]
    rest = [w for w in draft.get("warnings", []) if not _ist_formularschritt(w)]
    if rest:
        lines += ["", "## Meldungen der Automation"]
        lines += ["- %s" % w for w in rest]

    if draft.get("beschreibung"):
        lines += ["", "## Beschreibung zum Kopieren", "",
                  "```", draft["beschreibung"], "```"]

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
