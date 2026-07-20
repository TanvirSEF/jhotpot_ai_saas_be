"""
AI Resume ATS Optimizer Service — Phase B2

Responsibilities:
  1. Parse raw candidate resume content and target job description (JD).
  2. Perform keyword matching (matched keywords vs missing skills).
  3. Calculate an overall ATS match score (0-100).
  4. Enhance work experience bullet points using action verbs and metrics.
  5. Return structured JSON with ats_score, keyword_analysis, and optimized_resume_content.
"""

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an expert Executive Resume Writer and Applicant Tracking System (ATS) Optimization Specialist.

Your task is to analyze a candidate's raw resume JSON against a target Job Description (JD) and produce an AI-optimized resume payload with ATS match scoring.

STRICT INSTRUCTIONS:
1. Extract top required technical skills, hard skills, soft skills, and industry keywords from the target JD.
2. Evaluate the raw candidate profile against the JD requirements and compute an integer ATS match score from 0 to 100.
3. Identify matched keywords (present in both) and missing critical keywords (required by JD but missing in resume).
4. Provide a brief 2-3 sentence optimization_summary explaining key improvements.
5. Enhance work experience achievement bullet points using strong action verbs (e.g., "Spearheaded", "Engineered", "Optimized", "Architected") and quantifiable impact metrics where applicable. Do NOT invent fake employment history or false degrees.
6. Return your entire response as a valid JSON object conforming EXACTLY to the following JSON schema:

{
  "ats_score": <int between 0 and 100>,
  "keyword_analysis": {
    "matched_keywords": [<string array>],
    "missing_keywords": [<string array>],
    "optimization_summary": "<string>"
  },
  "optimized_resume_content": {
    "personal_info": { ... },
    "work_experiences": [
      {
        "company": "<string>",
        "role": "<string>",
        "location": "<string or null>",
        "start_date": "<string>",
        "end_date": "<string or null>",
        "is_current": <bool>,
        "achievements": [<enhanced achievement bullet strings>]
      }
    ],
    "education": [ ... ],
    "skill_categories": [
      {
        "category_name": "<string>",
        "skills": [<string array incorporating relevant keywords>]
      }
    ],
    "certifications": [ ... ],
    "projects": [ ... ]
  }
}
"""


async def optimize_resume_against_jd(
    raw_resume: dict[str, Any],
    target_jd: str,
) -> dict[str, Any]:
    """
    Call OpenAI Chat Completion in JSON mode to optimize *raw_resume*
    against *target_jd*.

    Args:
        raw_resume: Candidate raw resume content dict.
        target_jd:  Raw text of target Job Description.

    Returns:
        Dict containing ats_score, keyword_analysis, and optimized_resume_content.
    """
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    user_prompt = f"""TARGET JOB DESCRIPTION:
{target_jd.strip()}

CANDIDATE RAW RESUME DATA:
{json.dumps(raw_resume, indent=2)}
"""

    logger.info("Calling LLM ATS Optimizer (model=%s)", settings.OPENAI_CHAT_MODEL)

    try:
        response = await client.chat.completions.create(
            model=settings.OPENAI_CHAT_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=2500,
        )

        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)

        # Ensure fallback fields if keys are missing
        ats_score = int(parsed.get("ats_score", 70))
        keyword_analysis = parsed.get(
            "keyword_analysis",
            {
                "matched_keywords": [],
                "missing_keywords": [],
                "optimization_summary": "Resume optimized successfully.",
            },
        )
        optimized_content = parsed.get("optimized_resume_content", raw_resume)

        return {
            "ats_score": ats_score,
            "keyword_analysis": keyword_analysis,
            "optimized_resume_content": optimized_content,
        }

    except Exception as exc:
        logger.error("Resume optimization failed: %s", exc, exc_info=True)
        # Fallback response on error
        return {
            "ats_score": 50,
            "keyword_analysis": {
                "matched_keywords": [],
                "missing_keywords": [],
                "optimization_summary": f"Optimization encountered an issue: {exc}",
            },
            "optimized_resume_content": raw_resume,
        }
