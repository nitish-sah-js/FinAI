"""Weaviate collections HistoricalEvent and NewsItem (08 §3), self-provided 384-d vectors, HNSW cosine.

Identifier-like properties use Tokenization.FIELD so equality filters match the whole value: with the default
"word" tokenization `event_type == "oil_shock"` / `split == "train"` / `tickers contains "RELIANCE.NS"` would be
matched token by token ("oil", "shock", "reliance", "ns"), which silently breaks the holdout filter.

UNTESTED against a live server (Docker was down during integration).
"""
from __future__ import annotations

import logging
from typing import Any

try:
    from weaviate.classes.config import Configure, DataType, Property, Tokenization, VectorDistances
except ImportError:  # pragma: no cover
    Configure = DataType = Property = Tokenization = VectorDistances = None

logger = logging.getLogger(__name__)

HISTORICAL_EVENT_COLLECTION = "HistoricalEvent"
NEWS_ITEM_COLLECTION = "NewsItem"


def _vector_kwargs() -> dict:
    hnsw = Configure.VectorIndex.hnsw(distance_metric=VectorDistances.COSINE)
    if hasattr(Configure, "Vectors") and hasattr(Configure.Vectors, "self_provided"):
        return {"vector_config": Configure.Vectors.self_provided(vector_index_config=hnsw)}
    return {"vectorizer_config": Configure.Vectorizer.none(), "vector_index_config": hnsw}   # older 4.x


def _text(name: str, field: bool = False) -> Any:
    return Property(name=name, data_type=DataType.TEXT, skip_vectorization=True, vectorize_property_name=False,
                    tokenization=Tokenization.FIELD if field else None)


def _text_array(name: str, field: bool = True) -> Any:
    return Property(name=name, data_type=DataType.TEXT_ARRAY, skip_vectorization=True, vectorize_property_name=False,
                    tokenization=Tokenization.FIELD if field else None)


def _typed(name: str, dt: Any) -> Any:
    return Property(name=name, data_type=dt, skip_vectorization=True, vectorize_property_name=False)


def historical_event_properties() -> list:
    return [
        _text("event_id", field=True), _text("title"), _text("event_type", field=True), _text("region"),
        _text("country", field=True), _typed("start_date", DataType.DATE), _typed("end_date", DataType.DATE),
        _typed("event_date", DataType.DATE), _typed("severity_value", DataType.NUMBER),
        _text("severity_unit", field=True), _typed("severity_norm", DataType.NUMBER), _text("description"),
        _text("mechanism"), _text_array("affected_assets"), _text_array("tickers"), _text("outcomes_json", field=True),
        _text("split", field=True), _text("source"), _text("source_url", field=True), _text("embedding_text"),
    ]


def news_item_properties() -> list:
    return [
        _text("news_id", field=True), _text("title"), _text("summary"), _text("source", field=True),
        _text("url", field=True), _typed("published_at", DataType.DATE), _text_array("tickers"),
        _text("sentiment_label", field=True), _typed("sentiment_score", DataType.NUMBER),
        _typed("ingested_at", DataType.DATE), _typed("indexed_at", DataType.DATE),
    ]


def _ensure(client: Any, name: str, props: list, reset: bool) -> None:
    if client.collections.exists(name):
        if not reset:
            logger.info("collection %s exists", name)
            return
        logger.info("deleting collection %s", name)
        client.collections.delete(name)
    logger.info("creating collection %s", name)
    client.collections.create(name=name, properties=props, **_vector_kwargs())


def create_collections(client: Any, reset: bool = False, news: bool = True) -> None:
    if Configure is None:
        raise RuntimeError("weaviate-client is not installed")
    _ensure(client, HISTORICAL_EVENT_COLLECTION, historical_event_properties(), reset)
    if news:
        _ensure(client, NEWS_ITEM_COLLECTION, news_item_properties(), reset=False)
