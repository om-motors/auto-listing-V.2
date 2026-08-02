"""Arbeitet die Supabase-Warteschlange ab — der Ersatz für den `Eingang/`-Ordner.

    .venv/bin/python -m autolister.cloud_worker            # Dauerbetrieb
    .venv/bin/python -m autolister.cloud_worker --einmal   # nur die Warteschlange leeren
    .venv/bin/python -m autolister.cloud_worker --trockenlauf

Der Ablauf je Auftrag:

  1. ältesten Auftrag mit Status `neu` greifen und auf `laeuft` setzen
  2. Fotos aus dem Storage in einen Arbeitsordner laden
  3. die bestehende Pipeline laufen lassen — unverändert, mit Texterkennung,
     eBay-Recherche und Browser-Entwurf
  4. Titel, Preis, Entwurfsadresse und den Bericht zurückschreiben
  5. die Fotos im Storage löschen (die Originale liegen dann in `Erledigt/`)

Was hier **nicht** passiert: veröffentlichen. Der Arbeiter ruft dieselbe
Pipeline auf wie der Ordner-Watcher, und dort gilt die Sperre in `draft.py`
unverändert.
"""
from __future__ import annotations

import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Dict

from . import cloud, config, notify, pipeline

log = logging.getLogger("autolister")


def _arbeitsordner(auftrag: Dict) -> Path:
    ordner = config.ARBEIT / ("cloud_" + str(auftrag["id"])[:8])
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def auftrag_verarbeiten(auftrag: Dict, dry_run: bool = False) -> None:
    """Einen Auftrag von Anfang bis Ende. Fehler landen im Auftrag, nicht hier."""
    kennung = str(auftrag["id"])[:8]
    ordner = _arbeitsordner(auftrag)
    try:
        fotos = cloud.fotos_holen(auftrag.get("fotos") or [], ordner)
        if not fotos:
            raise RuntimeError("Der Auftrag enthält keine Fotos.")
        log.info("[%s] %d Foto(s) geladen", kennung, len(fotos))

        # Eine vom Handy eingetippte Teilenummer wird wie ein Ordnername
        # behandelt — `partnumber.aus_vorgabe()` erkennt selbst, ob das
        # überhaupt nach einer Nummer aussieht.
        name = (auftrag.get("bezeichnung") or "").strip() or ("cloud_" + kennung)

        ergebnis = pipeline.verarbeite_gruppe(fotos, dry_run=dry_run, name=name)

        listing = ergebnis["listing"]
        result = ergebnis["result"]
        bericht_pfad: Path = ergebnis["bericht"]
        bericht_text = ""
        try:
            bericht_text = bericht_pfad.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 — ohne Berichtstext geht es auch
            pass

        offen = notify._offene_schritte(result.get("warnings", []), listing)
        cloud.ergebnis_melden(auftrag["id"], listing, result, bericht_text, offen)
        log.info("[%s] fertig: %s | %s €", kennung, listing.get("titel"),
                 listing.get("preis"))

        # Erst jetzt aufräumen — vorher wäre bei einem Abbruch alles weg.
        cloud.fotos_loeschen(auftrag.get("fotos") or [])

    except BaseException as fehler:  # noqa: BLE001 — ein Auftrag darf nie den Dienst kippen
        log.exception("[%s] fehlgeschlagen", kennung)
        try:
            cloud.fehler_melden(auftrag["id"],
                                "%s: %s" % (type(fehler).__name__, fehler))
        except Exception:  # noqa: BLE001
            log.error("[%s] Fehler konnte nicht gemeldet werden", kennung)
        notify.notify("Auto-Listing: Auftrag fehlgeschlagen", str(fehler)[:150])
    finally:
        shutil.rmtree(ordner, ignore_errors=True)


def warteschlange_leeren(dry_run: bool = False) -> int:
    """Alle offenen Aufträge abarbeiten. Gibt zurück, wie viele es waren."""
    anzahl = 0
    while True:
        auftrag = cloud.naechster_auftrag()
        if not auftrag:
            return anzahl
        anzahl += 1
        log.info("Auftrag %s übernommen", str(auftrag["id"])[:8])
        auftrag_verarbeiten(auftrag, dry_run)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    config.ensure_dirs()

    if not cloud.eingerichtet():
        print("SUPABASE_URL und SUPABASE_SERVICE_KEY fehlen in der .env.\n"
              "Einrichtung Schritt für Schritt: siehe WEBAPP.md")
        sys.exit(1)

    dry_run = "--trockenlauf" in sys.argv
    einmal = "--einmal" in sys.argv
    if dry_run:
        log.info("TROCKENLAUF: Formulare werden ausgefüllt, aber nicht gespeichert.")

    if einmal:
        anzahl = warteschlange_leeren(dry_run)
        log.info("%d Auftrag/Aufträge verarbeitet.", anzahl)
        return

    log.info("Cloud-Arbeiter läuft — frage alle %d s nach neuen Aufträgen.",
             cloud.POLL_SEKUNDEN)
    while True:
        try:
            if warteschlange_leeren(dry_run) == 0:
                time.sleep(cloud.POLL_SEKUNDEN)
        except KeyboardInterrupt:
            log.info("Beendet.")
            return
        except Exception as fehler:  # noqa: BLE001 — Netzaussetzer dürfen nicht beenden
            log.warning("Warteschlange nicht erreichbar (%s) — neuer Versuch in %d s",
                        fehler, cloud.POLL_SEKUNDEN)
            time.sleep(cloud.POLL_SEKUNDEN)


if __name__ == "__main__":
    main()
