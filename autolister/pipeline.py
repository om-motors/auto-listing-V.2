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
from contextlib import contextmanager
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
        # Der Ordnername darf die Teilenummer vorgeben — praktisch, wenn die
        # Fotos sie nicht hergeben. Automatisch vergebene Upload-Namen
        # ("upload_20260730_135140") erkennt aus_vorgabe() als Nicht-Nummer.
        produkt.vision = vision.analyze_photos(produkt.photos, tmp, produkt.name)
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


def _umbenennen(alt: Path, neue_nummer: str) -> Path:
    """Arbeitsordner umbenennen, wenn eBay die Teilenummer korrigiert hat."""
    neu = config.ERLEDIGT / neue_nummer
    if alt == neu:
        return alt
    try:
        if neu.exists():
            for datei in alt.iterdir():
                shutil.move(str(datei), str(neu / datei.name))
            alt.rmdir()
        else:
            alt.rename(neu)
        return neu
    except Exception as exc:  # noqa: BLE001
        log.warning("Ordner konnte nicht umbenannt werden: %s", exc)
        return alt


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
                    reports.append(_process_with_browser(page, produkt, dry_run)["bericht"])
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


def _fotos_sortieren(photos: List[Path], vis: Dict) -> List[Path]:
    """Fotos so ordnen, dass das schönste Bild zuerst hochgeladen wird.

    **eBay macht das erste Foto zum Hauptbild** — und das ist das, was Käufer
    in der Suchergebnisliste sehen. Eine Nahaufnahme des Typenschilds taugt
    dafür nicht; gebraucht wird die Ansicht, auf der man das Teil erkennt.

    Die Texterkennung weiß bereits, auf welchen Bildern die Teilenummer steht
    (`_nummer_fotos`). Genau die wandern ans Ende. Die Reihenfolge innerhalb
    beider Gruppen bleibt, wie sie war.

    Zeigen *alle* Fotos die Nummer, wird nichts umsortiert — dann gibt es
    keine bessere Wahl, und eine willkürliche Umstellung wäre nur Unruhe.
    """
    mit_nummer = set(vis.get("_nummer_fotos") or [])
    ohne = [p for p in photos if str(p) not in mit_nummer]
    mit = [p for p in photos if str(p) in mit_nummer]
    if not ohne or not mit:
        return list(photos)

    # Unter den Übersichtsbildern das nach vorne, auf dem das Teil den Kasten
    # am dichtesten füllt. Das trennt die flach liegende Gesamtansicht von
    # schrägen Aufnahmen, bei denen der Kasten viel Schatten mitnimmt.
    def fuellung(pfad) -> float:
        try:
            from . import zuschnitt
            bild = images.open_normalized(pfad)
            kasten = zuschnitt.finde_kasten(bild)
            if not kasten:
                return 0.0
            flaeche = (kasten[2] - kasten[0]) * (kasten[3] - kasten[1])
            return flaeche / float(bild.size[0] * bild.size[1])
        except Exception:  # noqa: BLE001 — Reihenfolge darf nie den Lauf kippen
            return 0.0

    ohne.sort(key=fuellung, reverse=True)
    log.info("Fotoreihenfolge: '%s' als Hauptfoto, %d Bild(er) mit Teilenummer "
             "ans Ende", ohne[0].name, len(mit))
    return ohne + mit


def _process_with_browser(page, produkt: Produkt, dry_run: bool = False) -> Dict:
    """Phase 2 für ein Produkt: Recherche, Entwurfsdaten, eBay-Entwurf."""
    vis = produkt.vision
    nr = vis["teilenummer_kompakt"]

    # Hat die Texterkennung mehrere mögliche Nummern geliefert, entscheidet
    # eBay: die echte Nummer bringt Treffer, ein Lesefehler nicht. Das kostet
    # nichts und fängt genau die Fehler ab, die Muster allein nicht lösen.
    kandidaten = vis.get("_kandidaten") or []
    if kandidaten:
        res = research.pruefe_kandidaten(page, kandidaten)
        gewinner = res.get("kandidat")
        if not res.get("geprueft"):
            # Keine der gelesenen Nummern brachte auf eBay passende Treffer.
            # Jetzt trotzdem einen Entwurf zu bauen hieße, ein Inserat mit
            # falscher Teilenummer anzulegen — lieber ehrlich abbrechen und
            # den Nutzer die richtige Nummer nennen lassen.
            liste = ", ".join("%s (%.1f)" % (k.nummer, k.punkte)
                              for k in kandidaten[:6])
            raise vision.KeineTeilenummer(
                "Keine der gelesenen Teilenummern ließ sich auf eBay "
                "bestätigen. Gelesen wurde: %s. Bitte ein schärferes Foto der "
                "Nummer ergänzen — oder den Ordner nach der richtigen Nummer "
                "benennen, dann wird sie direkt verwendet." % liste)
        if gewinner and gewinner.nummer != nr:
            log.info("[%s] eBay bestätigt Teilenummer: %s -> %s",
                     produkt.name, nr, gewinner.nummer)
            vis["teilenummer"] = gewinner.formatiert
            vis["teilenummer_kompakt"] = gewinner.nummer
            vis["hersteller"] = gewinner.hersteller or vis.get("hersteller")
            nr = gewinner.nummer
            produkt.work_dir = _umbenennen(produkt.work_dir, nr)
    else:
        res = research.search_comparables(page, vis["teilenummer"], nr)

    log.info("[%s] %d Vergleichsangebote (Suche '%s')", produkt.name,
             len(res["angebote"]), res.get("query", ""))

    listing = compose.compose_listing(vis, res)
    description = compose.build_description(vis, listing)
    log.info("[%s] %s | %s €", produkt.name, listing["titel"], listing.get("preis"))

    # Werte für die Checkliste im Bericht durchreichen
    listing["_marke"] = vis.get("hersteller")
    listing["_nummer"] = vis.get("teilenummer_kompakt")

    upload_photos = images.prepare_for_upload(
        _fotos_sortieren(produkt.photos, vis), produkt.work_dir)
    result = draft.create_draft_on_page(
        page, listing, vis, description, upload_photos, produkt.work_dir, dry_run)
    result["beschreibung"] = description
    if dry_run:
        log.info("[%s] Trockenlauf — Fotos bleiben im Eingang", produkt.name)
        bericht = notify.write_report(vis, listing, res, result, produkt.photos)
    else:
        bericht = _finish(produkt, listing, res, result)
    # Die Einzelteile mitgeben, damit der Cloud-Arbeiter sie nach Supabase
    # zurückschreiben kann, ohne den Bericht wieder auseinanderzunehmen.
    return {"bericht": bericht, "vision": vis, "listing": listing,
            "result": result, "research": res}


@contextmanager
def browser_sitzung():
    """Einen Browser für mehrere Produkte offen halten.

    Für jedes Teil einen eigenen Browser zu starten kostet je rund vier
    Sekunden plus kalten eBay-Cache. Bei einem Handy-Upload mit drei Teilen
    fällt das dreimal an. Phase 2 der Pipeline macht es intern längst
    richtig — der Cloud-Arbeiter bekommt hier denselben Zugang.
    """
    with sync_playwright() as p:
        browser = draft.open_browser(p)
        page = browser.pages[0] if browser.pages else browser.new_page()
        try:
            yield page
        finally:
            browser.close()


def verarbeite_gruppe_auf_seite(page, photos: List[Path], dry_run: bool = False,
                                name: Optional[str] = None) -> Dict:
    """Wie `verarbeite_gruppe`, aber auf einer bereits offenen Seite."""
    config.ensure_dirs()
    produkt = _analyze(Produkt(
        photos=photos, name=name or photos[0].parent.name or "produkt"))
    if produkt.error:
        raise produkt.error
    return _process_with_browser(page, produkt, dry_run)


def verarbeite_gruppe(photos: List[Path], dry_run: bool = False,
                      name: Optional[str] = None) -> Dict:
    """Ein Produkt verarbeiten und **alle** Ergebnisteile zurückgeben.

    Liefert {bericht, vision, listing, result, research}. `process_group` ist
    die schmale Fassung davon und gibt nur den Berichtspfad zurück — der
    Cloud-Arbeiter braucht dagegen Titel, Preis und Entwurfsadresse einzeln,
    um sie nach Supabase zurückzuschreiben.
    """
    config.ensure_dirs()
    produkt = _analyze(Produkt(
        photos=photos, name=name or photos[0].parent.name or "produkt"))
    if produkt.error:
        raise produkt.error
    with sync_playwright() as p:
        browser = draft.open_browser(p)
        page = browser.pages[0] if browser.pages else browser.new_page()
        try:
            return _process_with_browser(page, produkt, dry_run)
        finally:
            browser.close()


def process_group(photos: List[Path], dry_run: bool = False) -> Path:
    """Ein einzelnes Produkt komplett verarbeiten (eigener Browser)."""
    return verarbeite_gruppe(photos, dry_run)["bericht"]


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
