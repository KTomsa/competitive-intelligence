# Azure persistence — Fact or Watt (application-side specification v4)

## What the repository proves and what it does not

This project targets **Azure SQL Database (MSSQL)** with a pre-provisioned `ci` schema. The user manages table creation and initial/reference data manually in Azure outside the application repository. This guide covers **only application connectivity and runtime persistence**, not SQL execution or schema deployment.

## Storage design

- **Azure SQL:** suppliers, audit runs, job leases, status/events, claim metadata, evidence metadata, rule results, verdicts, sanitized publications, versioned reference manifests.
- **Azure Blob Storage (optional):** potentially large immutable raw HTML/PDF/XLSX snapshots. Azure SQL stores immutable content IDs, hash, URL, capture time and Blob URI. Avoid local files for persistent Vercel data.
- **Version-controlled Git files:** documentation, harmless reference/test fixtures, allowlisted suppliers and golden tests. No secrets or production audit state in Git.

Recommended relational models: `suppliers`, `audit_runs`, `audit_jobs`, `audit_events`, `sources`, `source_snapshots`, `baseline_approvals`, `claims`, `evidence_items`, `tool_calls`, `verdicts`, `publications`. Use stable IDs, UTC timestamps, provenance fields and constrained status/verdict enums. Align field names with `docs/API_CONTRACTS.md`; do not make incompatible changes silently.

## Python database stack (if Azure SQL Database)

- SQLAlchemy **2.1+**, Microsoft's `mssql-python` driver, **`mssql+mssqlpython`** dialect.
- Set `DATABASE_URL` via environment, e.g. `mssql+mssqlpython://USER:URLENCODED_PASSWORD@SERVER.database.windows.net:1433/DB?encrypt=yes`. This is a **placeholder** not a live credential.
- Do not disable TLS certificate validation to get around connectivity errors. Passwords containing reserved characters must be URL-encoded or built with SQLAlchemy `URL.create`.
- If using `pyodbc` instead, it needs a separately installed and verified ODBC Driver 18; do not assume that system driver exists in Vercel's runtime. `mssql-python` avoids that external driver-manager requirement according to Microsoft documentation, but its exact binary/packaging behavior must still be validated in the Vercel deployment.
- Control connection pools and `pool_pre_ping`; function concurrency can multiply open connections. Sessions are per request/job, never global shared sessions.

## Application connection and data-ownership rules

- Add the SQLAlchemy/driver dependencies to `pyproject.toml`; implement `src/competitive_intelligence/db.py` and ORM models aligned with the **existing** Azure tables and constraints.
- The repository must not include SQL DDL/DML, migration scripts, seed modules or a schema initialization command. The Flask factory, Vercel build and worker **must not** create or change tables.
- Operational runtime writes (audits, events, job leases, captures, claims, evidence and publications) are permitted through authenticated SQLAlchemy repositories. Reference-data imports or changes requiring manual SQL remain external.
- Unit tests use fake repository adapters. Integration tests use a separately provisioned Azure SQL test database via `TEST_DATABASE_URL`; do not run migrations or bootstrap scripts as part of tests.
- Never use SQLite as production persistence or silently fall back to it if Azure SQL is unavailable. Fail clearly and preserve safe read-only behavior where possible.

## Durable audit queue

A Flask request creates an audit record/job in one transaction and returns 202. A separately deployed Python worker atomically claims a queued job, leases it, heartbeats while running, records stage events, retries recoverable failures with a bound, and publishes only on successful completion. Make duplicate delivery idempotent. Do not rely on Flask background threads or filesystem state in Vercel Functions.

## Azure networking and deployment blocker

**An Azure SQL hostname and password are not sufficient to guarantee Vercel connectivity.** Check Azure firewall rules, public/private endpoint configuration, outbound IP addresses from Vercel, port 1433, encrypted TLS and server authentication. Vercel Static IPs are plan-dependent (at the time of writing, advertised for Pro/Enterprise). Do not open the database broadly to the internet as a permanent workaround. If your plan cannot establish a narrowly allowlisted connection, consider an authenticated Azure-hosted API/worker or moving the DB-accessing backend to Azure rather than weakening the firewall.

## References

- Microsoft SQLAlchemy + mssql-python: https://learn.microsoft.com/en-us/sql/connect/python/mssql-python/sqlalchemy-integration
- Microsoft's mssql-python installation: https://learn.microsoft.com/en-us/sql/connect/python/mssql-python/installation
- Vercel static egress IPs: https://vercel.com/changelog/static-ips-are-now-available-for-more-secure-connectivity
