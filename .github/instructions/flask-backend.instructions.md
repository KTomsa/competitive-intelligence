---
applyTo: "src/competitive_intelligence/**/*.py,src/index.py"
---
# Python Flask backend, Azure persistence, Vercel entrypoint

- Keep an application factory `create_app(config=None)` in `src/competitive_intelligence/app.py`; `src/index.py` exports only `app = create_app()`. Do not place business logic in the entrypoint or add a second Flask app.
- Use Blueprints, Pydantic validation, service and repository abstractions. Every request has a short-lived SQLAlchemy session, transaction boundaries and explicit rollback/close.
- Production persistence is Azure SQL Database **if the provisioned Azure resource is MSSQL**, using `mssql+mssqlpython`/SQLAlchemy 2.1+. If the Azure resource is PostgreSQL, configure the matching dialect instead. No SQLite as live or test database.
- Read `DATABASE_URL` from environment and redact it from logs. Do not create tables automatically at import/startup. All schema changes via Alembic; migrations run as an explicit deployment step with proper access controls.
- Write audit statuses, events, claims, evidence, provenance, published results and job leases to persistent Azure storage. Large snapshots belong in object storage or an explicitly bounded database storage strategy; never rely on Vercel local files.
- `POST /api/v1/audits` authenticates, creates the job transactionally, returns 202. A separate worker consumes jobs and heartbeats leases. No background thread spawned from request handlers.
- Keep database code injectible; offline fixture tests use in-memory fake repositories; integration tests use a separate disposable Azure database.
- Use `Decimal` for money and timezone-aware UTC timestamps; never mix OPM counts with customer counts. No LLM-generated SQL, arbitrary network fetch or shell execution.
- Verify Azure firewall, TLS, credential scope and connection-pool size against Vercel concurrency; avoid opening a new engine for every request.
