# Azure persistence — Fact or Watt (repository specification v3)

## What the repository proves and what it does not

The existing Copilot instructions say an Azure database instance has been created, but the repository does not identify whether it is **Azure SQL Database (MSSQL)** or **Azure Database for PostgreSQL**, and it does not implement a working database connection yet. **This guide assumes Azure SQL Database.** Confirm the real Azure resource type before installing a driver or migrating data.

## Storage design

- **Azure SQL:** suppliers, audit runs, job leases, status/events, claim metadata, evidence metadata, rule results, verdicts, sanitized publications, versioned reference manifests.
- **Azure Blob Storage (optional):** potentially large immutable raw HTML/PDF/XLSX snapshots. Azure SQL stores immutable content IDs, hash, URL, capture time and Blob URI. Avoid local files for persistent Vercel data.
- **Version-controlled Git files:** documentation, harmless reference/test fixtures, allowlisted suppliers and golden tests. No secrets or production audit state in Git.

Recommended relational models: `suppliers`, `audit_runs`, `audit_jobs`, `audit_events`, `sources`, `source_snapshots`, `baseline_approvals`, `claims`, `evidence_items`, `tool_calls`, `verdicts`, `publications`. Use stable IDs, UTC timestamps, provenance fields and constrained status/verdict enums. Align field names with `docs/API_CONTRACTS.md`; do not make incompatible changes silently.

## Python database stack (if Azure SQL Database)

- SQLAlchemy **2.1+**, Microsoft's `mssql-python` driver, **`mssql+mssqlpython`** dialect, Alembic.
- Set `DATABASE_URL` via environment, e.g. `mssql+mssqlpython://USER:URLENCODED_PASSWORD@SERVER.database.windows.net:1433/DB?encrypt=yes`. This is a **placeholder** not a live credential.
- Do not disable TLS certificate validation to get around connectivity errors. Passwords containing reserved characters must be URL-encoded or built with SQLAlchemy `URL.create`.
- If using `pyodbc` instead, it needs a separately installed and verified ODBC Driver 18; do not assume that system driver exists in Vercel's runtime. `mssql-python` avoids that external driver-manager requirement according to Microsoft documentation, but its exact binary/packaging behavior must still be validated in the Vercel deployment.
- Control connection pools and `pool_pre_ping`; function concurrency can multiply open connections. Sessions are per request/job, never global shared sessions.

If the Azure resource is PostgreSQL, use `postgresql+psycopg`, the PostgreSQL migrations and network/security requirements instead; update this document and the Copilot instructions first.

## Migration and development rules

1. Add relevant dependencies with `uv add 'sqlalchemy[mssql-python]>=2.1' alembic pydantic` (after confirming MSSQL). Run `uv lock` and commit `pyproject.toml`/`uv.lock` together.
2. Implement `src/competitive_intelligence/db.py` engine/session factory and SQLAlchemy models under `src/competitive_intelligence/models/`.
3. Configure `alembic` with model metadata and a connection read securely from the environment. Apply migrations explicitly from a controlled runner; **never** at Vercel request startup or a build step.
4. For unit tests, inject fake repositories and mock external sources. For integration tests use `TEST_DATABASE_URL` pointing only at an isolated disposable test database.
5. Never use SQLite as the target persistence layer or as an implicit fallback during Azure SQL failures. Fail clearly and preserve public read-only availability where possible.

## Durable audit queue

A Flask request creates an audit record/job in one transaction and returns 202. A separately deployed Python worker atomically claims a queued job, leases it, heartbeats while running, records stage events, retries recoverable failures with a bound, and publishes only on successful completion. Make duplicate delivery idempotent. Do not rely on Flask background threads or filesystem state in Vercel Functions.

## Azure networking and deployment blocker

**An Azure SQL hostname and password are not sufficient to guarantee Vercel connectivity.** Check Azure firewall rules, public/private endpoint configuration, outbound IP addresses from Vercel, port 1433, encrypted TLS and server authentication. Vercel Static IPs are plan-dependent (at the time of writing, advertised for Pro/Enterprise). Do not open the database broadly to the internet as a permanent workaround. If your plan cannot establish a narrowly allowlisted connection, consider an authenticated Azure-hosted API/worker or moving the DB-accessing backend to Azure rather than weakening the firewall.

## References

- Microsoft SQLAlchemy + mssql-python: https://learn.microsoft.com/en-us/sql/connect/python/mssql-python/sqlalchemy-integration
- Microsoft's mssql-python installation: https://learn.microsoft.com/en-us/sql/connect/python/mssql-python/installation
- Vercel static egress IPs: https://vercel.com/changelog/static-ips-are-now-available-for-more-secure-connectivity
