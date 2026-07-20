"""
Embedding service — wraps OpenAI text-embedding-3-small.

Design decisions:
  - Async-first: uses httpx via the official openai async client.
  - Single public function `get_embedding()` keeps callers simple.
  - 1 536-dimension output matches PRD §4 and the pgvector column definition.
  - The client is lazily instantiated and reused across requests (connection
    pooling handled by the underlying httpx transport).
"""

import logging
import json
from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_MODEL = "text-embedding-3-small"
_DIMENSIONS = 1536


@lru_cache(maxsize=1)
def _get_client() -> AsyncOpenAI:
    """Return a lazily constructed, module-level async OpenAI client."""
    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def get_embedding(text: str) -> list[float]:
    """
    Generate a 1 536-dim embedding vector for *text*.

    Raises:
        openai.OpenAIError: propagated to the caller for retry handling.
        ValueError: if *text* is empty after stripping whitespace.
    """
    text = text.strip()
    if not text:
        raise ValueError("Cannot embed an empty string.")

    client = _get_client()
    logger.debug("Generating embedding for text (len=%d)", len(text))

    response = await client.embeddings.create(
        model=_MODEL,
        input=text,
        dimensions=_DIMENSIONS,
    )
    return response.data[0].embedding


def build_product_text(product_data: dict) -> str:
    """
    Serialize a product dict to a single plain-text blob for embedding.

    Keeping a deterministic serialization format ensures consistent vector
    similarity when the same field is updated.
    """
    parts = [
        f"Product: {product_data.get('name', '')}",
        f"SKU: {product_data.get('sku', 'N/A')}",
        f"Category: {product_data.get('category', '')}",
        "Attributes: " + json.dumps(
            product_data.get("attributes") or {},
            ensure_ascii=False,
            sort_keys=True,
        ),
        f"Price: BDT {product_data.get('price', 0):.2f}",
        f"Stock: {product_data.get('stock_status', 'Unknown')}",
        f"Description: {product_data.get('description', '')}",
    ]
    return "\n".join(p for p in parts if p.split(": ", 1)[1])


def build_faq_text(question: str, answer: str) -> str:
    """Serialize an FAQ pair to a plain-text blob for embedding."""
    return f"Q: {question.strip()}\nA: {answer.strip()}"


def build_guideline_text(guidelines: str) -> str:
    """Wrap business guidelines for embedding."""
    return f"Business Guidelines:\n{guidelines.strip()}"
