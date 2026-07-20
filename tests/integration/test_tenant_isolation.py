import os
import unittest
import uuid
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.v1.fb import _get_page_for_user
from app.api.v1.knowledge import _get_owned_org
from app.api.v1.resume import _get_user_export, _get_user_resume
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.security import create_access_token
from app.models import FbPage, Organization, Resume, ResumeExport, User
from tests.integration.test_migrations import ROOT, _guard_disposable_database


RUN_DATABASE_TESTS = os.getenv("RUN_DATABASE_INTEGRATION_TESTS") == "1"


@unittest.skipUnless(
    RUN_DATABASE_TESTS,
    "Set RUN_DATABASE_INTEGRATION_TESTS=1 to run tenant-isolation tests.",
)
class TenantIsolationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ["TEST_DATABASE_URL"]
        _guard_disposable_database(cls.database_url)
        alembic_config = Config(str(ROOT / "alembic.ini"))
        with patch.object(settings, "DATABASE_URL", cls.database_url):
            command.upgrade(alembic_config, "head")

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.database_url)
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.session = AsyncSession(bind=self.connection, expire_on_commit=False)

        self.owner = User(
            email=f"owner-{uuid.uuid4()}@example.com",
            full_name="Owner",
            hashed_password="test-hash",
            is_active=True,
        )
        self.other_user = User(
            email=f"other-{uuid.uuid4()}@example.com",
            full_name="Other User",
            hashed_password="test-hash",
            is_active=True,
        )
        self.session.add_all([self.owner, self.other_user])
        await self.session.flush()

        self.organization = Organization(
            user_id=self.owner.id,
            business_name="Owner Business",
        )
        self.resume = Resume(
            user_id=self.owner.id,
            title="Owner Resume",
            raw_json_data={"personal_info": {"full_name": "Owner"}},
            ats_score=0,
        )
        self.session.add_all([self.organization, self.resume])
        await self.session.flush()

        self.resume_export = ResumeExport(
            resume_id=self.resume.id,
            user_id=self.owner.id,
            state="pending",
            source_json_data=self.resume.raw_json_data,
            source_kind="raw",
            filename="resume_owner.pdf",
        )
        self.session.add(self.resume_export)
        await self.session.flush()

        self.page = FbPage(
            org_id=self.organization.id,
            page_id=f"page-{uuid.uuid4()}",
            page_name="Owner Page",
            encrypted_access_token="encrypted-test-token",
            is_bot_active=True,
        )
        self.session.add(self.page)
        await self.session.flush()

    async def asyncTearDown(self):
        await self.session.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def test_owner_can_access_owned_resources(self):
        organization = await _get_owned_org(
            self.organization.id,
            self.owner,
            self.session,
        )
        resume = await _get_user_resume(
            self.resume.id,
            self.owner,
            self.session,
        )
        page = await _get_page_for_user(
            self.session,
            self.page.id,
            self.owner,
        )
        resume_export = await _get_user_export(
            self.resume.id,
            self.resume_export.id,
            self.owner,
            self.session,
        )

        self.assertEqual(organization.id, self.organization.id)
        self.assertEqual(resume.id, self.resume.id)
        self.assertEqual(page.id, self.page.id)
        self.assertEqual(resume_export.id, self.resume_export.id)

    async def test_other_user_cannot_access_tenant_resources(self):
        with self.assertRaises(HTTPException) as organization_error:
            await _get_owned_org(
                self.organization.id,
                self.other_user,
                self.session,
            )
        self.assertEqual(organization_error.exception.status_code, 403)

        with self.assertRaises(HTTPException) as resume_error:
            await _get_user_resume(
                self.resume.id,
                self.other_user,
                self.session,
            )
        self.assertEqual(resume_error.exception.status_code, 404)

        with self.assertRaises(HTTPException) as export_error:
            await _get_user_export(
                self.resume.id,
                self.resume_export.id,
                self.other_user,
                self.session,
            )
        self.assertEqual(export_error.exception.status_code, 404)

        with self.assertRaises(HTTPException) as page_error:
            await _get_page_for_user(
                self.session,
                self.page.id,
                self.other_user,
            )
        self.assertEqual(page_error.exception.status_code, 404)

    async def test_access_token_resolves_only_the_active_subject(self):
        token = create_access_token(str(self.owner.id))
        current_user = await get_current_user(db=self.session, token=token)
        self.assertEqual(current_user.id, self.owner.id)

        self.owner.is_active = False
        await self.session.flush()
        with self.assertRaises(HTTPException) as inactive_error:
            await get_current_user(db=self.session, token=token)
        self.assertEqual(inactive_error.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
