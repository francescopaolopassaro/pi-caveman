# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Tests for the STATISTICAL and SYNTACTIC compression levels ported from Caveman C# 1.4.1."""
from __future__ import annotations

import pytest

from synthelion.core import CompressionService
from synthelion.models import CompressionLevel

ALL_LEVELS = [
    CompressionLevel.LIGHT,
    CompressionLevel.SEMANTIC,
    CompressionLevel.AGGRESSIVE,
    CompressionLevel.STATISTICAL,
    CompressionLevel.SYNTACTIC,
]


@pytest.fixture(scope="module")
def svc() -> CompressionService:
    return CompressionService()


class TestNeverCrashOrEmpty:
    @pytest.mark.parametrize("text", ["", "   ", "!!! ??? ...", "This is that which it was."])
    def test_degenerate_input_never_throws(self, svc, text):
        for level in ALL_LEVELS:
            svc.apply_compression(text, "eng", level)  # must not raise

    def test_unsupported_language_never_throws(self, svc):
        text = "Bonjou tout moun, kijan ou ye jodi a."
        for level in ALL_LEVELS:
            svc.apply_compression(text, "hat", level)  # no curated/POS data for Haitian Creole

    @pytest.mark.parametrize("level", [CompressionLevel.AGGRESSIVE, CompressionLevel.STATISTICAL, CompressionLevel.SYNTACTIC])
    def test_never_empties_content_bearing_input(self, svc, level):
        r = svc.apply_compression("Vai.", "ita", level)
        assert r.compressed_text != ""


class TestMainVerbSurvives:
    """Regression guard for the Italian "-are" suffix bug (also fixed in this port):
    "-are" matched every first-conjugation infinitive verb ("analizzare") as if it were
    a "-are" adjective ("regolare"), silently deleting the sentence's main verb.
    """

    TEXT = ("Ti chiedo cortesemente di analizzare con estrema attenzione questo report "
            "finanziario molto dettagliato.")

    @pytest.mark.parametrize("level", [CompressionLevel.AGGRESSIVE, CompressionLevel.STATISTICAL, CompressionLevel.SYNTACTIC])
    def test_verb_and_key_nouns_survive(self, svc, level):
        r = svc.apply_compression(self.TEXT, "ita", level)
        text = r.compressed_text.lower()
        assert "analizzare" in text, f"{level} dropped the sentence's main verb: {r.compressed_text!r}"
        assert "report" in text
        assert "finanziario" in text


class TestFinancialAdjectiveSurvives:
    """Regression guard for the English "-al"/"-ical" suffix bug: caught decorative
    adjectives but just as often a domain-specifying one ("financial" report != "legal"
    report) — found by the comprehensibility test suite in Caveman C# 1.4.1.
    """

    TEXT = "Could you please kindly review the attached quarterly financial report from Q3."

    @pytest.mark.parametrize("level", [CompressionLevel.AGGRESSIVE, CompressionLevel.SYNTACTIC])
    def test_financial_survives(self, svc, level):
        r = svc.apply_compression(self.TEXT, "eng", level)
        assert "financial" in r.compressed_text.lower()


class TestSyntacticPosGatedElision:
    def test_coordination_never_mistaken_for_hedge_clause(self, svc):
        it = svc.apply_compression("Ho comprato il pane e mangiato la torta.", "ita", CompressionLevel.SYNTACTIC)
        assert "pane" in it.compressed_text, f"Coordination wrongly elided: {it.compressed_text!r}"

        en = svc.apply_compression("I bought bread and ate cake.", "eng", CompressionLevel.SYNTACTIC)
        assert "bread" in en.compressed_text
        assert "cake" in en.compressed_text

    def test_elides_hedge_clause_keeps_main_verb_and_object(self, svc):
        r = svc.apply_compression(
            "Could you please kindly review the attached quarterly financial report before tomorrow.",
            "eng", CompressionLevel.SYNTACTIC,
        )
        assert "review" in r.compressed_text
        assert "report" in r.compressed_text.lower()

    def test_never_misfires_on_preposition_verb_homograph(self, svc):
        # "entro" is the Italian preposition "by/within" far more often than the
        # 1st-person verb "I enter" — a naive verb-form lookup used to misdetect it as
        # a second verb and elide the whole preceding clause (losing the subject).
        r = svc.apply_compression("Il cliente aspetta una risposta entro venerdì.", "ita", CompressionLevel.SYNTACTIC)
        text = r.compressed_text.lower()
        assert "cliente" in text, f"Subject clause wrongly elided as hedge clause: {r.compressed_text!r}"
        assert "venerdì" in text

    def test_no_pos_data_language_does_not_crash(self, svc):
        svc.apply_compression("ಇದು ಒಂದು ಪರೀಕ್ಷೆ.", "kan", CompressionLevel.SYNTACTIC)  # no raise


class TestAllLevelsNeverExpandTokenCount:
    @pytest.mark.parametrize("text,iso3", [
        ("Vorrei sapere se è possibile ricevere informazioni sui voli per Roma domani mattina.", "ita"),
        ("Could you please kindly review the attached quarterly financial report before tomorrow.", "eng"),
    ])
    def test_never_expands(self, svc, text, iso3):
        for level in ALL_LEVELS:
            r = svc.apply_compression(text, iso3, level)
            assert r.compressed_tokens <= r.original_tokens, (
                f"{level} expanded {text!r} from {r.original_tokens} to {r.compressed_tokens} tokens"
            )

class TestNumericFactsSurvive:
    @pytest.mark.parametrize("level", ALL_LEVELS)
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Il server risponde sulla porta 8080.", "8080"),
            ("La fattura ammonta a 5000 euro.", "5000"),
            ("Lo sconto applicato è del 25%.", "25"),
            ("La versione installata è la 3.11.4.", "3"),
            ("La scadenza è fissata al 12/08/2026.", "12"),
            ("La temperatura rilevata è -12,5 gradi.", "12"),
        ],
    )
    def test_numeric_value_survives(self, svc, level, text, expected):
        result = svc.apply_compression(text, "ita", level)

        assert expected in result.compressed_text, (
            f"{level} removed numeric fact {expected!r}: "
            f"{result.compressed_text!r}"
        )