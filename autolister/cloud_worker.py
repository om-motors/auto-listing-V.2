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

from . import cloud, config, gruppieren, notify, pipeline

log = logging.getLogger("autolister")


def _arbeitsordner(auftrag: Dict) -> Path:
    ordner = config.ARBEIT / ("cloud_" + str(auftrag["id"])[:8])
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def auftrag_verarbeiten(auftrag: Dict, dry_run: bool = False, page=None) -> None:
    """Einen Auftrag von Anfang bis Ende. Fehler landen im Auftrag, nicht hier."""
    kennung = str(auftrag["id"])[:8]
    ordner = _arbeitsordner(auftrag)
    try:
        speicherpfade = list(auftrag.get("fotos") or [])
        fotos = cloud.fotos_holen(speicherpfade, ordner)
        if not fotos:
            raise RuntimeError("Der Auftrag enthält keine Fotos.")
        log.info("[%s] %d Foto(s) geladen", kennung, len(fotos))

        # Enthält der Upload mehrere Teile? Dann für jedes weitere einen
        # eigenen Auftrag anlegen und hier nur das erste bearbeiten. So kann
        # der Nutzer einen ganzen Rundgang auf einmal hochladen.
        wo_liegt = {str(lokal): fern for lokal, fern in zip(fotos, speicherpfade)}
        gruppen = gruppieren.aufteilen(fotos)
        if len(gruppen) > 1:
            for weiteres_teil in gruppen[1:]:
                pfade = [wo_liegt[str(p)] for p in weiteres_teil if str(p) in wo_liegt]
                if pfade:
                    # Eine getippte Teilenummer gilt nur für EIN Teil — sie
                    # den übrigen mitzugeben wäre schlicht falsch.
                    cloud.auftrag_anlegen(pfade, bezeichnung=None)
            log.info("[%s] %d Teile erkannt — %d weitere(r) Auftrag angelegt",
                     kennung, len(gruppen), len(gruppen) - 1)
            fotos = gruppen[0]
            speicherpfade = [wo_liegt[str(p)] for p in fotos if str(p) in wo_liegt]

            # ⚠️ Auch in der DATENBANK auf die eigene Gruppe eindampfen.
            #
            # Vorher wurden nur die lokalen Variablen verkleinert; die Zeile in
            # `auftraege` behielt alle Fotos des Uploads. Der fertige Auftrag
            # führte damit die Bilder ALLER Teile, und in TeilePilot sah es aus,
            # als wären die Fotos falsch einsortiert.
            #
            # Am 2026-08-14 zweimal gemessen: Ein Upload mit 15 Fotos ergab
            # einen fertigen "Stoßstangenhalter" mit allen 15 Bildern und
            # daneben fünf Aufträge mit den Teilmengen. Der Nutzer sah beim
            # Steuergerät ein einzelnes Bild, während seine übrigen beim
            # Stoßstangenhalter lagen.
            try:
                cloud.auftrag_fotos_setzen(auftrag["id"], speicherpfade)
            except Exception as fehler:  # noqa: BLE001 — darf den Lauf nicht kippen
                log.warning("[%s] Fotoliste nicht eingegrenzt: %s", kennung, fehler)

        # Eine vom Handy eingetippte Teilenummer wird wie ein Ordnername
        # behandelt — `partnumber.aus_vorgabe()` erkennt selbst, ob das
        # überhaupt nach einer Nummer aussieht.
        #
        # Der Ersatzname darf dabei NICHT wie eine Teilenummer aussehen. Das
        # frühere "cloud_<id>" tat genau das: aus "cloud_45c9d009" wurde
        # "CLOUD45C9D009" — 13 Zeichen, sechs Ziffern, und damit eine gültige
        # Vorgabe. Am 2026-08-02 lief ein Auftrag deshalb mit einer erfundenen
        # Teilenummer los; gerettet hat es nur die eBay-Gegenprüfung.
        # "Handy-Auftrag <id>" ist mit über 17 Zeichen sicher außerhalb.
        name = (auftrag.get("bezeichnung") or "").strip() or ("Handy-Auftrag " + kennung)

        if page is not None:
            ergebnis = pipeline.verarbeite_gruppe_auf_seite(
                page, fotos, dry_run=dry_run, name=name)
        else:
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
        #
        # Im Trockenlauf NICHT löschen: Dort wird kein Entwurf gespeichert und
        # `_finish()` läuft nicht, die Originale landen also auch nicht in
        # `Erledigt/`. Ein Löschen im Speicher hätte die Fotos damit endgültig
        # vernichtet — ausgerechnet beim Durchlauf, der zum gefahrlosen
        # Ausprobieren gedacht ist.
        if dry_run:
            log.info("[%s] Trockenlauf — Fotos bleiben im Speicher", kennung)
        else:
            # Nur die Fotos DIESES Teils. Bei einem Upload mit mehreren Teilen
            # warten die übrigen noch in ihren eigenen Aufträgen.
            cloud.fotos_loeschen(speicherpfade)

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
    """Alle offenen Aufträge abarbeiten — in EINEM Browser.

    Ein Handy-Upload mit drei Teilen erzeugt drei Aufträge. Für jeden einen
    eigenen Browser zu starten kostet je rund vier Sekunden plus kalten
    eBay-Cache; hier fällt es einmal an.
    """
    auftrag = cloud.naechster_auftrag()
    if not auftrag:
        return 0

    anzahl = 0
    with pipeline.browser_sitzung() as page:
        while auftrag:
            anzahl += 1
            log.info("Auftrag %s übernommen", str(auftrag["id"])[:8])
            auftrag_verarbeiten(auftrag, dry_run, page=page)

            # Lebt der Browser noch? Ist er abgestürzt, würde jeder weitere
            # Auftrag sofort scheitern und fälschlich als "fehler" markiert.
            # Dann lieber abbrechen — der nächste Durchgang öffnet einen
            # frischen Browser und nimmt die Aufträge erneut.
            try:
                _ = page.url
            except Exception:
                log.warning("Browser nicht mehr ansprechbar — Rest beim "
                            "nächsten Durchgang")
                break
            auftrag = cloud.naechster_auftrag()
    return anzahl


def _selbsttest() -> bool:
    """Prüft die Einrichtung, ohne etwas zu verändern.

    Gedacht für den Moment direkt nach dem Eintragen der Schlüssel: sagt
    zuverlässig, ob der Mac die Warteschlange erreicht — und ob dort auch
    wirklich der `service_role`-Schlüssel steht und nicht versehentlich der
    öffentliche.
    """
    import base64
    import json as _json

    def zeile(ok: bool, text: str) -> None:
        print(("  OK   " if ok else "  FEHLT") + "  " + text)

    print("Selbsttest der Web-App-Anbindung\n" + "=" * 52)
    gut = True

    zeile(bool(cloud.SUPABASE_URL), "SUPABASE_URL: %s" % (cloud.SUPABASE_URL or "—"))
    gut &= bool(cloud.SUPABASE_URL)

    if not cloud.SERVICE_KEY:
        zeile(False, "SUPABASE_SERVICE_KEY ist leer")
        return False

    # Rolle aus dem Schlüssel lesen — ohne ihn auszugeben.
    rolle = "unbekannt"
    try:
        nutzlast = cloud.SERVICE_KEY.split(".")[1]
        nutzlast += "=" * (-len(nutzlast) % 4)
        rolle = _json.loads(base64.urlsafe_b64decode(nutzlast)).get("role", "?")
    except Exception:
        pass
    if rolle == "service_role":
        zeile(True, "Schlüsselrolle: service_role")
    elif rolle == "anon":
        zeile(False, "Das ist der ÖFFENTLICHE anon-Schlüssel! Der service_role-"
                     "Schlüssel steht in Supabase unter Project Settings -> API Keys.")
        return False
    else:
        # Neuere Supabase-Schlüssel (sb_secret_...) sind kein JWT — dann
        # entscheidet der Verbindungstest unten.
        zeile(True, "Schlüsselrolle nicht ablesbar (%s) — wird gleich geprüft" % rolle)

    try:
        offen = cloud._anfrage("/rest/v1/auftraege?select=id,status&limit=5")
        zeile(True, "Tabelle 'auftraege' erreichbar (%d Eintrag/Einträge sichtbar)"
                    % len(offen or []))
    except Exception as fehler:
        zeile(False, "Tabelle nicht erreichbar: %s" % fehler)
        print("\n  -> Ist supabase/schema.sql im SQL-Editor gelaufen?")
        return False

    try:
        cloud._anfrage("/storage/v1/object/list/%s" % cloud.BUCKET, methode="POST",
                       daten=_json.dumps({"prefix": "", "limit": 1}).encode("utf-8"),
                       kopfzeilen={"Content-Type": "application/json"})
        zeile(True, "Speicher-Bucket '%s' erreichbar" % cloud.BUCKET)
    except Exception as fehler:
        zeile(False, "Bucket '%s' nicht erreichbar: %s" % (cloud.BUCKET, fehler))
        return False

    print("=" * 52)
    print("Alles bereit. Fotos hochladen — der Arbeiter holt sie ab.")
    return bool(gut)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    config.ensure_dirs()

    # Vor der Vollständigkeitsprüfung: der Selbsttest soll ja gerade dann
    # etwas Brauchbares sagen, wenn noch etwas fehlt.
    if "--pruefen" in sys.argv:
        sys.exit(0 if _selbsttest() else 1)

    if not cloud.eingerichtet():
        print("SUPABASE_URL und SUPABASE_SERVICE_KEY fehlen in der .env.\n"
              "Einrichtung Schritt für Schritt: siehe WEBAPP.md")
        sys.exit(1)

    dry_run = "--trockenlauf" in sys.argv
    einmal = "--einmal" in sys.argv
    if dry_run:
        log.info("TROCKENLAUF: Formulare werden ausgefüllt, aber nicht gespeichert.")

    # Beim Start aufräumen: was noch auf `laeuft` steht, kann niemand bearbeiten
    # — der vorige Lauf ist abgestürzt oder der Mac ging aus. Ohne das bliebe
    # der Auftrag für immer liegen, weil nur `neu` gegriffen wird.
    try:
        frei = cloud.haengende_freigeben()
        if frei:
            log.info("%d steckengebliebene(r) Auftrag wieder freigegeben", frei)
    except Exception as fehler:  # noqa: BLE001 — darf den Start nicht verhindern
        log.warning("Aufräumen beim Start fehlgeschlagen: %s", fehler)

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
