import unittest
from decimal import Decimal

from app.api.v1.knowledge import ProductCreate
from app.models import KnowledgeEmbedding
from app.services.embedding import build_guideline_text, build_product_text


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


if __name__ == "__main__":
    unittest.main()
