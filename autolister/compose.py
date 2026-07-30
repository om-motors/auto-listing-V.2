"""Schritt 3: Entwurfsdaten erstellen — Titel, Preis, Kategorie, Merkmale."""
from __future__ import annotations

import json
import statistics
from typing import Dict

from . import config, llm

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
  "kategorie_suchbegriff": "kurzer Suchbegriff für die eBay-Kategoriewahl, z.B. 'Stoßstangenhalter'",
  "vergleichbare_indizes": [0, 2, 5],
  "versandstufe": "Standard | Mittel | Groß | Spedition",
  "einbauposition": "vorne links | ... oder null",
  "hinweise_fuer_nutzer": ["offene Punkte, die der Nutzer prüfen sollte"]
}}"""


def compose_listing(vision_result: Dict, research_result: Dict) -> Dict:
    comps = research_result.get("angebote", [])
    comps_text = "\n".join(
        "%d. %s — %.2f EUR" % (i, c["titel"], c["preis"]) for i, c in enumerate(comps)
    ) or "(keine Treffer)"

    prompt = PROMPT.format(
        vision=json.dumps(vision_result, ensure_ascii=False, indent=2),
        query=research_result.get("query", ""),
        comps=comps_text,
    )
    result = llm.ask_json(prompt)

    # Preis deterministisch aus den ausgewählten Vergleichsangeboten rechnen
    indices = [i for i in result.get("vergleichbare_indizes", []) if 0 <= i < len(comps)]
    prices = sorted(comps[i]["preis"] for i in indices)
    if len(prices) >= 5:  # Ausreißer oben/unten kappen
        prices = prices[1:-1]
    if prices:
        preis = round(statistics.mean(prices), 0) - 0.10  # z.B. 34.90
        result["preis"] = max(preis, 1.0)
        result["preisbasis"] = [
            {"titel": comps[i]["titel"], "preis": comps[i]["preis"]} for i in indices
        ]
    else:
        result["preis"] = None
        result["preisbasis"] = []
        result.setdefault("hinweise_fuer_nutzer", []).append(
            "Keine Vergleichsangebote gefunden — Preis manuell festlegen!"
        )

    if len(result.get("titel", "")) > 80:
        result["titel"] = result["titel"][:80].rstrip()

    stufe = result.get("versandstufe", "Standard")
    preise = {name: preis for name, preis, _ in config.VERSAND_STUFEN}
    result["versandpreis"] = preise.get(stufe, 7.69)
    return result


def build_description(vision_result: Dict, listing: Dict) -> str:
    template = (config.VORLAGEN / "beschreibung.md").read_text(encoding="utf-8")
    # Nur den eigentlichen Vorlagentext unterhalb der Trennlinie verwenden
    if "---" in template:
        template = template.split("---", 1)[1].strip()
    text = (
        template
        .replace("<Marke>", vision_result.get("hersteller") or "")
        .replace("<Teilname>", listing.get("teilname") or "")
        .replace("<Position>", listing.get("einbauposition") or "")
        .replace("<Teilenummer>", vision_result.get("teilenummer") or "")
    )
    # doppelte Leerzeichen aus leeren Platzhaltern entfernen
    return "\n".join(" ".join(line.split()) for line in text.splitlines())
