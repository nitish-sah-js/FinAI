"""Tests for sentiment.model — FinBERT scorer."""

import pytest
from unittest.mock import patch, MagicMock


class TestFinbertScorer:
    """Tests for the FinbertScorer class using mocked transformers."""

    @staticmethod
    def _make_mock_scorer():
        """Create a FinbertScorer with mocked model and tokenizer."""
        import torch
        import torch.nn.functional as F

        with patch("sentiment.model.AutoTokenizer") as mock_tok, \
             patch("sentiment.model.AutoModelForSequenceClassification") as mock_model_cls:

            # Mock model config
            mock_model = MagicMock()
            mock_model.config.id2label = {0: "Positive", 1: "Negative", 2: "Neutral"}
            mock_model.to.return_value = mock_model
            mock_model.half.return_value = mock_model
            mock_model.eval.return_value = None
            mock_model_cls.from_pretrained.return_value = mock_model

            # Mock tokenizer
            mock_tokenizer = MagicMock()
            mock_tok.from_pretrained.return_value = mock_tokenizer

            from sentiment.model import FinbertScorer
            with patch("torch.cuda.is_available", return_value=False):
                # a name WITHOUT a known label override, so the label map really comes from config.id2label
                scorer = FinbertScorer("test/model-with-correct-config")

        return scorer, mock_model, mock_tokenizer

    def test_label_map_normalized_to_lowercase(self):
        scorer, _, _ = self._make_mock_scorer()
        assert scorer.id2label == {0: "positive", 1: "negative", 2: "neutral"}

    def test_pos_neg_index_detected(self):
        scorer, _, _ = self._make_mock_scorer()
        assert scorer._pos_idx == 0
        assert scorer._neg_idx == 1

    def test_score_returns_correct_structure(self):
        import torch
        scorer, mock_model, mock_tokenizer = self._make_mock_scorer()

        # Simulate model output: batch of 2
        # Item 1: strong positive [0.9, 0.05, 0.05]
        # Item 2: strong negative [0.05, 0.9, 0.05]
        logits = torch.tensor([[3.0, -1.0, -1.0], [-1.0, 3.0, -1.0]])
        mock_output = MagicMock()
        mock_output.logits = logits

        mock_model.__call__ = MagicMock(return_value=mock_output)
        mock_model.return_value = mock_output

        # Mock tokenizer to return something .to()-able
        enc = MagicMock()
        enc.to.return_value = enc
        mock_tokenizer.__call__ = MagicMock(return_value=enc)
        mock_tokenizer.return_value = enc

        results = scorer.score(["good news", "bad news"])
        assert len(results) == 2

        # First item should be positive
        assert results[0]["label"] == "positive"
        assert results[0]["score"] > 0  # pos_prob - neg_prob > 0
        assert 0 < results[0]["confidence"] <= 1.0
        assert "probs" in results[0]
        assert set(results[0]["probs"].keys()) == {"positive", "negative", "neutral"}

        # Second item should be negative
        assert results[1]["label"] == "negative"
        assert results[1]["score"] < 0

    def test_score_empty_list(self):
        scorer, _, _ = self._make_mock_scorer()
        results = scorer.score([])
        assert results == []


class TestGetScorer:
    """Tests for the get_scorer singleton."""

    def test_returns_same_instance(self):
        import sentiment.model as mod
        sentinel = MagicMock()
        mod._scorer = sentinel
        try:
            assert mod.get_scorer() is sentinel
        finally:
            mod._scorer = None
