import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractTests(unittest.TestCase):
    def test_api_startup_does_not_run_migrations(self) -> None:
        source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("command.upgrade", source)
        self.assertNotIn("redis.ping", source)

    def test_container_runs_as_non_root_and_includes_pdf_runtime(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("USER app", dockerfile)
        self.assertIn("libpango-1.0-0", dockerfile)
        self.assertIn('CMD ["uvicorn"', dockerfile)
        self.assertNotIn("COPY . ", dockerfile)

    def test_ci_enforces_required_gates(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "backend-ci.yml").read_text(
            encoding="utf-8"
        )
        for command in ("ruff check", "mypy app", "bandit -r", "pip-audit"):
            self.assertIn(command, workflow)
        self.assertIn("compose.test.yaml", workflow)
        self.assertIn("docker/build-push-action", workflow)

    def test_release_services_share_one_image_contract(self) -> None:
        compose = (ROOT / "compose.prod.yaml").read_text(encoding="utf-8")
        for service in ("api:", "worker:", "beat:", "migrate:"):
            self.assertIn(service, compose)
        self.assertIn("profiles: [release]", compose)


if __name__ == "__main__":
    unittest.main()
