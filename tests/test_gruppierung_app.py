"""Sichere Gruppierung fuer App-Auftraege.

Der Mitarbeiter fotografiert jedes Teil zusammenhaengend und die
Teilenummer immer als letztes Bild. Nur eine ueber eBay bestaetigte Nummer
darf deshalb eine Gruppe schliessen.
"""
from pathlib import Path
import unittest
from unittest.mock import patch

from autolister import cloud_worker, gruppieren, partnumber, research


def ocr_funde(*nummern):
    """Minimale OCR-Antwort je Foto; None bedeutet normales Produktbild."""
    return [[(nummer, 1.0)] if nummer else [] for nummer in nummern]


class GruppierungApp(unittest.TestCase):
    def setUp(self):
        self.fotos = [Path(f"{i:02}.jpg") for i in range(1, 8)]

    @staticmethod
    def bestaetige(kandidaten):
        echte = {"8K0857551", "8K0907801N"}
        return next((k for k in kandidaten if k.nummer in echte), None)

    @patch("autolister.gruppieren.ocr.lies_fotos_einzeln")
    def test_bestaetigte_nummer_schliesst_das_vorherige_produkt(self, lesen):
        lesen.return_value = ocr_funde(
            None, None, "8K0857551", None, None, None, "8K0907801N")

        gruppen = gruppieren.fuer_app(self.fotos, self.bestaetige)

        self.assertEqual(gruppen, [self.fotos[:3], self.fotos[3:]])

    @patch("autolister.gruppieren.ocr.lies_fotos_einzeln")
    def test_unbestaetigte_aufklebernummer_erzeugt_keine_grenze(self, lesen):
        lesen.return_value = ocr_funde(
            None, "4ZC532825GS", "8K0857551",
            None, "5C0010090", None, "8K0907801N")

        gruppen = gruppieren.fuer_app(self.fotos, self.bestaetige)

        self.assertEqual(gruppen, [self.fotos[:3], self.fotos[3:]])

    @patch("autolister.gruppieren.ocr.lies_fotos_einzeln")
    def test_gleiche_nummer_auf_uebersicht_und_nahaufnahme_schliesst_nur_einmal(
            self, lesen):
        lesen.return_value = ocr_funde(
            None, "8K0857551", "8K0857551", None, None, None, "8K0907801N")

        gruppen = gruppieren.fuer_app(self.fotos, self.bestaetige)

        self.assertEqual(gruppen, [self.fotos[:3], self.fotos[3:]])

    @patch("autolister.gruppieren.ocr.lies_fotos_einzeln")
    def test_fotos_hinter_letzter_nummer_stoppen_den_auftrag(self, lesen):
        lesen.return_value = ocr_funde(
            None, None, "8K0857551", None, None, None, None)

        with self.assertRaisesRegex(
                gruppieren.GruppierungUnsicher,
                "letzten bestätigten Teilenummer"):
            gruppieren.fuer_app(self.fotos, self.bestaetige)

    @patch("autolister.gruppieren.ocr.lies_fotos_einzeln")
    def test_ohne_bestaetigte_nummer_entsteht_kein_entwurf(self, lesen):
        lesen.return_value = ocr_funde(
            None, "4ZC532825GS", None, None, "5C0010090", None, None)

        with self.assertRaisesRegex(
                gruppieren.GruppierungUnsicher,
                "keine bestätigte Teilenummer"):
            gruppieren.fuer_app(self.fotos, self.bestaetige)


class EbayBestaetigung(unittest.TestCase):
    def setUp(self):
        self.kandidat = partnumber.Kandidat(
            nummer="8K0857551", formatiert="8K0 857 551",
            hersteller="Audi", punkte=5.0, quelle="8K0 857 551")

    @patch("autolister.research._suche")
    def test_nur_titel_mit_exakter_nummer_bestaetigt_den_anker(self, suche):
        suche.return_value = [
            {"titel": "Original Audi Sonnenblende 8K0857551", "preis": 30.0},
        ]

        gefunden = research.bestaetige_kandidaten(object(), [self.kandidat])

        self.assertIs(gefunden, self.kandidat)

    @patch("autolister.research._suche")
    def test_beliebige_ebay_treffer_bestaetigen_keine_nummer(self, suche):
        suche.return_value = [
            {"titel": "Audi Sonnenblende ohne Teilenummer", "preis": 30.0},
        ]

        gefunden = research.bestaetige_kandidaten(object(), [self.kandidat])

        self.assertIsNone(gefunden)

    @patch("autolister.research._suche")
    def test_anderer_suffix_bestaetigt_die_nummer_nicht(self, suche):
        suche.return_value = [
            {"titel": "Audi Sonnenblende 8K0857551A", "preis": 30.0},
        ]

        gefunden = research.bestaetige_kandidaten(object(), [self.kandidat])

        self.assertIsNone(gefunden)

    @patch("autolister.research._suche")
    def test_nummer_innerhalb_einer_laengeren_nummer_gilt_nicht(self, suche):
        suche.return_value = [
            {"titel": "Audi Teil X8K08575519", "preis": 30.0},
        ]

        gefunden = research.bestaetige_kandidaten(object(), [self.kandidat])

        self.assertIsNone(gefunden)


class CloudWorkerGruppierung(unittest.TestCase):
    @patch("autolister.research._suche")
    @patch("autolister.gruppieren.ocr.lies_fotos_einzeln")
    def test_worker_verbindet_ocr_mit_ebay_bestaetigung(self, lesen, suche):
        fotos = [Path("01.jpg"), Path("02.jpg"), Path("03.jpg")]
        lesen.return_value = ocr_funde(None, None, "8K0857551")
        suche.return_value = [
            {"titel": "Original Audi Sonnenblende 8K0857551", "preis": 30.0},
        ]

        gruppen = cloud_worker._app_gruppen(object(), fotos)

        self.assertEqual(gruppen, [fotos])

if __name__ == "__main__":
    unittest.main()
