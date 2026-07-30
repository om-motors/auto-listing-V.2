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
4. Schätze die DHL-Versandstufe anhand der Teilegröße:
   Standard (Halter, Sensoren, Kleinteile, Zierleisten),
   Mittel (Scheinwerfer, Spiegel, größere Verkleidungen),
   Groß (Stoßstangen, Türverkleidungen),
   Spedition (Türen, Hauben, Kotflügel, Sitze).
   Im Zweifel die kleinere Stufe.

Antworte NUR mit einem JSON-Objekt:
{{
  "titel": "max. 80 Zeichen",
  "teilname": "...",
  "modellcodes": "...",
  "kategorie_suchbegriff": "kurzer Suchbegriff für die eBay-Kategoriewahl",
  "vergleichbare_indizes": [0, 2, 5],
  "versandstufe": "Standard | Mittel | Groß | Spedition",
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


def _preisquelle(research_result: Dict, nummer: str) -> Dict:
    """Bevorzugt tatsächlich verkaufte Artikel als Preisgrundlage."""
    verkaufte = research_result.get("verkaufte") or []
    passend = ableiten.vergleichbare_angebote(verkaufte, nummer) if verkaufte else []
    if len(passend) >= 3:
        return {"angebote": verkaufte, "indizes": passend, "quelle": "verkaufte Artikel"}
    angebote = research_result.get("angebote", [])
    return {"angebote": angebote,
            "indizes": ableiten.vergleichbare_angebote(angebote, nummer),
            "quelle": "laufende Angebote"}


def _lokal(vision_result: Dict, research_result: Dict) -> Dict:
    """Kostenlose Variante: alles aus den Vergleichstiteln ableiten."""
    angebote = research_result.get("angebote", [])
    nummer = vision_result.get("teilenummer_kompakt", "")
    hersteller = vision_result.get("hersteller")

    indizes = ableiten.vergleichbare_angebote(angebote, nummer)
    teil = ableiten.teilname(angebote, indizes) or vision_result.get("teil_vermutung")
    codes = ableiten.modellcodes(angebote, indizes)
    pos = vision_result.get("position") or ableiten.position(angebote, indizes)
    stufe = ableiten.versandstufe(teil, " ".join(
        angebote[i]["titel"] for i in indizes[:5]))

    hinweise = ["Teilname und Modellcodes wurden aus den Vergleichsangeboten "
                "abgeleitet (ohne KI) — bitte im Entwurf gegenlesen."]
    if not research_result.get("geprueft", True):
        hinweise.append("Teilenummer konnte auf eBay nicht bestätigt werden — "
                        "bitte besonders sorgfältig prüfen!")
    if len(indizes) < 3:
        hinweise.append("Nur wenige Vergleichsangebote gefunden — Preis prüfen.")

    quelle = _preisquelle(research_result, nummer)
    hinweise.append("Preis aus %d %s." % (len(quelle["indizes"]), quelle["quelle"]))
    if quelle["quelle"] == "laufende Angebote":
        hinweise.append("Das sind Wunschpreise laufender Inserate, keine "
                        "erzielten Verkäufe — im Zweifel etwas darunter bleiben.")

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

    stufe = ergebnis.get("versandstufe", "Standard")
    preise = {name: preis for name, preis, _ in config.VERSAND_STUFEN}
    ergebnis["versandpreis"] = preise.get(stufe, 7.69)
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
