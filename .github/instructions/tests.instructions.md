---
applyTo: "tests/**/*.py"
---
# Tests for Azure-backed Flask and agent pipeline

- Use pytest and Flask test client. Unit tests: mock/in-memory repository + fake LLM/network without requiring Azure secrets. Integration tests: `TEST_DATABASE_URL` for a **disposable Azure test database**; never use production credentials and never silently substitute SQLite.
- Test SQLAlchemy model compatibility with the separately provisioned Azure SQL test schema (tables, dialect types, foreign keys) without executing DDL, migrations or seed SQL. Unit tests use fake repositories; integration tests use scoped test transactions.
- Test job enqueue/status/claim/heartbeat/lease expiry/retry, idempotent completion, multi-worker contention and persistence across Flask process restarts.
- Test public `GET latest` vs protected `POST audits`, sanitized output, access-code rate limiting and missing credential fail-closed behavior.
- Test live/unchanged-baseline/fallback snapshot provenance, quote validation, approved LLM tools only, `Decimal` price calculation and Judge downgrade-only invariant.
- Test the Vercel `src/index.py` import and Flask routes. Never claim actual Vercel deployment or Azure connectivity passed unless independently tested.
