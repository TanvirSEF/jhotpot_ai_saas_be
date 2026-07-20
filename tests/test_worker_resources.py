import unittest
from unittest.mock import AsyncMock, patch

from app.worker.tasks import _task_db_session


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class WorkerResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_task_session_disposes_engine_after_success(self):
        engine = type("Engine", (), {"dispose": AsyncMock()})()
        session = object()

        with (
            patch("app.worker.db.create_async_engine", return_value=engine),
            patch(
                "app.worker.db.async_sessionmaker",
                return_value=lambda: _SessionContext(session),
            ),
        ):
            async with _task_db_session() as yielded:
                self.assertIs(yielded, session)

        engine.dispose.assert_awaited_once()

    async def test_task_session_disposes_engine_after_failure(self):
        engine = type("Engine", (), {"dispose": AsyncMock()})()

        with (
            patch("app.worker.db.create_async_engine", return_value=engine),
            patch(
                "app.worker.db.async_sessionmaker",
                return_value=lambda: _SessionContext(object()),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "task failed"):
                async with _task_db_session():
                    raise RuntimeError("task failed")

        engine.dispose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
