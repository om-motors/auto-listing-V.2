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
import uuid
from pathlib import Path
from typing import Dict, List, Optional

# Muss VOR dem Lesen von os.environ stehen: config lädt beim Import die .env.
# Ohne diesen Import hängt es von der Reihenfolge der Importe im aufrufenden
# Modul ab, ob die Werte unten schon da sind — beim Selbsttest kam so eine
# leere SUPABASE_URL heraus, obwohl sie in der .env stand.
from . import config  # noqa: F401  (nur wegen der Nebenwirkung)

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


def auftrag_anlegen(fotos: List[str], bezeichnung: Optional[str] = None) -> None:
    """Einen weiteren Auftrag in die Warteschlange stellen.

    Gebraucht, wenn ein Upload mehrere Teile enthält: Für jedes weitere Teil
    entsteht ein eigener Auftrag. So bekommt jedes Teil auf dem Handy seine
    eigene Zeile mit Status, Preis und Entwurfslink — statt dass drei Teile
    hinter einem einzigen Eintrag verschwinden.
    """
    _anfrage("/rest/v1/auftraege", methode="POST",
             daten=json.dumps({"fotos": fotos,
                               "bezeichnung": bezeichnung}).encode("utf-8"),
             kopfzeilen={"Content-Type": "application/json",
                             "Prefer": "return=minimal"})


def auftrag_atomar_aufteilen(auftrag_id: str, gruppen: List[List[str]]) -> None:
    """Elternauftrag und alle Kindauftraege in einer DB-Transaktion trennen.

    Die Kind-IDs sind aus Eltern-ID und Gruppennummer ableitbar. Geht nur die
    Netzwerkantwort verloren, kann derselbe Aufruf daher gefahrlos wiederholt
    werden, ohne doppelte Auftraege zu erzeugen.
    """
    if len(gruppen) < 2:
        return
    namespace = uuid.UUID(str(auftrag_id))
    kinder = [
        {"id": str(uuid.uuid5(namespace, "autolisting-gruppe-%d" % i)),
         "fotos": fotos}
        for i, fotos in enumerate(gruppen[1:], start=1)
    ]
    _anfrage(
        "/rest/v1/rpc/auftrag_atomar_aufteilen", methode="POST",
        daten=json.dumps({"p_auftrag_id": str(auftrag_id),
                          "p_eltern_fotos": gruppen[0],
                          "p_kinder": kinder}).encode("utf-8"),
        kopfzeilen={"Content-Type": "application/json",
                    "Prefer": "return=minimal"})


def haengende_freigeben() -> int:
    """Steckengebliebene Aufträge zurück auf `neu` setzen. Nur beim Start!

    Ein Auftrag auf `laeuft` bedeutet: irgendwer arbeitet daran. Startet der
    Arbeiter gerade erst, kann das niemand sein — der vorige Lauf ist
    abgestürzt, wurde beendet oder der Mac ging aus. Solche Aufträge blieben
    sonst für immer liegen, denn `naechster_auftrag()` greift nur `neu`.

    **Bewusst kein Zeitlimit zur Laufzeit.** Am 2026-08-03 gemessen: zwei
    Aufträge brauchten 1 h 53 und 2 h 48 von `begonnen_am` bis `fertig_am` —
    nicht weil sie hingen, sondern weil das MacBook dazwischen schlief und
    danach weiterarbeitete. Ein Zeitlimit hätte sie mitten im Lauf
    zurückgesetzt und doppelte Entwürfe erzeugt. Beim Laptop ist die lange
    Pause der Normalfall, nicht das Warnzeichen.
    """
    zurueck = _anfrage(
        "/rest/v1/auftraege?status=eq.laeuft", methode="PATCH",
        daten=json.dumps({"status": "neu", "begonnen_am": None}).encode("utf-8"),
        kopfzeilen={"Content-Type": "application/json",
                    "Prefer": "return=representation"})
    return len(zurueck or [])


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


# ------------------------------------------------------------- Gelerntes ---

# Was die Texterkennung liest -> was in Wahrheit richtig ist.
#
# Gefuellt wird die Tabelle von zwei Seiten: In TeilePilot traegt ein Mensch
# die richtige Nummer nach, wenn ein Auftrag mangels lesbarer Nummer
# gescheitert ist; und hier lernt der Mac aus jeder eBay-Bestaetigung, die
# einen anderen Kandidaten als den erstgelesenen zum Sieger macht.
#
# Schema: siehe supabase/nummer-lernen.sql im Repo om-motors/autoteilewawi.


def _normal(nummer: str) -> str:
    """Grossbuchstaben, keine Trennzeichen — dieselbe Form wie in der App."""
    return "".join(z for z in (nummer or "").upper() if z.isalnum())


def gelernte_nummern(gelesene: List[str]) -> Dict[str, str]:
    """Nachschlagen, wie diese Fehllesungen frueher berichtigt wurden.

    Gibt {gelesen: richtig} zurueck — leer, wenn nichts bekannt ist. Ein
    Fehler beim Nachschlagen darf den Lauf nicht kippen: Dann weiss der Mac
    eben nichts und verhaelt sich wie bisher.
    """
    schluessel = [_normal(g) for g in gelesene if _normal(g)]
    if not schluessel or not eingerichtet():
        return {}
    try:
        liste = ",".join('"%s"' % s for s in sorted(set(schluessel)))
        zeilen = _anfrage("/rest/v1/nummer_lernen?select=gelesen,richtig"
                          "&gelesen=in.(%s)" % urllib.parse.quote(liste))
        return {z["gelesen"]: z["richtig"] for z in (zeilen or [])}
    except Exception as fehler:  # noqa: BLE001 — Nachschlagen ist Kuer, nicht Pflicht
        log.warning("Gelernte Nummern nicht abrufbar: %s", fehler)
        return {}


def nummer_lernen(gelesen: str, richtig: str, quelle: str = "ebay") -> None:
    """Eine Berichtigung merken.

    ⚠️ Eine Zeile mit `quelle='mensch'` wird NICHT ueberschrieben. Wer das
    Teil in der Hand hatte, hat recht; eine eBay-Vermutung darf das nicht
    verdraengen. Umgesetzt ueber die Bedingung `quelle=eq.ebay` im PATCH —
    trifft sie nicht zu, passiert schlicht nichts.
    """
    g, r = _normal(gelesen), _normal(richtig)
    if not g or not r or g == r or not eingerichtet():
        return
    daten = json.dumps({"gelesen": g, "richtig": r, "quelle": quelle,
                        "zuletzt": "now()"}).encode("utf-8")
    try:
        # on_conflict + merge-duplicates ist ein Upsert; die Bedingung, dass
        # menschliche Zeilen stehen bleiben, prueft der zweite Aufruf.
        vorhanden = _anfrage("/rest/v1/nummer_lernen?select=quelle&gelesen=eq.%s"
                             % urllib.parse.quote(g))
        if vorhanden and vorhanden[0].get("quelle") == "mensch":
            return
        _anfrage("/rest/v1/nummer_lernen", methode="POST", daten=daten,
                 kopfzeilen={"Content-Type": "application/json",
                             "Prefer": "resolution=merge-duplicates,return=minimal"})
        log.info("gelernt: %s -> %s (%s)", g, r, quelle)
    except Exception as fehler:  # noqa: BLE001 — Lernen darf nie den Lauf kippen
        log.warning("Nummer %s -> %s nicht gelernt: %s", g, r, fehler)


def auftrag_fotos_setzen(auftrag_id: str, pfade: List[str]) -> None:
    """Die Fotoliste eines Auftrags auf die eigenen Bilder eingrenzen.

    Gebraucht, wenn ein Upload mehrere Teile enthielt: Die uebrigen Gruppen
    bekommen eigene Auftraege, und dieser hier darf ihre Bilder nicht
    weiterfuehren - sonst zeigt TeilePilot an einem fertigen Entwurf die Fotos
    aller Teile des Uploads.
    """
    _anfrage(
        "/rest/v1/auftraege?id=eq.%s" % auftrag_id, methode="PATCH",
        daten=json.dumps({"fotos": pfade}).encode("utf-8"),
        kopfzeilen={"Content-Type": "application/json", "Prefer": "return=minimal"})
