"""
vectordb/schema.py
Creates the Weaviate collections: HistoricalEvent and NewsItem.
Handles both the modern Configure.Vectors.self_provided() API and the older
Configure.Vectorizer.none() fallback for different weaviate-client 4.x versions.
"""
from __future__ import annotations
import logging
from typing import Any

try:
    import weaviate
    from weaviate.classes.config import (
        Configure,
        Property,
        DataType,
        VectorDistances,
    )
except ImportError:
    weaviate = None
    Configure = None
    Property = None
    DataType = None
    VectorDistances = None

logger = logging.getLogger(__name__)

HISTORICAL_EVENT_COLLECTION = "HistoricalEvent"
NEWS_ITEM_COLLECTION = "NewsItem"


def _vector_config():
    """
    Try the modern self_provided API first; fall back to the older none() vectorizer
    if the installed client version doesn't support it.
    """
    if Configure is None:
        raise RuntimeError("weaviate-client is not installed")
    try:
        return {
            "vector_config": Configure.Vectors.self_provided(
                vector_index_config=Configure.VectorIndex.hnsw(
                    distance_metric=VectorDistances.COSINE
                )
            )
        }
    except AttributeError:
        pass
    # Older 4.x API
    return {
        "vectorizer_config": Configure.Vectorizer.none(),
        "vector_index_config": Configure.VectorIndex.hnsw(
            distance_metric=VectorDistances.COSINE
        ),
    }


def _text(name: str, skip_vectorize: bool = False):
    return Property(
        name=name,
        data_type=DataType.TEXT,
        skip_vectorization=skip_vectorize,
        vectorize_property_name=False,
    )


def _text_array(name: str):
    return Property(
        name=name,
        data_type=DataType.TEXT_ARRAY,
        skip_vectorization=True,
        vectorize_property_name=False,
    )


def _date(name: str):
    return Property(
        name=name,
        data_type=DataType.DATE,
        skip_vectorization=True,
        vectorize_property_name=False,
    )


def _number(name: str):
    return Property(
        name=name,
        data_type=DataType.NUMBER,
        skip_vectorization=True,
        vectorize_property_name=False,
    )


def _bool(name: str):
    return Property(
        name=name,
        data_type=DataType.BOOL,
        skip_vectorization=True,
        vectorize_property_name=False,
    )


def create_collections(client: Any, reset: bool = False) -> None:
    """
    Create HistoricalEvent and NewsItem collections.
    If reset=True, delete existing collections first (useful for re-seeding).
    """
    if weaviate is None:
        raise RuntimeError("weaviate-client is not installed")

    vec_kwargs = _vector_config()

    # ---- HistoricalEvent ----
    if client.collections.exists(HISTORICAL_EVENT_COLLECTION):
        if reset:
            logger.info("Deleting existing collection %s", HISTORICAL_EVENT_COLLECTION)
            client.collections.delete(HISTORICAL_EVENT_COLLECTION)
        else:
            logger.info("Collection %s already exists, skipping", HISTORICAL_EVENT_COLLECTION)
            _ensure_news_item(client, vec_kwargs, reset)
            return

    logger.info("Creating collection %s", HISTORICAL_EVENT_COLLECTION)
    client.collections.create(
        name=HISTORICAL_EVENT_COLLECTION,
        **vec_kwargs,
        properties=[
            _text("event_id", skip_vectorize=True),
            _text("title"),
            _text("event_type", skip_vectorize=True),
            _text("region"),
            _text("country", skip_vectorize=True),
            _date("start_date"),
            _date("end_date"),
            _date("event_date"),
            _number("severity_value"),
            _text("severity_unit", skip_vectorize=True),
            _number("severity_norm"),
            _text("description"),
            _text("mechanism"),
            _text_array("affected_assets"),
            _text_array("tickers"),
            _text("outcomes_json", skip_vectorize=True),
            _text("split", skip_vectorize=True),
            _text("source", skip_vectorize=True),
            _text("source_url", skip_vectorize=True),
            _text("embedding_text", skip_vectorize=True),
        ],
    )

    _ensure_news_item(client, vec_kwargs, reset)


def _ensure_news_item(
    client: Any, vec_kwargs: dict, reset: bool
) -> None:
    if client.collections.exists(NEWS_ITEM_COLLECTION):
        if reset:
            logger.info("Deleting existing collection %s", NEWS_ITEM_COLLECTION)
            client.collections.delete(NEWS_ITEM_COLLECTION)
        else:
            logger.info("Collection %s already exists, skipping", NEWS_ITEM_COLLECTION)
            return

    logger.info("Creating collection %s", NEWS_ITEM_COLLECTION)
    client.collections.create(
        name=NEWS_ITEM_COLLECTION,
        **vec_kwargs,
        properties=[
            _text("news_id", skip_vectorize=True),
            _text("title"),
            _text("summary"),
            _text("source", skip_vectorize=True),
            _text("url", skip_vectorize=True),
            _date("published_at"),
            _text_array("tickers"),
            _text("sentiment_label", skip_vectorize=True),
            _number("sentiment_score"),
            _date("ingested_at"),
            _date("indexed_at"),
        ],
    )
