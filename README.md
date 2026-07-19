# NexusSuite API

FastAPI backend for NexusSuite — auth, a Facebook bot service, and a CV/resume builder.

## Tech stack

- **FastAPI** + **Uvicorn** — web framework / ASGI server
- **SQLAlchemy 2.0** — ORM (SQLite for dev, PostgreSQL for prod)
- **Pydantic v2** + **pydantic-settings** — validation & config
- **PyJWT** + **passlib[bcrypt]** — JWT auth & password hashing

## Project structure

```
app/
├── main.py              # entry point: app, CORS, routers, DB bootstrap
├── api/v1/              # versioned API routes
│   ├── auth.py          # register + login (JWT)
│   ├── bot.py           # Facebook bot (placeholder)
│   └── resume.py        # CV builder (placeholder)
├── core/                # config & security
│   ├── config.py        # .env via pydantic-settings
│   └── security.py      # password hashing + JWT
├── db/session.py        # engine, session factory, Base, get_db
└── models/all_models.py # SQLAlchemy models
```

## Setup

```bash
# 1. Create & activate a virtual environment (already present in ./venv)
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env              # then edit values as needed

# 4. Run
uvicorn app.main:app --reload
```

API docs are at **http://127.0.0.1:8000/docs** (Swagger UI).

## Endpoints

| Method | Path                        | Description              |
|--------|-----------------------------|--------------------------|
| GET    | `/`                         | Project info             |
| GET    | `/health`                   | Health check             |
| POST   | `/api/v1/auth/register`     | Register a new user      |
| POST   | `/api/v1/auth/login`        | Login, returns a JWT     |
| GET    | `/api/v1/bot/`              | Bot status (placeholder) |
| GET    | `/api/v1/resume/`           | Resume service (placeholder) |

## Notes

- Tables are auto-created on startup for dev convenience. Use **Alembic** for real migrations.
- Change `SECRET_KEY` before deploying.
