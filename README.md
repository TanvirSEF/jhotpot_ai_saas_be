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

1. Create and activate a Python 3.11+ virtual environment.
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

The OpenAPI UI is available at `http://127.0.0.1:8000/docs`.

## Configuration

Configuration is loaded from `.env` through `app.core.config.Settings`. Startup
fails early when PostgreSQL, encryption, OpenAI, or Meta credentials are not
usable. Staging and production additionally require HTTPS URLs and an explicit
CORS allowlist.

Never commit `.env`. It is intentionally ignored by Git.

## API areas

The current OpenAPI document is the canonical route contract. The main route
groups are:

- `/api/v1/auth` — user registration and login
- `/api/v1/org` — business profiles and operating guidelines
- `/api/v1/knowledge` — products, FAQs, and semantic search
- `/api/v1/fb` — Meta OAuth, connected Pages, and webhook ingestion
- `/api/v1/resume` — resume CRUD, optimization, and PDF download

Route names may differ from early PRD examples; resource ownership and behavior
take precedence over matching those example names.

## Database migrations

The application runs migrations during startup for the current development
workflow. Production deployments should run `alembic upgrade head` as a single
release job before starting or rolling API replicas.

Useful checks:

```bash
alembic heads
alembic history
python -m unittest discover -s tests -v
```

## PDF support

WeasyPrint requires native operating-system libraries. When those libraries are
not available, the application uses the pinned xhtml2pdf fallback. Production
containers should install and verify the WeasyPrint runtime explicitly.

## Delivery plan

Backend hardening and feature-completion phases are tracked in
`docs/backend_implementation_plan.md`.
