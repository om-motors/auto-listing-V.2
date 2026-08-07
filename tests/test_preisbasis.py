"""Prüft, woraus der Preis gerechnet wird.

Kein neues Paket nötig — das ist das eingebaute `unittest`:

    .venv/bin/python -m unittest discover tests

Das Material sind **echte Angebotstitel** aus den Berichten unter `Berichte/`,
nicht erfundene. Wo ein Fall aus einem Bericht stammt, steht dessen Datum dabei.
"""
import unittest

from autolister import ableiten, compose


def angebote(*paare):
    return [{"titel": t, "preis": p} for t, p in paare]


# Aus Berichte/2026-08-07_2222_8K0907801J.md — Steuergerät Feststellbremse.
# Von 23 Vergleichsangeboten führten nur 13 die gesuchte Nummer; die übrigen
# waren andere Ausführungen und zogen den Preis von 22,90 € auf 24,90 €.
STEUERGERAET = angebote(
    ("Steuergerät 8K0907801L 8K0907801J 234Tkm Audi A5 B8 8T 3.0 TDI", 15.00),
    ("AUDI A5 FESTSTELLBREMSE STEUERGERÄT ECU UNIT 8K0907801J", 19.88),
    ("AUDI Q5 8R + weitere Steuergerät Handbremse 8K0907801H Parkbremse", 19.90),
    ("GENUINE 10-15 AUDI A4 B8 A5 Q5 HANDBRAKE MODULE 8K0907801J", 22.80),
    ("2L122B18 * AUDI A5 Sportback 8TA Steuergerät Handbremse 8K0907801H", 23.00),
    ("Audi Q5 8R Handbremssteuergerät 8K0907801N 2.0 Benzin 162kW 2017", 23.39),
    ("Steuergerät für Parkbremse Audi A4 8K A5 8T 8K0907801N Original", 23.99),
    ("Audi A4 B8 A5 8F Handbremsmodul Stellmotor Steuergerät 8K0907801M", 29.95),
    ("AUDI A4 B8 FESTSTELLBREMSSTEUERGERÄT 8K0907801N 8K0907801J", 35.09),
    ("AUDI A5 8T ELEKTRISCHES HANDBREMSSTEUERGERÄT 8K0907801J", 37.43),
    ("Audi A5 8T B8 07-12 Steuergerät Parkbremse Feststellbremse 8K0907801F", 39.90),
    ("Feststellbremse Parkbremse Steuergerät Audi A4 S4 B8 8K 8K0907801F", 39.95),
    ("AUDI A4 8K2, B8 Steuergerät Handbremse 8K0907801M 8K0907801J", 41.02),
    ("Audi A4 B8 Steuergerät Modul Parkbremse Handbremse 8K0907801E", 49.00),
    ("Steuergerät Parkbremse Feststellbremse 8K0907801D Für Audi A4 A5", 55.00),
)


class TrefferNachNummer(unittest.TestCase):
    """Die genaue Nummer zählt, die Lockerung ist die Ausnahme."""

    def test_fremde_nachsatzbuchstaben_zaehlen_nicht_mit(self):
        genau, gelockert = ableiten.treffer_nach_nummer(STEUERGERAET, "8K0907801J")
        self.assertEqual(len(genau), 6)
        self.assertEqual(len(gelockert), len(STEUERGERAET))
        # …801H, …801M, …801N, …801D, …801E, …801F gehören nicht dazu
        for i in genau:
            self.assertIn("8k0907801j", STEUERGERAET[i]["titel"].lower())

    def test_genug_genaue_treffer_schlagen_die_lockerung(self):
        self.assertEqual(
            ableiten.vergleichbare_angebote(STEUERGERAET, "8K0907801J"),
            ableiten.treffer_nach_nummer(STEUERGERAET, "8K0907801J")[0])

    def test_bei_zu_wenigen_wird_gelockert(self):
        """Sonst hätte ein selten angebotenes Teil gar keinen Preis.

        Echter Fall aus Berichte/2026-07-30_1706_8K0807832A.md: genau ein
        Vergleichsangebot, und das schrieb die Nummer ohne das `A`.
        """
        einzeln = angebote(
            ("Original Audi A5 Querträger Prallträger hinten 8K0807832", 129.00))
        self.assertEqual(
            ableiten.vergleichbare_angebote(einzeln, "8K0807832A"), [0])

    def test_nummer_mit_leerzeichen_gilt_als_gleich(self):
        mit_luecken = angebote(("Audi Sonnenblende rechts 8K0 857 552", 29.99))
        self.assertEqual(
            ableiten.vergleichbare_angebote(mit_luecken, "8K0857552"), [0])

    def test_ohne_treffer_bleibt_die_liste_leer(self):
        self.assertEqual(
            ableiten.vergleichbare_angebote(STEUERGERAET, "4F0959455"), [])

    def test_fremde_nummern_werden_benannt(self):
        _, gelockert = ableiten.treffer_nach_nummer(STEUERGERAET, "8K0907801J")
        genau = set(ableiten.treffer_nach_nummer(STEUERGERAET, "8K0907801J")[0])
        fremde = [i for i in gelockert if i not in genau]
        namen = ableiten.fremde_nummern(STEUERGERAET, fremde, "8K0907801J")
        self.assertIn("8K0907801H", namen)
        self.assertIn("8K0907801N", namen)
        self.assertNotIn("8K0907801J", namen)


class Preisrechnung(unittest.TestCase):
    """Der Preis folgt der schärferen Auswahl — und sagt, worauf er beruht."""

    def _listing(self, verkaufte=()):
        vision = {"teilenummer_kompakt": "8K0907801J", "hersteller": "Audi"}
        research = {"angebote": STEUERGERAET, "verkaufte": list(verkaufte),
                    "query": "8K0907801J", "geprueft": True}
        return compose._lokal(vision, research)

    def test_preis_ohne_fremde_varianten(self):
        # Median der sechs Angebote mit genau dieser Nummer: 28,95 €.
        # Über alle 15 wären es 29,90 € — die fremden Ausführungen liegen
        # überwiegend darüber und ziehen den Preis nach oben.
        self.assertEqual(self._listing()["preis"], 28.90)

    def test_fremde_varianten_bleiben_draussen(self):
        basis = " ".join(c["titel"] for c in self._listing()["preisbasis"])
        self.assertNotIn("8K0907801H", basis)
        self.assertNotIn("8K0907801D", basis)

    def test_bericht_nennt_die_fehlenden_verkaeufe(self):
        hinweise = " ".join(self._listing()["hinweise_fuer_nutzer"])
        self.assertIn("laufende Angebote", hinweise)
        self.assertIn("Verkaufte Artikel: 0 gefunden", hinweise)

    def test_lockerung_wird_im_bericht_gemeldet(self):
        """Wird gelockert, muss es im Bericht stehen — nicht stillschweigend."""
        wenige = angebote(
            ("Audi A4 B8 Steuergerät Parkbremse 8K0907801H", 40.00),
            ("Audi Q5 8R Handbremssteuergerät 8K0907801N", 24.00),
            ("Audi A5 Steuergerät Handbremse 8K0907801M", 30.00),
        )
        listing = compose._lokal(
            {"teilenummer_kompakt": "8K0907801J"},
            {"angebote": wenige, "verkaufte": [], "geprueft": True})
        hinweise = " ".join(listing["hinweise_fuer_nutzer"])
        self.assertIn("andere Ausführung", hinweise)
        self.assertIn("8K0907801H", hinweise)

    def test_benennung_nutzt_auch_die_fremden_ausfuehrungen(self):
        """Benennen und Rechnen brauchen verschiedene Auswahlen.

        Ein 8K0907801H ist ebenso ein „Steuergerät Feststellbremse" wie das
        8K0907801J — fürs Auszählen des Teilnamens sind seine Titel also
        wertvoll. Für den Preis nicht: dort entscheidet der
        Nachsatzbuchstabe über den Betrag.

        Ohne diese Trennung wäre mit der schärferen Preisauswahl auch der
        Teilname mitgeschrumpft — und „Steuergerät Feststellbremse" ist eine
        ausdrückliche Vorgabe des Nutzers (2026-08-07).
        """
        gemischt = angebote(
            ("Audi A4 B8 Modul Parkbremse 8K0907801J", 20.00),
            ("Audi A5 8T Modul Parkbremse 8K0907801J", 22.00),
            ("Audi Q5 8R Modul Parkbremse 8K0907801J", 24.00),
            ("Audi A4 B8 Steuergerät Feststellbremse Parkbremse 8K0907801H", 40.00),
            ("Audi A5 8T Steuergerät Feststellbremse Parkbremse 8K0907801H", 42.00),
            ("Audi Q5 8R Steuergerät Feststellbremse Parkbremse 8K0907801N", 44.00),
            ("Audi A4 B8 Steuergerät Feststellbremse Parkbremse 8K0907801M", 46.00),
        )
        listing = compose._lokal(
            {"teilenummer_kompakt": "8K0907801J"},
            {"angebote": gemischt, "verkaufte": [], "geprueft": True})
        # Name aus allen sieben Titeln …
        self.assertEqual(listing["teilname"], "Steuergerät Feststellbremse")
        # … Preis aber nur aus den drei mit genau dieser Nummer (Median 22 €)
        self.assertEqual(listing["preis"], 21.90)
        self.assertEqual(len(listing["preisbasis"]), 3)

    def test_verkaufte_artikel_haben_vorrang(self):
        verkaufte = angebote(
            ("Audi A4 B8 Steuergerät Feststellbremse 8K0907801J", 20.00),
            ("Audi A5 8T Steuergerät Feststellbremse 8K0907801J", 22.00),
            ("Audi Q5 8R Steuergerät Feststellbremse 8K0907801J", 24.00),
        )
        listing = self._listing(verkaufte)
        self.assertEqual(listing["preisquelle"], "verkaufte Artikel")
        self.assertEqual(listing["preis"], 21.90)


if __name__ == "__main__":
    unittest.main()
