"""Schritt 3: Entwurfsdaten erstellen — Titel, Preis, Versandstufe.

Zwei Wege zum selben Ergebnis:

* **lokal** (Standard, kostenlos): Teilname und Modellcodes werden aus den
  eBay-Vergleichstiteln ausgezählt (`ableiten.py`). Kein Sprachmodell, keine
  Kosten, keine Internetabhängigkeit über eBay hinaus.
* **KI** (optional, kostenpflichtig): ein Modell wählt die vergleichbaren
  Angebote aus und formuliert Teilname und Modellcodes.

Der **Preis wird in beiden Fällen von Python gerechnet**, nie geschätzt: Aus
den vergleichbaren Angeboten wird der Mittelwert gebildet, nachdem oben und
unten je ein Ausreißer entfernt wurde.
"""
from __future__ import annotations

import json
import logging
import statistics
from typing import Dict, List, Optional

from . import ableiten, config, llm

log = logging.getLogger("autolister")

PROMPT = """Du erstellst einen eBay.de-Entwurf für ein gebrauchtes Original-Kfz-Teil.

Analyse der Fotos:
{vision}

Aktive Vergleichsangebote auf eBay.de (Suche: "{query}"):
{comps}

Aufgaben:
1. Leite aus den Vergleichsangeboten den korrekten TEILNAMEN ab
   (z.B. "Halter Stoßfänger vorne links") und die passenden
   FAHRZEUGE/MODELLCODES (z.B. "Audi A5 8T 8TA 8F").
2. Wähle die Indizes der Angebote, die wirklich vergleichbar sind
   (gleiches Teil, gebraucht, Original — keine Nachbauten, keine
   Ausreißer, keine anderen Teilenummern).
3. Baue den TITEL (max. 80 Zeichen!) nach dem Muster:
   Original <Marke> <Modellcodes> <Teilname> <Position> <TeilenummerKompakt>
4. Schätze die Versandstufe anhand der Teilegröße. Es gibt GENAU DREI:
   Standard (Halter, Sensoren, Kleinteile, Zierleisten, Blenden),
   Mittel (Scheinwerfer, Spiegel, größere Verkleidungen),
   Spedition (alles Sperrige: Stoßstangen, Träger, Türen, Hauben,
              Kotflügel, Sitze, Türverkleidungen).
   Die frühere Stufe "Groß" gibt es nicht mehr — sie ist in "Spedition"
   aufgegangen. Im Zweifel die kleinere Stufe.

Antworte NUR mit einem JSON-Objekt:
{{
  "titel": "max. 80 Zeichen",
  "teilname": "...",
  "modellcodes": "...",
  "kategorie_suchbegriff": "kurzer Suchbegriff für die eBay-Kategoriewahl",
  "vergleichbare_indizes": [0, 2, 5],
  "versandstufe": "Standard | Mittel | Spedition",
  "einbauposition": "vorne links | ... oder null",
  "hinweise_fuer_nutzer": ["offene Punkte, die der Nutzer prüfen sollte"]
}}"""


def _preis_rechnen(angebote: List[Dict], indizes: List[int]) -> Dict:
    """Preis deterministisch aus den vergleichbaren Angeboten bilden.

    Es wird der **Median** genommen, nicht der Mittelwert. Der Markt für
    gebrauchte Teile ist stark gespreizt: private Verkäufer und
    Export-Verwerter unterscheiden sich beim selben Teil um das Zehnfache
    (gemessen an einem echten Differential: 140 € bis 1236 €). Ein
    Mittelwert wird von den teuren Händlerangeboten nach oben gezogen, der
    Median bleibt dort, wo der Markt tatsächlich ist.

    Zusätzlich werden Angebote außerhalb des 1,5-fachen Quartilsabstands
    verworfen — das sind Fehlpreise und falsch zugeordnete Teile.
    """
    gueltig = [i for i in indizes if 0 <= i < len(angebote)]
    if not gueltig:
        return {"preis": None, "preisbasis": [], "preisspanne": None}

    paare = sorted(((angebote[i]["preis"], i) for i in gueltig), key=lambda p: p[0])
    preise = [p for p, _ in paare]

    if len(preise) >= 4:
        q1 = statistics.quantiles(preise, n=4)[0]
        q3 = statistics.quantiles(preise, n=4)[2]
        spanne = q3 - q1
        unten, oben = q1 - 1.5 * spanne, q3 + 1.5 * spanne
        behalten = [(p, i) for p, i in paare if unten <= p <= oben]
        if len(behalten) >= 3:
            paare = behalten
            preise = [p for p, _ in paare]

    preis = max(round(statistics.median(preise), 0) - 0.10, 1.0)
    return {
        "preis": preis,
        "preisspanne": (min(preise), max(preise)),
        "preisbasis": [
            {"titel": angebote[i]["titel"], "preis": p} for p, i in paare
        ],
    }


def _quelle(angebote: List[Dict], indizes: List[int], name: str,
            nummer: str, **rest) -> Dict:
    """Eine Preisgrundlage samt Auskunft darüber, woraus sie besteht."""
    genau = set(ableiten.treffer_nach_nummer(angebote, nummer)[0])
    fremde = [i for i in indizes if i not in genau]
    ergebnis = {
        "angebote": angebote,
        "indizes": indizes,
        "quelle": name,
        "fremde_anzahl": len(fremde),
        "fremde_nummern": ableiten.fremde_nummern(angebote, fremde, nummer),
    }
    ergebnis.update(rest)
    return ergebnis


def _preisquelle(research_result: Dict, nummer: str) -> Dict:
    """Bevorzugt tatsächlich verkaufte Artikel als Preisgrundlage."""
    verkaufte = research_result.get("verkaufte") or []
    passend = ableiten.vergleichbare_angebote(verkaufte, nummer) if verkaufte else []
    if len(passend) >= 3:
        return _quelle(verkaufte, passend, "verkaufte Artikel", nummer)
    angebote = research_result.get("angebote", [])
    return _quelle(angebote, ableiten.vergleichbare_angebote(angebote, nummer),
                   "laufende Angebote", nummer,
                   verkaufte_gefunden=len(verkaufte),
                   verkaufte_passend=len(passend))


def _lokal(vision_result: Dict, research_result: Dict) -> Dict:
    """Kostenlose Variante: alles aus den Vergleichstiteln ableiten."""
    angebote = research_result.get("angebote", [])
    nummer = vision_result.get("teilenummer_kompakt", "")
    hersteller = vision_result.get("hersteller")

    indizes = ableiten.vergleichbare_angebote(angebote, nummer)

    # **Benennen und Rechnen brauchen verschiedene Auswahlen.**
    # Zum Benennen zählen die anderen Ausführungen mit: ein 8K0907801H ist
    # genauso ein „Steuergerät Feststellbremse" wie das 8K0907801J, und je
    # mehr Titel mitzählen, desto stabiler das Auszählen. Beim Preis ist es
    # umgekehrt — dort entscheidet der Nachsatzbuchstabe über den Betrag,
    # deshalb hält `_preisquelle()` die Auswahl dort eng.
    benennung = ableiten.treffer_nach_nummer(angebote, nummer)[1] or indizes
    teil = ableiten.teilname(angebote, benennung) or vision_result.get("teil_vermutung")
    codes = ableiten.modellcodes(angebote, benennung)
    # Steht auf dem Teil nur das Logo, kennt die Texterkennung die Marke nicht.
    # Die Vergleichstitel nennen sie fast immer.
    if not hersteller or hersteller in ("Audi/VW",):
        hersteller = ableiten.hersteller(angebote, benennung) or hersteller
        # zurückschreiben, damit Beschreibung und Bericht dieselbe Marke nennen
        if hersteller:
            vision_result["hersteller"] = hersteller
    pos = vision_result.get("position") or ableiten.position(angebote, benennung)
    stufe = ableiten.versandstufe(teil, " ".join(
        angebote[i]["titel"] for i in benennung[:5]))

    hinweise = ["Teilname und Modellcodes wurden aus den Vergleichsangeboten "
                "abgeleitet (ohne KI) — bitte im Entwurf gegenlesen."]
    if not research_result.get("geprueft", True):
        hinweise.append("Teilenummer konnte auf eBay nicht bestätigt werden — "
                        "bitte besonders sorgfältig prüfen!")
    if len(indizes) == 1:
        hinweise.append("ACHTUNG: Nur EIN Angebot mit dieser Teilenummer "
                        "gefunden. Der Preis beruht damit auf einem einzigen "
                        "Verkäufer — unbedingt selbst einschätzen.")
    elif len(indizes) < 4:
        hinweise.append("Nur %d Vergleichsangebote — Preis ist wenig "
                        "abgesichert, bitte prüfen." % len(indizes))

    quelle = _preisquelle(research_result, nummer)
    hinweise.append("Preis aus %d %s." % (len(quelle["indizes"]), quelle["quelle"]))
    if quelle["fremde_anzahl"]:
        hinweise.append(
            "ACHTUNG: Davon führen %d Angebote eine andere Ausführung (%s) — "
            "mit genau %s gab es zu wenige. Ein anderer Nachsatzbuchstabe ist "
            "ein anderes Teil und kostet anders; Preis nur grob."
            % (quelle["fremde_anzahl"],
               ", ".join(quelle["fremde_nummern"][:3]) or "anderer Nachsatz",
               nummer))
    if quelle["quelle"] == "laufende Angebote":
        hinweise.append(
            "Das sind Wunschpreise laufender Inserate, keine erzielten "
            "Verkäufe — im Zweifel etwas darunter bleiben. (Verkaufte "
            "Artikel: %d gefunden, %d mit dieser Nummer — nötig sind 3.)"
            % (quelle.get("verkaufte_gefunden", 0),
               quelle.get("verkaufte_passend", 0)))

    ergebnis = {
        "titel": ableiten.baue_titel(hersteller, codes, teil, pos, nummer),
        "teilname": teil,
        "modellcodes": codes,
        "kategorie_suchbegriff": teil or nummer,
        "vergleichbare_indizes": indizes,
        "versandstufe": stufe,
        "einbauposition": pos,
        "hinweise_fuer_nutzer": hinweise,
        "preisquelle": quelle["quelle"],
    }
    ergebnis.update(_preis_rechnen(quelle["angebote"], quelle["indizes"]))
    return ergebnis


def _mit_ki(vision_result: Dict, research_result: Dict) -> Dict:
    """Optionale Variante mit Sprachmodell (kostenpflichtig bzw. Abo)."""
    angebote = research_result.get("angebote", [])
    comps_text = "\n".join(
        "%d. %s — %.2f EUR" % (i, c["titel"], c["preis"]) for i, c in enumerate(angebote)
    ) or "(keine Treffer)"

    prompt = PROMPT.format(
        vision=json.dumps(vision_result, ensure_ascii=False, indent=2),
        query=research_result.get("query", ""),
        comps=comps_text,
    )
    ergebnis = llm.ask_json(prompt)
    ergebnis.update(_preis_rechnen(angebote, ergebnis.get("vergleichbare_indizes", [])))
    return ergebnis


def compose_listing(vision_result: Dict, research_result: Dict) -> Dict:
    """Entwurfsdaten erstellen — je nach Betriebsart lokal oder mit KI."""
    modus = config.aktiver_modus()
    if modus == "lokal":
        ergebnis = _lokal(vision_result, research_result)
    else:
        try:
            ergebnis = _mit_ki(vision_result, research_result)
        except Exception as exc:  # noqa: BLE001
            log.warning("KI-Aufbereitung fehlgeschlagen (%s) — nutze lokale Regeln", exc)
            ergebnis = _lokal(vision_result, research_result)
            ergebnis.setdefault("hinweise_fuer_nutzer", []).append(
                "KI nicht erreichbar (%s) — lokal abgeleitet." % exc)

    if ergebnis.get("preis") is None:
        ergebnis.setdefault("hinweise_fuer_nutzer", []).append(
            "Keine Vergleichsangebote gefunden — Preis manuell festlegen!")

    titel = ergebnis.get("titel") or ""
    if len(titel) > 80:
        ergebnis["titel"] = titel[:80].rstrip()

    # Eine unbekannte Stufe darf NICHT stillschweigend zur billigsten werden.
    # Vorher fiel ein vom Modell geliefertes "Groß" über den Default auf 7,69 €
    # zurück — eine Stoßstange wäre so mit Standardversand inseriert worden.
    # Jetzt: teuerste Stufe nehmen und den Nutzer darauf hinweisen.
    stufe = ergebnis.get("versandstufe") or "Standard"
    preise = {name: preis for name, preis, _ in config.VERSAND_STUFEN}
    if stufe not in preise:
        teuerste = max(config.VERSAND_STUFEN, key=lambda s: s[1])
        ergebnis.setdefault("hinweise_fuer_nutzer", []).append(
            "Unbekannte Versandstufe %r — vorsichtshalber '%s' (%.2f €) gesetzt. "
            "Bitte im Entwurf prüfen." % (stufe, teuerste[0], teuerste[1]))
        stufe = teuerste[0]
        ergebnis["versandstufe"] = stufe
    ergebnis["versandpreis"] = preise[stufe]
    return ergebnis


def build_description(vision_result: Dict, listing: Dict) -> str:
    template = (config.VORLAGEN / "beschreibung.md").read_text(encoding="utf-8")
    if "---" in template:
        template = template.split("---", 1)[1].strip()
    text = (
        template
        .replace("<Marke>", vision_result.get("hersteller") or "")
        .replace("<Teilname>", listing.get("teilname") or "")
        .replace("<Position>", listing.get("einbauposition") or "")
        .replace("<Teilenummer>", vision_result.get("teilenummer") or "")
    )
    return "\n".join(" ".join(zeile.split()) for zeile in text.splitlines())
