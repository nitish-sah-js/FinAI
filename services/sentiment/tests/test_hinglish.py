"""Tests for sentiment.hinglish — Hinglish/Hindi detection."""

import pytest
from sentiment.hinglish import is_hinglish, _hindi_words, CORE_HINDI


class TestIsHinglish:
    """Tests for is_hinglish() function."""

    @pytest.fixture(autouse=True)
    def seed_wordlist(self):
        """Ensure the word list is populated with at least CORE_HINDI."""
        _hindi_words.update(CORE_HINDI)

    # ── Devanagari detection ──────────────────────────────────────

    def test_devanagari_script_detected(self):
        assert is_hinglish("शेयर बाज़ार गिरा") is True

    def test_mixed_devanagari_latin(self):
        assert is_hinglish("Sensex में गिरावट") is True

    # ── Roman-Hindi token ratio ───────────────────────────────────

    def test_hinglish_sentence(self):
        # "bazaar mein tezi hai aur nivesh badhna chahiye"
        # 7 out of ~8 tokens are Hindi → well above 0.12
        assert is_hinglish("bazaar mein tezi hai aur nivesh badhna chahiye") is True

    def test_pure_english(self):
        assert is_hinglish("Reliance posts record quarterly profit on strong refining margins") is False

    def test_english_with_one_hindi_word(self):
        # NOTE: "the" is in CORE_HINDI (Hindi past-tense "they were"),
        # so avoid it in English test sentences to isolate single-word test.
        text = (
            "Stock prices recovered sharply after initial losses "
            "during early morning trading on positive global cues aur"
        )
        # "aur" is in CORE_HINDI; ratio = 1/14 ≈ 0.07 < 0.12
        assert is_hinglish(text) is False

    # ── Edge cases ────────────────────────────────────────────────

    def test_empty_string(self):
        assert is_hinglish("") is False

    def test_only_numbers_and_symbols(self):
        assert is_hinglish("12345 $$$ ###") is False

    def test_nifty_sensex_alone_not_hinglish(self):
        # Previously a documented quirk ("nifty" was in the Hindi list, so this was flagged). Fixed: market jargon and
        # English collisions ("the", "main", "share", "nifty", ...) no longer count, and ≥ 2 Hindi words are required.
        assert is_hinglish("Nifty at 20000") is False

    @pytest.mark.parametrize("text", [
        "Nifty ends flat as investors await RBI policy",
        "Shares of the main refiners fell as the market opened lower",
        "Reliance shares slip as crude rally squeezes refining margins",
    ])
    def test_real_english_headlines_are_not_hinglish(self, text):
        assert is_hinglish(text) is False

    @pytest.mark.parametrize("text", [
        "ITC ka FMCG business mazboot, lekin cigarette volume par dabav",
        "Sensex aaj 500 ank gir gaya, bazaar mein girawat",
        "Agar monsoon kamzor raha toh FMCG ka kya hoga next month?",
    ])
    def test_real_hinglish_headlines(self, text):
        assert is_hinglish(text) is True
