# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Regression tests for bugs found via real-text quality review:

- Negation particles ("non"/"not"/"ne...pas"/"no"/"nicht"/"não"/"不") were being
  stripped as ordinary function words at every compression level, silently
  inverting sentence meaning.
- Italian "subito" (adverb, "immediately") was mis-lemmatised to "subire"
  (verb, "to undergo") via a UD homograph-contaminated lemma entry.
- Chinese had no word segmentation at all (a whole sentence matched the
  tokenizer's Han-character run as one "token"), so compression and language
  detection both silently no-op'd for Chinese beyond punctuation stripping.
- Language-detection data contamination: "john" in the Italian index and
  "ha"/"e" in the French index (proper noun / cross-language leakage), and
  "ate" doubling as a Portuguese exclusive marker collided with English's
  "ate" and defeated exclusive-marker disambiguation.
"""
from __future__ import annotations

from synthelion.core import CompressionService
from synthelion.models import CompressionLevel
from synthelion.detector import LanguageDetector


class TestNegationSurvives:
    """Negation particles must survive every compression level, in every
    language that has one, at least in AGGRESSIVE's safety-floor sense."""

    svc = CompressionService()

    def _compressed(self, text: str, iso3: str, level: CompressionLevel) -> str:
        return self.svc.compress(text, level, iso3=iso3).compressed_text

    def test_italian_non_survives_all_levels(self):
        text = "quando nelle istituzioni non c'era alcuna sensibilità"
        for level in [CompressionLevel.LIGHT, CompressionLevel.SEMANTIC, CompressionLevel.AGGRESSIVE]:
            assert "non" in self._compressed(text, "ita", level).lower(), level

    def test_english_not_survives_all_levels(self):
        text = "I do not think this is correct and you should not do that"
        for level in [CompressionLevel.LIGHT, CompressionLevel.SEMANTIC, CompressionLevel.AGGRESSIVE]:
            assert "not" in self._compressed(text, "eng", level).lower(), level

    def test_french_negation_survives(self):
        text = "Je ne sais pas si cela est vrai"
        out = self._compressed(text, "fra", CompressionLevel.SEMANTIC).lower()
        assert "ne" in out and "pas" in out

    def test_spanish_no_survives(self):
        text = "No creo que esto sea correcto"
        out = self._compressed(text, "spa", CompressionLevel.AGGRESSIVE).lower()
        assert "no" in out

    def test_german_nicht_survives(self):
        text = "Ich weiss nicht ob das richtig ist"
        out = self._compressed(text, "deu", CompressionLevel.AGGRESSIVE).lower()
        assert "nicht" in out

    def test_portuguese_negation_survives(self):
        text = "Não acho que isso seja correto"
        out = self._compressed(text, "por", CompressionLevel.SEMANTIC).lower()
        assert "não" in out or "nao" in out

    def test_chinese_bu_survives(self):
        text = "我不喜欢这个方法，因为它没有考虑到否定词。"
        for level in [CompressionLevel.LIGHT, CompressionLevel.SEMANTIC, CompressionLevel.AGGRESSIVE]:
            assert "不" in self._compressed(text, "zho", level), level

    def test_chinese_negation_compound_survives_aggressive(self):
        # "不是" ("is not") is itself a dictionary word the segmenter merges into
        # one token -- must still be recognised as negation-protected, not just
        # bare "不".
        text = "别管他，这不是我们的问题。"
        out = self._compressed(text, "zho", CompressionLevel.AGGRESSIVE)
        assert "不是" in out


class TestSubitoLemmaFix:
    svc = CompressionService()

    def test_subito_not_lemmatised_to_subire(self):
        text = "In inglese invece il nome fece riferimento fin da subito agli human rights"
        r = self.svc.compress(text, CompressionLevel.SEMANTIC, iso3="ita")
        assert "subito" in r.compressed_text.lower()
        assert "subire" not in r.compressed_text.lower()

    def test_subito_stable_across_levels(self):
        text = "Fin da subito abbiamo capito il problema."
        for level in [CompressionLevel.LIGHT, CompressionLevel.SEMANTIC, CompressionLevel.AGGRESSIVE]:
            out = self.svc.compress(text, level, iso3="ita").compressed_text.lower()
            assert "subire" not in out, level


class TestChineseSegmentationAndDetection:
    det = LanguageDetector()
    svc = CompressionService()

    def test_detects_chinese_not_english(self):
        text = "这个系统不支持中文的否定词处理，所以我们需要检查一下。"
        assert self.det.detect(text) == "zho"

    def test_chinese_compression_actually_segments_words(self):
        text = "我们不能接受这个方案，因为它不安全。"
        r = self.svc.compress(text, CompressionLevel.SEMANTIC, iso3="zho")
        # A dropped token count proves segmentation happened (the pre-fix
        # behaviour collapsed the whole sentence into one blob and compressed
        # nothing). Counting whitespace in the output would not work: Chinese is
        # written without spaces, so the renderer no longer inserts any between
        # adjacent Han tokens — the segmentation is real, it is just not visible
        # in the rendered string.
        assert r.compressed_tokens < r.original_tokens
        assert "我们" not in r.compressed_text      # function word removed
        assert "不" in r.compressed_text            # negation preserved


class TestDetectorDataQualityFixes:
    det = LanguageDetector()

    def test_marco_sentence_detects_italian_not_french(self):
        text = "Marco ha comprato il pane e ha mangiato la torta."
        assert self.det.detect(text) == "ita"

    def test_john_sentence_detects_english_not_italian(self):
        text = "John bought bread and ate cake yesterday."
        assert self.det.detect(text) == "eng"

    def test_john_not_a_function_word_anywhere(self):
        from synthelion.word_provider import FunctionWordProvider
        idx = FunctionWordProvider()._load_index()
        for iso3, (_, _, fw) in idx.items():
            assert "john" not in fw, iso3
