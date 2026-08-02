"""Zugriff auf Supabase vom Mac aus — Warteschlange und Fotospeicher.

Bewusst ohne Fremdbibliothek: `urllib` aus der Standardbibliothek reicht für
die paar HTTP-Aufrufe vollkommen. Eine Abhängigkeit weniger, die kaputtgehen
oder Geld kosten kann.

**Schlüssel:** Hier wird der `service_role`-Schlüssel benutzt. Der umgeht die
Zugriffsregeln (RLS) und darf alles. Er gehört ausschließlich in die `.env`
auf diesem Mac — niemals in die Web-Seite, die ist öffentlich abrufbar.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("autolister")

SUPABASE_URL = (os.environ.get("SUPABASE_URL", "") or "").rstrip("/")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
BUCKET = os.environ.get("SUPABASE_BUCKET", "fotos")

# So lange wartet der Arbeiter zwischen zwei Blicken in die Warteschlange.
POLL_SEKUNDEN = int(os.environ.get("AUTOLISTER_POLL_SEKUNDEN", "30"))


class CloudNichtEingerichtet(RuntimeError):
    """SUPABASE_URL oder SUPABASE_SERVICE_KEY fehlen in der .env."""


def eingerichtet() -> bool:
    return bool(SUPABASE_URL and SERVICE_KEY)


def _pruefen() -> None:
    if not eingerichtet():
        raise CloudNichtEingerichtet(
            "SUPABASE_URL und SUPABASE_SERVICE_KEY müssen in der .env stehen. "
            "Siehe WEBAPP.md.")


def _anfrage(pfad: str, methode: str = "GET", daten: Optional[bytes] = None,
             kopfzeilen: Optional[Dict[str, str]] = None,
             roh: bool = False, timeout: int = 60):
    """Eine Anfrage an Supabase. Gibt geparstes JSON zurück (oder Bytes)."""
    _pruefen()
    kopf = {
        "apikey": SERVICE_KEY,
        "Authorization": "Bearer " + SERVICE_KEY,
    }
    kopf.update(kopfzeilen or {})
    anfrage = urllib.request.Request(SUPABASE_URL + pfad, data=daten,
                                     headers=kopf, method=methode)
    try:
        with urllib.request.urlopen(anfrage, timeout=timeout) as antwort:
            inhalt = antwort.read()
    except urllib.error.HTTPError as fehler:
        text = fehler.read().decode("utf-8", "replace")[:400]
        raise RuntimeError("Supabase %s %s: %s" % (methode, pfad.split("?")[0],
                                                   text or fehler.reason)) from None
    if roh:
        return inhalt
    if not inhalt:
        return None
    return json.loads(inhalt.decode("utf-8"))


# ------------------------------------------------------------ Warteschlange --

def naechster_auftrag() -> Optional[Dict]:
    """Den ältesten offenen Auftrag holen und sofort auf `laeuft` setzen.

    Das Setzen läuft als Bedingung mit (`status=eq.neu`): Kommt später ein
    zweiter Arbeiter dazu, kann er denselben Auftrag nicht ein zweites Mal
    greifen — dann liefert das PATCH eine leere Liste zurück.
    """
    offen = _anfrage("/rest/v1/auftraege?status=eq.neu"
                     "&order=erstellt_am.asc&limit=1&select=*")
    if not offen:
        return None
    auftrag = offen[0]
    belegt = _anfrage(
        "/rest/v1/auftraege?id=eq.%s&status=eq.neu" % auftrag["id"],
        methode="PATCH",
        daten=json.dumps({"status": "laeuft",
                          "begonnen_am": "now()"}).encode("utf-8"),
        kopfzeilen={"Content-Type": "application/json",
                    "Prefer": "return=representation"})
    if not belegt:
        return None  # jemand anders war schneller
    return auftrag


def ergebnis_melden(auftrag_id: str, listing: Dict, result: Dict,
                    bericht_text: str, offene_punkte: List[str]) -> None:
    """Einen fertigen Auftrag mit seinem Ergebnis abschließen."""
    _anfrage(
        "/rest/v1/auftraege?id=eq.%s" % auftrag_id, methode="PATCH",
        daten=json.dumps({
            "status": "fertig",
            "fertig_am": "now()",
            "titel": listing.get("titel"),
            "preis": listing.get("preis"),
            "versandstufe": listing.get("versandstufe"),
            "entwurf_url": result.get("draft_url"),
            "bericht": bericht_text,
            "offene_punkte": offene_punkte,
        }).encode("utf-8"),
        kopfzeilen={"Content-Type": "application/json", "Prefer": "return=minimal"})


def fehler_melden(auftrag_id: str, text: str) -> None:
    _anfrage(
        "/rest/v1/auftraege?id=eq.%s" % auftrag_id, methode="PATCH",
        daten=json.dumps({"status": "fehler", "fertig_am": "now()",
                          "fehler": text[:1000]}).encode("utf-8"),
        kopfzeilen={"Content-Type": "application/json", "Prefer": "return=minimal"})


# ------------------------------------------------------------------ Fotos ---

def foto_holen(pfad: str, ziel: Path) -> Path:
    """Ein Foto aus dem Storage in einen lokalen Ordner laden."""
    inhalt = _anfrage("/storage/v1/object/%s/%s"
                      % (BUCKET, urllib.parse.quote(pfad)), roh=True, timeout=180)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(inhalt)
    return ziel


def fotos_holen(pfade: List[str], ordner: Path) -> List[Path]:
    dateien = []
    for pfad in pfade:
        name = pfad.rsplit("/", 1)[-1]
        dateien.append(foto_holen(pfad, ordner / name))
    return dateien


def fotos_loeschen(pfade: List[str]) -> None:
    """Nach der Verarbeitung aufräumen.

    Die Originale liegen dann längst in `Erledigt/` auf dem Mac. Ohne dieses
    Aufräumen wäre das kostenlose Kontingent (1 GB) nach gut 250 Teilen voll.
    """
    for pfad in pfade:
        try:
            _anfrage("/storage/v1/object/%s/%s"
                     % (BUCKET, urllib.parse.quote(pfad)), methode="DELETE")
        except Exception as fehler:  # noqa: BLE001 — Aufräumen darf nie den Lauf kippen
            log.warning("Foto %s konnte nicht gelöscht werden: %s", pfad, fehler)
