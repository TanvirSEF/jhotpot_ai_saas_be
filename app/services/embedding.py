


import json
import logging
from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.observability import observe_operation

logger = logging.getLogger(__name__)

_MODEL = "text-embedding-3-small"
_DIMENSIONS = 1536


@lru_cache(maxsize=1)
def _get_client() -> AsyncOpenAI:


    return AsyncOpenAI(api_key=settings.OPENAI_API_KEY, max_retries=0, timeout=30.0)


async def get_embedding(text: str) -> list[float]:


    text = text.strip()
    if not text:
        raise ValueError("Cannot embed an empty string.")

    client = _get_client()
    logger.debug("Generating embedding for text (len=%d)", len(text))

    with observe_operation("openai", "embedding"):
        response = await client.embeddings.create(
            model=_MODEL,
            input=text,
            dimensions=_DIMENSIONS,
        )
    return response.data[0].embedding


def build_product_text(product_data: dict) -> str:


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

    return f"Q: {question.strip()}\nA: {answer.strip()}"


def build_guideline_text(guidelines: str) -> str:

    return f"Business Guidelines:\n{guidelines.strip()}"
