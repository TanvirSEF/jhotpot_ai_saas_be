# NexusSuite API

Backend for NexusSuite AI, a multi-tenant SaaS platform that combines:

- Facebook Page automation with a merchant-managed knowledge base and RAG replies.
- ATS-focused resume creation, job-description optimization, and PDF export.

## Technology

- FastAPI and Uvicorn
- SQLAlchemy 2 async sessions with PostgreSQL
- pgvector for semantic retrieval
- Redis and Celery for background work
- OpenAI for embeddings and text generation
- Alembic for schema migrations
- Fernet encryption for stored Meta Page tokens
- WeasyPrint with an xhtml2pdf fallback for PDF generation

PostgreSQL is required. SQLite is not supported because the schema uses JSONB,
pgvector, and HNSW indexes.

## Local setup

1. Create and activate a Python 3.12+ virtual environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Start PostgreSQL 15+ and Redis. The database role must be able to enable the
   `vector` extension during the first migration.
4. Copy `.env.example` to `.env`, then replace every placeholder credential.
5. Apply migrations:

   ```bash
   alembic upgrade head
   ```

6. Start the API:

   ```bash
   uvicorn app.main:app --reload
   ```

7. Start a development worker that consumes all queues:

   ```bash
   celery -A app.worker.celery_app worker -Q webhooks,embeddings,default --loglevel=info
   ```

8. Start one Celery Beat process for durable webhook-inbox recovery:

   ```bash
   celery -A app.worker.celery_app beat --loglevel=info
   ```

   Run exactly one Beat scheduler per environment unless you deploy a
   distributed scheduler with its own leader-election guarantee.

The OpenAPI UI is available at `http://127.0.0.1:8000/docs`.

## Configuration

Configuration is loaded from `.env` through `app.core.config.Settings`. Startup
fails early when PostgreSQL, encryption, OpenAI, or Meta credentials are not
usable. Staging and production additionally require HTTPS URLs and an explicit
CORS allowlist.

RAG grounding is controlled by `RAG_MIN_SIMILARITY`,
`RAG_MAX_INPUT_CHARS`, and `RAG_MAX_CONTEXT_CHARS`. Start with the documented
defaults, then tune similarity only against a representative, labeled retrieval
evaluation set; lowering it increases answer coverage and hallucination risk.

Never commit `.env`. It is intentionally ignored by Git.

## API areas

The current OpenAPI document is the canonical route contract. The main route
groups are:

- `/api/v1/auth` — user registration and login
- `/api/v1/org` — business profiles and operating guidelines
- `/api/v1/knowledge` — products, FAQs, and semantic search
- `/api/v1/fb` — Meta OAuth, Page lifecycle/health, subscriptions, and webhook ingestion
- `/api/v1/resume` — resume CRUD, strict optimization, and durable PDF exports

Route names may differ from early PRD examples; resource ownership and behavior
take precedence over matching those example names.

## Database migrations

The API never mutates the schema during startup. Run `alembic upgrade head`
exactly once as a release job before starting or rolling API and worker
replicas. This makes migration failures visible before application traffic
reaches the new release.

Useful checks:

```bash
alembic heads
alembic history
python -m unittest discover -s tests -v
```

### Migration integration test

The migration lifecycle test intentionally upgrades and downgrades a disposable
database. Its safety guard only permits the local test database exposed on port
`55432`; it refuses any other target.

On a machine with Docker Desktop:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_migrations.ps1
```

The same lifecycle test runs automatically in the backend integration CI job. It
verifies a fresh upgrade, preservation of an existing user during the integer to
UUID conversion, downgrade/re-upgrade behavior, and `alembic check` model drift.
The disposable stack also runs Redis security-control and PostgreSQL tenant-
isolation tests.

## Authentication security

Access tokens include and validate issuer, audience, issue time, not-before,
expiry, token type, subject, and unique token ID claims. Tokens issued before
this claim contract was introduced are intentionally invalid; users must log in
again after deployment.

Login and registration use independent per-IP and per-account Redis limits. If
Redis cannot make the security decision, authentication fails closed with HTTP
503 instead of silently disabling brute-force protection. Meta OAuth state is
also registered in Redis and consumed atomically, preventing callback replay.

## PDF support

WeasyPrint requires native operating-system libraries. When those libraries are
not available, the application uses the pinned xhtml2pdf fallback. Production
containers should install and verify the WeasyPrint runtime explicitly.

The production image, Compose reference, CI gates, immutable-image release
workflow, and backup/rollback procedure are documented in
`operations/release-runbook.md`.

PDF generation is asynchronous: create a job with
`POST /api/v1/resume/{resume_id}/exports`, poll
`GET /api/v1/resume/{resume_id}/exports/{export_id}`, then download a ready file
from its `/download` child route. The older resume `/download` route serves the
latest ready export and never compiles inside the API process. Generated files
must pass A4 page, selectable-text, identity-anchor, and PDF readability checks
before becoming ready.

`RESUME_EXPORT_STORAGE_PATH` configures the local atomic storage adapter. API
and worker processes must share that durable path. A multi-host production
deployment should provide a shared volume or replace this adapter with object
storage while preserving the same key/read/write/delete contract.

## Monitoring and health

- `GET /live` is a dependency-free liveness probe.
- `GET /ready` checks PostgreSQL and Redis with the
  `HEALTHCHECK_TIMEOUT_SECONDS` bound; `/health` is its compatibility alias.
- `GET /metrics` exposes Prometheus metrics when `METRICS_ENABLED=true`.

Logs default to newline-delimited JSON (`LOG_FORMAT=json`). Send an optional
safe `X-Request-ID` header to correlate an API request with its Celery task;
otherwise the API creates one and returns it in the response. Prometheus must
scrape `/metrics` through an internal network or protected ingress, not a public
internet route. Alert on the webhook route p95 above 250 ms and vector operation
p95 above 20 ms using the exported histograms. Centralized structured logs are
the error investigation path; no external error-tracking SDK is required.

## Delivery plan

Backend hardening and feature-completion phases are tracked in
`docs/backend_implementation_plan.md`.
