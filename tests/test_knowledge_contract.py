import unittest
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.v1.knowledge import ProductCreate, rebuild_embeddings
from app.models import EmbeddingJobState, EmbeddingStatusRecord, KnowledgeEmbedding
from app.services.embedding import build_guideline_text, build_product_text
from app.services.embedding_status import content_digest, set_embedding_status
from app.worker.dispatch import queue_embedding_tasks


class KnowledgeContractTests(unittest.TestCase):
    def test_product_schema_accepts_prd_catalog_fields(self):
        product = ProductCreate(
            name="Linen Shirt",
            sku="SHIRT-001",
            category="Clothing",
            attributes={
                "sizes": ["S", "M", "L"],
                "colors": ["Black", "Blue"],
            },
            price=Decimal("1490.00"),
            description="Breathable linen shirt.",
        )

        self.assertEqual(product.category, "Clothing")
        self.assertEqual(product.attributes["sizes"], ["S", "M", "L"])

    def test_product_embedding_contains_catalog_context(self):
        text = build_product_text(
            {
                "name": "Linen Shirt",
                "sku": "SHIRT-001",
                "category": "Clothing",
                "attributes": {
                    "sizes": ["S", "M"],
                    "colors": ["Black"],
                },
                "price": 1490,
                "stock_status": "In Stock",
                "description": "Breathable linen shirt.",
            }
        )

        self.assertIn("Category: Clothing", text)
        self.assertIn('"sizes": ["S", "M"]', text)
        self.assertIn("Price: BDT 1490.00", text)

    def test_guideline_embedding_text_is_canonical(self):
        self.assertEqual(
            build_guideline_text("  Delivery takes 24 hours.  "),
            "Business Guidelines:\nDelivery takes 24 hours.",
        )

    def test_embedding_table_has_one_vector_per_source_constraint(self):
        constraint_names = {
            constraint.name for constraint in KnowledgeEmbedding.__table__.constraints
        }
        self.assertIn("uq_knowledge_embeddings_entity", constraint_names)

    def test_embedding_model_tracks_the_hnsw_index(self):
        index_names = {index.name for index in KnowledgeEmbedding.__table__.indexes}
        self.assertIn("ix_knowledge_embeddings_embedding_hnsw", index_names)

    def test_embedding_status_has_one_record_per_source(self):
        constraint_names = {
            constraint.name for constraint in EmbeddingStatusRecord.__table__.constraints
        }
        self.assertIn("uq_embedding_statuses_entity", constraint_names)
        self.assertIn("ck_embedding_statuses_state", constraint_names)

    def test_embedding_states_cover_the_full_worker_lifecycle(self):
        self.assertEqual(
            {state.value for state in EmbeddingJobState},
            {"pending", "processing", "ready", "failed", "not_required", "missing"},
        )

    def test_content_digest_is_deterministic_and_sensitive_to_changes(self):
        self.assertEqual(content_digest("same"), content_digest("same"))
        self.assertNotEqual(content_digest("before"), content_digest("after"))


class EmbeddingDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_transition_uses_atomic_upsert(self):
        db = type("Database", (), {"execute": AsyncMock()})()
        await set_embedding_status(
            db,
            org_id=uuid.uuid4(),
            entity_type="faq",
            entity_id=uuid.uuid4(),
            state=EmbeddingJobState.PROCESSING,
            task_id="task-1",
        )

        statement = db.execute.await_args.args[0]
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT ON CONSTRAINT uq_embedding_statuses_entity", sql)

    async def test_pending_state_commits_before_task_publish(self):
        org_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        events: list[str] = []
        db = type("Database", (), {"commit": AsyncMock(side_effect=lambda: events.append("commit"))})()

        async def record_pending(*args, **kwargs):
            self.assertEqual(kwargs["state"], EmbeddingJobState.PENDING)
            events.append("pending")

        def publish(*args, **kwargs):
            events.append("publish")

        with (
            patch("app.worker.dispatch.set_embedding_status", side_effect=record_pending),
            patch("app.worker.tasks.generate_embeddings.apply_async", side_effect=publish),
        ):
            task_ids = await queue_embedding_tasks(
                db,
                [(org_id, "product", entity_id)],
                headers={"request_id": "request-1"},
            )

        self.assertEqual(events, ["pending", "commit", "publish"])
        self.assertEqual(len(task_ids), 1)

    async def test_bulk_rebuild_rejects_an_unbounded_task_fanout(self):
        class Result:
            def __init__(self, values):
                self.values = values

            def scalars(self):
                return self.values

        db = type(
            "Database",
            (),
            {
                "execute": AsyncMock(
                    side_effect=[
                        Result([uuid.uuid4() for _ in range(1001)]),
                        Result([]),
                    ]
                )
            },
        )()
        request = type(
            "Request",
            (),
            {"state": type("State", (), {"request_id": "request-1"})()},
        )()

        with patch("app.api.v1.knowledge._get_owned_org", AsyncMock()):
            with self.assertRaises(HTTPException) as caught:
                await rebuild_embeddings(
                    uuid.uuid4(),
                    request,
                    current_user=object(),
                    db=db,
                )

        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
