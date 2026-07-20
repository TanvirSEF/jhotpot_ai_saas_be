import ast
import unittest
from pathlib import Path

from app.main import app


ROOT = Path(__file__).resolve().parents[1]


class BackendContractTests(unittest.TestCase):
    def test_openapi_contains_current_canonical_workflows(self):
        paths = app.openapi()["paths"]

        expected_paths = {
            "/live",
            "/ready",
            "/health",
            "/api/v1/auth/register",
            "/api/v1/auth/login",
            "/api/v1/org",
            "/api/v1/knowledge/{org_id}/products",
            "/api/v1/knowledge/{org_id}/faqs",
            "/api/v1/knowledge/{org_id}/search",
            "/api/v1/knowledge/{org_id}/embedding-status",
            "/api/v1/knowledge/{org_id}/embeddings/rebuild",
            "/api/v1/knowledge/{org_id}/embeddings/{entity_type}/{entity_id}/retry",
            "/api/v1/fb/connect",
            "/api/v1/fb/pages/{page_record_id}/health",
            "/api/v1/fb/pages/{page_record_id}/reconnect",
            "/api/v1/fb/pages/{page_record_id}/subscribe",
            "/api/v1/fb/pages/{page_record_id}/transfer",
            "/api/v1/fb/webhook",
            "/api/v1/resume",
            "/api/v1/resume/{resume_id}/optimize",
            "/api/v1/resume/{resume_id}/download",
            "/api/v1/resume/{resume_id}/exports",
            "/api/v1/resume/{resume_id}/exports/{export_id}",
            "/api/v1/resume/{resume_id}/exports/{export_id}/download",
        }

        self.assertTrue(expected_paths.issubset(paths.keys()))

    def test_vector_extension_is_created_before_vector_table(self):
        migration = (
            ROOT
            / "migrations"
            / "versions"
            / "35bb75b87e31_step1_module_a_schema.py"
        ).read_text(encoding="utf-8")

        extension_position = migration.index("CREATE EXTENSION IF NOT EXISTS vector")
        vector_table_position = migration.index("'knowledge_embeddings'")
        self.assertLess(extension_position, vector_table_position)
        self.assertNotIn("op.drop_table('users')", migration)

    def test_alembic_metadata_imports_every_model(self):
        source = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_models: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.models":
                imported_models.update(alias.name for alias in node.names)

        self.assertEqual(
            imported_models,
            {
                "Faq",
                "FbPage",
                "EmbeddingStatusRecord",
                "KnowledgeEmbedding",
                "Organization",
                "Product",
                "Resume",
                "TaskFailure",
                "User",
                "WebhookEvent",
                "RagRun",
                "ResumeExport",
            },
        )


if __name__ == "__main__":
    unittest.main()
