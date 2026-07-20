import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.api.v1.resume import optimize_resume
from app.models import ResumeExport
from app.schemas.resume import (
    OptimizeRequest,
    ResumeContent,
    ResumeOptimizationResult,
)
from app.services.export_storage import ExportStorageError, LocalExportStorage
from app.services.pdf_generator import generate_validated_resume_pdf
from app.services.resume_optimizer import ResumeOptimizationProviderError


def resume_payload(*, long: bool = False) -> dict:
    achievements = ["Built reliable APIs and reduced response time by 20%."]
    if long:
        achievements = [
            f"Delivered production initiative {index} with measurable quality improvements "
            "across a cross-functional engineering organization and customer workflow."
            for index in range(45)
        ]
    return {
        "personal_info": {
            "full_name": "Ada Lovelace",
            "email": "ada@example.com",
            "phone": "+1 555 0100",
            "location": "London",
            "linkedin_url": None,
            "github_url": None,
            "portfolio_url": None,
            "summary": "Software engineer focused on dependable systems.",
        },
        "work_experiences": [
            {
                "company": "Analytical Engines",
                "role": "Software Engineer",
                "location": "London",
                "start_date": "2020-01",
                "end_date": None,
                "is_current": True,
                "achievements": achievements,
            }
        ],
        "education": [],
        "skill_categories": [
            {"category_name": "Engineering", "skills": ["Python", "SQL"]}
        ],
        "certifications": [],
        "projects": [],
    }


class ResumeCorrectnessTests(unittest.IsolatedAsyncioTestCase):
    def test_complete_resume_schema_rejects_unknown_and_missing_fields(self):
        unknown = resume_payload()
        unknown["unexpected_section"] = []
        with self.assertRaises(ValidationError):
            ResumeContent.model_validate(unknown)

        missing = resume_payload()
        del missing["personal_info"]["email"]
        with self.assertRaises(ValidationError):
            ResumeContent.model_validate(missing)

        schema = ResumeContent.model_json_schema()
        self.assertFalse(schema["additionalProperties"])
        for definition in schema["$defs"].values():
            self.assertFalse(definition["additionalProperties"])

    async def test_provider_failure_does_not_commit_resume_changes(self):
        resume = SimpleNamespace(
            id=uuid.uuid4(),
            raw_json_data=resume_payload(),
            optimized_json_data=None,
            ats_score=0,
        )
        db = AsyncMock()
        failure = ResumeOptimizationProviderError("Provider unavailable.")

        with (
            patch("app.api.v1.resume._get_user_resume", AsyncMock(return_value=resume)),
            patch(
                "app.api.v1.resume.optimize_resume_against_jd",
                AsyncMock(side_effect=failure),
            ),
        ):
            with self.assertRaisesRegex(Exception, "502"):
                await optimize_resume(
                    resume.id,
                    OptimizeRequest(
                        target_job_description="Senior Python engineer for reliable APIs"
                    ),
                    SimpleNamespace(id=uuid.uuid4()),
                    db,
                )

        db.commit.assert_not_awaited()
        self.assertIsNone(resume.optimized_json_data)
        self.assertEqual(resume.ats_score, 0)

    def test_optimization_result_requires_complete_nested_resume(self):
        with self.assertRaises(ValidationError):
            ResumeOptimizationResult.model_validate(
                {
                    "ats_score": 88,
                    "keyword_analysis": {
                        "matched_keywords": ["Python"],
                        "missing_keywords": [],
                        "optimization_summary": "Aligned experience.",
                    },
                    "optimized_resume_content": {"personal_info": {}},
                }
            )

    def test_long_resume_pdf_is_multi_page_and_selectable(self):
        pdf_bytes, validation = generate_validated_resume_pdf(
            resume_payload(long=True)
        )
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreaterEqual(validation.page_count, 2)
        self.assertIn("Ada Lovelace", validation.extracted_text)
        self.assertIn("initiative 44", validation.extracted_text)

    def test_storage_is_atomic_and_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = LocalExportStorage(Path(directory))
            path = storage.write("user/resume/export.pdf", b"%PDF-test")
            self.assertEqual(path.read_bytes(), b"%PDF-test")
            self.assertEqual(storage.path("user/resume/export.pdf"), path)
            with self.assertRaises(ExportStorageError):
                storage.write("../escape.pdf", b"bad")

    def test_export_model_tracks_validation_and_integrity_metadata(self):
        columns = set(ResumeExport.__table__.columns.keys())
        self.assertTrue(
            {
                "source_json_data",
                "state",
                "storage_key",
                "sha256",
                "page_count",
                "selectable_text",
                "last_error_code",
            }.issubset(columns)
        )


if __name__ == "__main__":
    unittest.main()
