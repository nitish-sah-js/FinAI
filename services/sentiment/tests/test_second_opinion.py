"""Tests for sentiment.second_opinion — merge logic."""

import pytest
from sentiment.second_opinion import merge


# ── merge() ───────────────────────────────────────────────────────────────────

class TestMerge:
    """Tests for the FinBERT + LLM merge logic."""

    @staticmethod
    def _fb(label="positive", score=0.7, confidence=0.85):
        return {"label": label, "score": score, "confidence": confidence}

    @staticmethod
    def _llm(sentiment="positive", confidence=0.9):
        return {
            "sentiment":        sentiment,
            "confidence":       confidence,
            "affected_tickers": ["RELIANCE.NS"],
            "materiality":      "medium",
            "horizon":          "days",
            "model":            "gemma3:4b",
        }

    # ── None LLM (no second opinion) ─────────────────────────────

    def test_none_llm_returns_finbert_unchanged(self):
        fb = self._fb()
        result = merge(fb, None)
        assert result is fb

    # ── Agreement path ────────────────────────────────────────────

    def test_agreement_boosts_confidence(self):
        fb  = self._fb(label="positive", confidence=0.7)
        llm = self._llm(sentiment="positive", confidence=0.95)
        result = merge(fb, llm)
        assert result["label"] == "positive"
        assert result["confidence"] == 0.95  # max(0.7, 0.95)
        assert result["second_opinion"] == llm

    def test_agreement_keeps_higher_fb_confidence(self):
        fb  = self._fb(label="negative", confidence=0.9)
        llm = self._llm(sentiment="negative", confidence=0.6)
        result = merge(fb, llm)
        assert result["confidence"] == 0.9

    # ── Disagreement, high LLM confidence → LLM wins ─────────────

    def test_disagreement_llm_high_confidence_overrides(self):
        fb  = self._fb(label="positive", confidence=0.6)
        llm = self._llm(sentiment="negative", confidence=0.8)
        result = merge(fb, llm)
        assert result["label"] == "negative"
        assert result["confidence"] == 0.8
        assert result["second_opinion"] == llm

    def test_disagreement_llm_exactly_0_7_overrides(self):
        fb  = self._fb(label="neutral", confidence=0.5)
        llm = self._llm(sentiment="positive", confidence=0.7)
        result = merge(fb, llm)
        assert result["label"] == "positive"

    # ── Disagreement, low LLM confidence → neutral fallback ──────

    def test_disagreement_llm_low_confidence_falls_to_neutral(self):
        fb  = self._fb(label="positive", confidence=0.8)
        llm = self._llm(sentiment="negative", confidence=0.5)
        result = merge(fb, llm)
        assert result["label"] == "neutral"
        assert result["confidence"] == 0.4
        assert "_warning" in result

    def test_disagreement_llm_zero_confidence(self):
        fb  = self._fb(label="negative", confidence=0.9)
        llm = self._llm(sentiment="positive", confidence=0.0)
        result = merge(fb, llm)
        assert result["label"] == "neutral"
        assert result["confidence"] == 0.4

    # ── Edge: LLM missing confidence key ──────────────────────────

    def test_missing_confidence_key_defaults_to_zero(self):
        fb = self._fb(label="positive", confidence=0.8)
        llm = {"sentiment": "negative", "affected_tickers": [],
               "materiality": "low", "horizon": "intraday", "model": "gemma3:4b"}
        # confidence defaults to 0 → falls to neutral
        result = merge(fb, llm)
        assert result["label"] == "neutral"
        assert result["confidence"] == 0.4


class TestScoreFollowsLabel:
    """An LLM override must move the score too (portfolio_sentiment aggregates score, not label)."""

    def test_override_sets_signed_score(self):
        from sentiment.second_opinion import merge
        out = merge({"label": "neutral", "score": 0.08, "confidence": 0.5},
                    {"sentiment": "negative", "confidence": 0.9})
        assert out["label"] == "negative" and out["score"] == -0.9

    def test_low_confidence_disagreement_zeroes_score(self):
        from sentiment.second_opinion import merge
        out = merge({"label": "positive", "score": 0.6, "confidence": 0.55},
                    {"sentiment": "negative", "confidence": 0.5})
        assert out["label"] == "neutral" and out["score"] == 0.0

    def test_agreement_keeps_finbert_score(self):
        from sentiment.second_opinion import merge
        out = merge({"label": "negative", "score": -0.7, "confidence": 0.55},
                    {"sentiment": "negative", "confidence": 0.8})
        assert out["score"] == -0.7
