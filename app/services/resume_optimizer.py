"""Schema-validated ATS resume optimization."""

import json
import logging
from typing import Any

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.core.config import settings
from app.core.observability import observe_operation
from app.schemas.resume import ResumeContent, ResumeOptimizationResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an expert resume writer and ATS optimization specialist.

Analyze the candidate resume only against the supplied target job description.
Return the requested structured result and follow these rules:
- Compute an integer ATS match score from 0 to 100.
- List genuinely matched keywords and important missing keywords.
- Explain the most important improvements in two or three concise sentences.
- Improve wording with clear action verbs and quantified impact only when the
  source resume already provides enough facts to support the claim.
- Never invent employers, roles, dates, degrees, certifications, projects,
  skills, responsibilities, achievements, or metrics.
- Preserve every resume section and all required fields in the schema.
"""


class ResumeOptimizationError(RuntimeError):
    """Safe base error for an optimization that must not be persisted."""


class ResumeOptimizationProviderError(ResumeOptimizationError):
    """The model provider failed or refused to return an answer."""


class ResumeOptimizationOutputError(ResumeOptimizationError):
    """The provider response did not satisfy the complete resume contract."""


async def optimize_resume_against_jd(
    raw_resume: dict[str, Any],
    target_jd: str,
) -> ResumeOptimizationResult:
    """Return a fully validated optimization or raise without a fallback result."""
    try:
        canonical_resume = ResumeContent.model_validate(raw_resume)
    except ValidationError as exc:
        raise ResumeOptimizationOutputError(
            "Stored resume data does not match the canonical schema."
        ) from exc

    try:
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        with observe_operation("openai", "resume_optimization"):
            response = await client.beta.chat.completions.parse(
                model=settings.OPENAI_CHAT_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "TARGET JOB DESCRIPTION:\n"
                            f"{target_jd.strip()}\n\n"
                            "CANDIDATE RAW RESUME DATA:\n"
                            f"{json.dumps(canonical_resume.model_dump(mode='json'), indent=2)}"
                        ),
                    },
                ],
                response_format=ResumeOptimizationResult,
                temperature=0.2,
                max_tokens=3000,
            )
    except Exception as exc:
        logger.warning(
            "Resume optimization provider call failed error_type=%s",
            type(exc).__name__,
        )
        raise ResumeOptimizationProviderError(
            "Resume optimization provider is temporarily unavailable."
        ) from exc

    message = response.choices[0].message
    if getattr(message, "refusal", None):
        logger.info("Resume optimization was refused by the model provider.")
        raise ResumeOptimizationProviderError(
            "Resume optimization could not be completed for this request."
        )

    parsed = getattr(message, "parsed", None)
    if parsed is None:
        logger.warning("Resume optimization returned no parsed structured output.")
        raise ResumeOptimizationOutputError(
            "Resume optimization returned an invalid structured result."
        )

    try:
        return ResumeOptimizationResult.model_validate(parsed)
    except ValidationError as exc:
        logger.warning("Resume optimization output failed schema validation.")
        raise ResumeOptimizationOutputError(
            "Resume optimization returned an invalid structured result."
        ) from exc
