# Phase 2: durable fixture audits

## Implemented behavior

The Flask factory selects `SqlAuditRepository` when `DATABASE_URL` is present. It creates a lazy, bounded SQLAlchemy engine using `mssql+mssqlpython`, enforces TLS configuration, and opens no connection or transaction at startup. Missing configuration selects the explicit offline in-memory demonstration. An Azure connection failure never switches a configured application to memory.

Authorized POST creates an audit, job and initial event in one transaction and returns 202. Database mode runs no pipeline in the request. Start the worker separately:

```bash
uv sync --locked
uv run fact-or-watt-worker check-db
uv run fact-or-watt-worker run
```

`run --once` processes at most one available delivery. The worker claims a job under SQL Server update/read-past locks, assigns a random delivery token, increments its attempt count, and uses the database clock for expiration. A worker heartbeat renews the lease during processing. Every stage, event, heartbeat, failure and completion verifies the current token and unexpired lease while holding the job lock. Losing a lease stops writes from the old delivery.

Exceptions requeue the job after `WORKER_RETRY_SECONDS` until its stored maximum attempt count is reached. An expired final delivery is marked dead/failed when the queue is next scanned. The pipeline reruns deterministic fixture stages on a retry; prior events remain as history and the completion transaction stores exactly one result. It does not resume halfway through a fixture stage.

## Existing schema and result representation

Read-only inspection confirmed Azure SQL Database and the existing `ci` operational schema on 2026-11-13. The application maps `audit_runs`, `audit_jobs`, `audit_events`, `briefings`, and `publications`; it creates no tables, migrations or reference records. UTC-aware application datetimes map to the existing UTC `datetime2` columns. Audit IDs are UUID strings in database mode; clients should continue treating IDs as opaque strings.

Fixture supplier IDs are fictional and deliberately do not reference the real supplier registry. The requested scope is stored in `audit_runs.request_config_json`. The completed typed result, including sources, claims, evidence, verdicts and briefing, is stored atomically as a versioned snapshot in `briefings.briefing_json`:

```json
{"version": 1, "audit_result": "AuditResult object"}
```

Here `audit_result` represents the complete JSON object, not a JSON string. The human-readable summary is also stored in `briefing_text`. This is a Phase 2 fixture representation. Normalized live source/capture/claim/evidence writes belong to the later source and agent phases; these tables are not populated with fictional reference data. Unknown or invalid stored-result versions fail clearly.

## Publication

Completion does not publish. An operator with database credentials explicitly approves a completed audit:

```bash
uv run fact-or-watt-worker publish AUDIT_ID --approved-by YOUR_OPERATOR_NAME
```

The repository builds a public `AuditResult` from declared DTO fields only. Headers, access codes, database configuration, delivery tokens, raw source bodies and provider traces have no public fields. Publication stores an independent JSON copy, approver and timestamp; a transaction serializes the current-publication pointer. Repeating approval of the current audit is idempotent. Earlier publications retain their approval history when a different audit becomes current. `GET /api/v1/audits/latest` reads only the explicitly published snapshot.

## Verification and limits

Executed during implementation:

- `uv sync` to repair missing baseline dependencies; baseline `pytest`: 54 passed.
- `uv add 'sqlalchemy[mssql-python]>=2.1' python-dotenv`; lockfile updated.
- `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`: 83 passed, 3 skipped (Azure integration is opt-in).
- `.venv/Scripts/python.exe -m competitive_intelligence.worker check-db`: local Azure connection, required operational columns and server-clock/UTC conversion passed.
- `uv sync --locked`: final worker entry points installed successfully.

Offline tests cover enqueue-only HTTP behavior, app recreation using an injected repository, no startup connection/seeding, safe database errors, queue lock compilation, UTC conversion, lease ownership, retries, and invalid snapshot handling. They do not prove cross-process database durability or actual multi-connection contention.

Only the production database is currently available; there is no separate test database. Azure write/restart verification therefore remains pending and must not use production. Offline development and tests can continue without a test database; production verification is limited to read-only checks.

The Azure tests remain optional and disabled by default. They use rollback-only transactions against a separately provisioned `TEST_DATABASE_URL` and refuse the same host/database as `DATABASE_URL`. They recreate repositories and exercise completion/publication, expired-lease fencing, retries and final expiration. If an isolated test database becomes available, run them there with an empty job queue:

```powershell
$env:RUN_AZURE_INTEGRATION = '1'
uv run pytest tests/test_azure_repository.py -q -rs
```

No DDL, migrations, seed data or production write integration tests are included. A separate multi-process worker/restart demonstration and Vercel network/deployment checks remain release acceptance work. `LLM_API_KEY` is configured but intentionally unused by this phase; `mode=live` continues returning 501 until live source intake and bounded agents are implemented.

## Changed files

| Area | Files |
|---|---|
| App and configuration | [app.py](../src/competitive_intelligence/app.py), [config.py](../src/competitive_intelligence/config.py), [.env.example](../.env.example) |
| Database mappings | [db.py](../src/competitive_intelligence/db.py), [models/__init__.py](../src/competitive_intelligence/models/__init__.py) |
| Repository and publication | [factory.py](../src/competitive_intelligence/services/factory.py), [repository.py](../src/competitive_intelligence/services/repository.py), [sql_repository.py](../src/competitive_intelligence/services/sql_repository.py), [publication.py](../src/competitive_intelligence/services/publication.py) |
| Worker and polling | [worker.py](../src/competitive_intelligence/worker.py), [app.js](../src/competitive_intelligence/static/js/app.js) |
| Tests | [conftest.py](../tests/conftest.py), [test_persistence.py](../tests/test_persistence.py), [test_azure_repository.py](../tests/test_azure_repository.py) |
| Dependencies | [pyproject.toml](../pyproject.toml), [uv.lock](../uv.lock) |
| Documentation | [README.md](../README.md), [DATABASE_AZURE.md](DATABASE_AZURE.md), [DEPLOYMENT_VERCEL.md](DEPLOYMENT_VERCEL.md), [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), [PHASE_2.md](PHASE_2.md) |
