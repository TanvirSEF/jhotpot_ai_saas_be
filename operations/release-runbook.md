# Backend release runbook

This runbook assumes PostgreSQL and Redis are managed services and the same
immutable image digest is used by the migration job, API, worker, and Beat.
Never put production credentials in Compose files or source control.

## 1. Release gates

The release candidate must pass Ruff, mypy, Bandit, pip-audit, unit/contract
tests, disposable PostgreSQL/Redis integration tests, migration lifecycle and
drift checks, and the container smoke test. A `v*` tag publishes a GHCR image
with an SBOM, provenance, a semantic-version tag, and an immutable SHA tag.
Promote the digest that CI tested; do not rebuild separately per environment.

## 2. Pre-deployment preparation

1. Confirm the current Alembic revision and that `alembic heads` returns one
   head. Review every migration for locks, table rewrites, destructive DDL, and
   backward compatibility with the currently running release.
2. Prefer expand/contract migrations: add nullable structures first, deploy
   compatible code, backfill separately, and remove old structures in a later
   release.
3. Record the current application image digest, database revision, environment
   configuration version, and expected new digest in the change record.
4. Create and validate a fresh database backup:

   ```powershell
   .\scripts\backup_database.ps1 -DatabaseUrl $env:DATABASE_URL -OutputDirectory D:\nexussuite-backups
   ```

   Copy the resulting dump to independent encrypted storage and verify its
   retention policy. For a managed database, also create a provider snapshot or
   point-in-time recovery marker.

## 3. Deploy

1. Pull the exact release digest on the target host.
2. Run the migration as a single job and require a zero exit code:

   ```bash
   BACKEND_IMAGE=ghcr.io/OWNER/REPO@sha256:DIGEST docker compose -f compose.prod.yaml --profile release run --rm migrate
   ```

3. Confirm `alembic current` equals the single value from `alembic heads`.
4. Roll API instances first, keeping old instances available until new `/live`
   and `/ready` probes pass. Then roll workers. Run exactly one Beat instance.
5. Verify externally:

   ```powershell
   .\scripts\verify_release.ps1 -BaseUrl https://api.example.com
   ```

6. Check error rate, latency, queue depth, task failures, database saturation,
   and webhook processing for at least one normal traffic window.

## 4. Rollback decision

- If the new schema is backward compatible, stop new workers, restore the old
  image digest for API/workers, and re-run smoke checks. Leave the expanded
  schema in place for a forward fix.
- Do not blindly run `alembic downgrade` in production. A downgrade can destroy
  data or fail after the new application has written new shapes.
- If a migration corrupted data or made the schema incompatible, stop all
  writers, preserve evidence, restore the validated dump/provider snapshot to a
  new database, verify row counts and Alembic revision, switch traffic, and then
  restart workers. Record the recovery point and any accepted data loss.
- Prefer a forward-fix migration when data is intact. A rollback is complete
  only after readiness, core API, webhook ingestion, one background task, and a
  PDF export have been verified.

## 5. Ownership and secrets

The deployment platform must inject `.env` values from its secret manager. The
API port defaults to loopback and should be exposed through a TLS reverse proxy.
The API and worker require the same durable resume-export volume. Beat state is
separate and must have a single writer. Backup credentials should be read-only
except for the minimum privileges needed by `pg_dump`.
