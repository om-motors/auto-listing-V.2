"""Komplette Verarbeitung: Fotos -> eBay-Entwurf -> Bericht.

Aufruf für einen einzelnen Ordner:
    .venv/bin/python -m autolister.pipeline Eingang/<ordner>
Ohne Argument wird alles im Eingang verarbeitet (so wie es der Watcher tut).

Ablauf und warum er so schnell ist:

  Phase 1 (parallel, ohne Browser)
      Alle Produkte werden gleichzeitig analysiert. Die Bildanalyse ist
      reine Wartezeit auf die API — vier Produkte parallel brauchen kaum
      länger als eines.

  Phase 2 (ein Browser für alles)
      Recherche und Entwurf laufen seriell in EINEM Browserfenster.
      Früher startete pro Produkt ein neuer Browser (~4 s Startzeit,
      kalter eBay-Cache); jetzt fällt das genau einmal an.
"""
from __future__ import annotations

import logging
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from playwright.sync_api import sync_playwright

from . import compose, config, draft, images, notify, research, vision

log = logging.getLogger("autolister")


@dataclass
class Produkt:
    """Ein Produkt auf seinem Weg durch die Pipeline."""
    photos: List[Path]
    name: str
    work_dir: Optional[Path] = None
    vision: Dict = field(default_factory=dict)
    error: Optional[BaseException] = None


def find_product_groups(eingang: Path) -> List[Produkt]:
    """Produkte im Eingang finden: jeder Unterordner = ein Produkt,
    lose Fotos direkt im Eingang = zusammen ein Produkt."""
    produkte: List[Produkt] = []
    loose: List[Path] = []
    for entry in sorted(eingang.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            photos = vision.collect_photos(entry)
            if photos:
                produkte.append(Produkt(photos=photos, name=entry.name))
        elif entry.suffix.lower() in config.IMAGE_EXTENSIONS:
            loose.append(entry)
    if loose:
        produkte.append(Produkt(photos=sorted(loose), name="lose_fotos"))
    return produkte


def _analyze(produkt: Produkt) -> Produkt:
    """Phase 1 für ein Produkt: Fotos analysieren (läuft im Thread-Pool)."""
    tmp = config.ARBEIT / produkt.name
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        produkt.vision = vision.analyze_photos(produkt.photos, tmp)
        nr = produkt.vision["teilenummer_kompakt"]
        produkt.work_dir = config.ERLEDIGT / nr
        produkt.work_dir.mkdir(parents=True, exist_ok=True)
        # aufbereitete Kopien in den endgültigen Arbeitsordner mitnehmen
        for sub in ("_analyse", "_upload"):
            src = tmp / sub
            if src.exists():
                shutil.move(str(src), str(produkt.work_dir / sub))
        log.info("[%s] Teilenummer %s (Konfidenz: %s)", produkt.name,
                 produkt.vision["teilenummer"],
                 produkt.vision.get("konfidenz_teilenummer"))
    except BaseException as exc:  # noqa: BLE001 — Fehler pro Produkt isolieren
        produkt.error = exc
        log.error("[%s] Bildanalyse fehlgeschlagen: %s", produkt.name, exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return produkt


def _finish(produkt: Produkt, listing: Dict, res: Dict, result: Dict) -> Path:
    """Fotos ablegen, Bericht schreiben, Bescheid geben."""
    for photo in produkt.photos:
        target = produkt.work_dir / photo.name
        if photo.exists():
            shutil.move(str(photo), str(target))
    group_dir = produkt.photos[0].parent
    if group_dir != config.EINGANG and group_dir.exists() and not any(group_dir.iterdir()):
        group_dir.rmdir()

    report = notify.write_report(produkt.vision, listing, res, result, produkt.photos)
    preis = ("%.2f €" % listing["preis"]) if listing.get("preis") else "Preis manuell!"
    notify.notify("eBay-Entwurf gespeichert", "%s — %s" % (listing["titel"][:60], preis))
    log.info("[%s] Fertig: %s", produkt.name, report)
    return report


def _handle_error(produkt: Produkt, exc: BaseException) -> None:
    shot = getattr(exc, "screenshot", None)
    notify.write_error(produkt.name, "%s: %s" % (type(exc).__name__, exc),
                       str(shot) if shot else None)
    notify.notify("Auto-Listing: Fehler", "%s: %s" % (produkt.name, str(exc)[:150]))
    log.exception("[%s] Fehler", produkt.name)


def process_all(dry_run: bool = False) -> List[Path]:
    """Alles im Eingang verarbeiten. Gibt die geschriebenen Berichte zurück."""
    config.ensure_dirs()
    produkte = find_product_groups(config.EINGANG)
    if not produkte:
        log.info("Eingang ist leer — nichts zu tun.")
        return []

    start = time.time()
    log.info("%d Produkt(e) gefunden", len(produkte))

    # --- Phase 1: alle Bildanalysen parallel ---------------------------------
    workers = min(config.VISION_PARALLEL, len(produkte))
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            produkte = list(pool.map(_analyze, produkte))
    else:
        produkte = [_analyze(p) for p in produkte]

    for p in produkte:
        if p.error:
            _handle_error(p, p.error)
    fertig = [p for p in produkte if not p.error]
    if not fertig:
        return []
    log.info("Bildanalyse abgeschlossen (%.1f s) — starte Browser", time.time() - start)

    # --- Phase 2: ein Browser für Recherche + alle Entwürfe -------------------
    reports: List[Path] = []
    with sync_playwright() as p:
        browser = draft.open_browser(p)
        page = browser.pages[0] if browser.pages else browser.new_page()
        try:
            try:
                eingeloggt = draft.check_logged_in(page)
            except draft.CaptchaBlocked as exc:
                notify.write_error("captcha", str(exc))
                notify.notify("Auto-Listing: Sicherheitsabfrage", str(exc)[:150])
                log.error("%s", exc)
                return []
            if not eingeloggt:
                exc = draft.NotLoggedIn(
                    "Nicht bei eBay eingeloggt. Bitte einmalig ausführen: "
                    ".venv/bin/python -m autolister.login"
                )
                notify.write_error("login", str(exc))
                notify.notify("Auto-Listing: eBay-Login nötig", str(exc))
                log.error("%s", exc)
                return []

            for produkt in fertig:
                try:
                    reports.append(_process_with_browser(page, produkt, dry_run))
                except (draft.NotLoggedIn, draft.CaptchaBlocked) as exc:
                    # Beides betrifft die Sitzung, nicht das einzelne Produkt —
                    # die übrigen Produkte hätten dasselbe Problem.
                    notify.write_error(produkt.name, str(exc))
                    notify.notify("Auto-Listing: Eingriff nötig", str(exc)[:150])
                    log.error("%s", exc)
                    break
                except BaseException as exc:  # noqa: BLE001
                    _handle_error(produkt, exc)
        finally:
            browser.close()

    log.info("Durchlauf beendet: %d/%d Entwürfe in %.1f s",
             len(reports), len(produkte), time.time() - start)
    return reports


def _process_with_browser(page, produkt: Produkt, dry_run: bool = False) -> Path:
    """Phase 2 für ein Produkt: Recherche, Entwurfsdaten, eBay-Entwurf."""
    vis = produkt.vision
    nr = vis["teilenummer_kompakt"]

    res = research.search_comparables(page, vis["teilenummer"], nr)
    log.info("[%s] %d Vergleichsangebote (Suche '%s')", produkt.name,
             len(res["angebote"]), res["query"])

    listing = compose.compose_listing(vis, res)
    description = compose.build_description(vis, listing)
    log.info("[%s] %s | %s €", produkt.name, listing["titel"], listing.get("preis"))

    upload_photos = images.prepare_for_upload(produkt.photos, produkt.work_dir)
    result = draft.create_draft_on_page(
        page, listing, vis, description, upload_photos, produkt.work_dir, dry_run)
    if dry_run:
        log.info("[%s] Trockenlauf — Fotos bleiben im Eingang", produkt.name)
        return notify.write_report(vis, listing, res, result, produkt.photos)
    return _finish(produkt, listing, res, result)


def process_group(photos: List[Path], dry_run: bool = False) -> Path:
    """Ein einzelnes Produkt komplett verarbeiten (eigener Browser)."""
    config.ensure_dirs()
    produkt = _analyze(Produkt(photos=photos, name=photos[0].parent.name or "produkt"))
    if produkt.error:
        raise produkt.error
    with sync_playwright() as p:
        browser = draft.open_browser(p)
        page = browser.pages[0] if browser.pages else browser.new_page()
        try:
            return _process_with_browser(page, produkt, dry_run)
        finally:
            browser.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    args = [a for a in sys.argv[1:] if a != "--trockenlauf"]
    dry_run = "--trockenlauf" in sys.argv
    if dry_run:
        log.info("TROCKENLAUF: Formular wird ausgefüllt, aber nicht gespeichert.")
    if args:
        folder = Path(args[0]).resolve()
        photos = vision.collect_photos(folder)
        if not photos:
            print("Keine Fotos in", folder)
            sys.exit(1)
        process_group(photos, dry_run)
    else:
        process_all(dry_run)


if __name__ == "__main__":
    main()
