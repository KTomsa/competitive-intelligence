# Fact or Watt — Competitive Intelligence

**Fact or Watt** is a Flask-based web application that helps analysts assess marketing claims made by Czech electricity suppliers against traceable reference evidence. It retrieves approved supplier web pages, extracts literal claims, uses bounded AI agents to select evidence-gathering tools, applies deterministic verification rules, and produces a neutral managerial briefing.

> Project status: **1.0.0rc1 release candidate**. Phases 1–4 and Phase 5 application hardening/evaluation tooling are implemented. This is not a certified production deployment: human golden-set annotation, real reference curation, Azure write/restart acceptance and Vercel preview/production checks remain required. See the [release runbook and acceptance record](docs/PHASE_5.md).

Phase 3 now adds shared Azure canonical-text archives, audit-source provenance links and reads of active approved baseline claims. The database owner confirmed `ok` as the success status; it is now the default, so no extra status configuration is required. A separate test database is unavailable; Azure write/restart behavior remains unverified while offline implementation continues. See [Phase 3 source storage](docs/PHASE_3.md) for configuration and verification limits.

## Technology

- **Backend:** Python 3.14, Flask, Jinja2, Pydantic v2, SQLAlchemy.
- **Frontend:** server-rendered HTML, vanilla JavaScript, CSS, optionally Bootstrap 5. No Next.js/React/TypeScript.
- **Persistent database:** an **Azure-hosted database**, assumed here to be **Azure SQL Database (Microsoft SQL Server)**; use SQLAlchemy 2.1+ with the `mssql+mssqlpython` dialect after validating the actual service and driver compatibility.
- **Deployment:** Vercel for Flask UI/API; a dedicated Python worker (recommended: Azure Container Apps; optional: Vercel Daemons public beta) for queued audits.
- **Dependencies:** `uv`, `pyproject.toml`, `uv.lock`.

If the existing Azure service is **Azure Database for PostgreSQL** instead, change the SQLAlchemy URL dialect/driver only after updating the environment assumptions. The current persistence target is MSSQL in Azure SQL Database.

## Project layout

```text
src/
  index.py                     # Vercel WSGI entry point (thin adapter)
  competitive_intelligence/
    __init__.py
    app.py                     # Flask application factory
    db.py                      # lazy Azure SQL engine, read-only schema check
    web/                       # page and API blueprints
    models/                    # existing Phase 2 operational table mappings
    schemas/                   # Pydantic DTOs
    services/                  # orchestration, repositories, publication
    agents/                    # literal extraction and bounded evidence agents
    tools/                     # deterministic fixture reference tools
    rules/                     # deterministic verdict rules
    connectors/                # bounded allowlisted supplier fetches
    templates/                 # Jinja2 HTML
    static/                    # JavaScript and CSS
    worker.py                  # independent worker/operator CLI
.github/
  copilot-instructions.md
  instructions/
docs/
  ARCHITECTURE.md
  DATABASE_AZURE.md
  DEPLOYMENT_VERCEL.md
  API_CONTRACTS.md
  RULE_CATALOG.md
  DATA_SOURCES.md
  IMPLEMENTATION_PLAN.md
tests/
pyproject.toml
uv.lock
vercel.json
.env.example
```

The supplier-page connector, parser, native Gemini adapter and optional OpenAI Responses adapter are implemented. The project uses `LLM_PROVIDER=gemini`, `LLM_MODEL=gemini-3.5-flash-lite` and the existing `LLM_API_KEY`. Follow `docs/IMPLEMENTATION_PLAN.md` for remaining acceptance work.

The release candidate adds shared SQL admission quotas, production configuration/host checks, bounded request bodies, health endpoints, safe errors with request IDs, integrity checks for published results, reviewed exact-source product bindings, graceful worker termination and offline CI. [24 source-backed annotation candidates](eval/review-candidates.json) are ready for analyst review; no human labels or production accuracy scores are invented.

## Database ownership

The Azure SQL `ci` schema and initial/reference data are prepared **manually outside this repository**. Do not add `.sql` DDL/DML, `database/` seed directories, automatic schema migrations or database bootstrap scripts. The Flask app uses SQLAlchemy to read reference data and persist operational audit data against existing tables. This does not prohibit normal application INSERT/UPDATE/SELECT transactions.

## Local development

In VS Code, use **Terminal → Run Task → Fact or Watt: web + worker** to start both required processes. Running only the Flask app queues database-backed audits but does not process them. The separate tasks have dedicated terminals; the worker prints readiness and every claimed audit. Stop any existing web/worker processes before starting the combined task to avoid duplicate instances.

```bash
uv sync
uv run flask --app competitive_intelligence.app:create_app run --debug
uv run pytest
```

VS Code's **Run Python File** also supports `src/competitive_intelligence/app.py`.
From the project root, the equivalent direct command is:

```bash
uv run python src/competitive_intelligence/app.py
```

Use a local `.env` (never commit it). Set `DATABASE_URL` and `AUDIT_ACCESS_CODE` for database-backed fixture audits. Fixture-mode unit tests use fake repository adapters without database credentials.

The application and worker read the local `.env`; host environment variables take precedence. `DATABASE_URL` activates Azure SQL, and `AUDIT_ACCESS_CODE` authorizes launches. Phase 4 uses an explicit `LLM_PROVIDER`, `LLM_MODEL` and `LLM_API_KEY`; a generic key alone does not identify a provider and is never forwarded automatically. The fixture pipeline needs no model key. Unit tests explicitly disable database/model access even when a local environment has credentials.

Run the worker separately from Flask:

```bash
uv run fact-or-watt-worker check-db
uv run fact-or-watt-worker check-llm-config
uv run fact-or-watt-worker run
# Process at most one available job:
uv run fact-or-watt-worker run --once
# Approve a completed audit for the public homepage:
uv run fact-or-watt-worker publish AUDIT_ID --approved-by YOUR_OPERATOR_NAME
# Offline smoke run, regardless of DATABASE_URL:
uv run fact-or-watt-fixture-audit
```

In database mode, POST only enqueues an audit; it stays queued until the worker runs. Startup performs no queries or demo writes. Completion does not publish automatically. Live mode accepts supplier IDs `cez`, `eon` and `innogy`; fixture mode uses the fictional IDs shown in the form. Without a database, the process-local demonstration runs audits inline. See [Phase 2 operations and verification](docs/PHASE_2.md) for storage details and opt-in test instructions.

Currently only the production database is available. Default tests stay offline; Azure write/restart tests are optional and require a separate test database. Do not point `TEST_DATABASE_URL` at production. A test database is not required to continue offline development.

## Audits

- `POST /api/v1/audits` (authorized) enqueues an audit and returns HTTP 202.
- `GET /api/v1/audits/<audit_id>` returns its persisted stage, events and warnings.
- `GET /api/v1/audits/<audit_id>/results` returns completed results.
- `GET /api/v1/audits/latest` returns the last explicitly published and sanitized audit.

A **separate worker** processes jobs. Do not run a long-running crawler/LLM pipeline in the Flask request or a detached thread inside a Vercel Function. Claims and evidence retain source URL, source location, timestamps and data origin (`live`, `baseline`, `snapshot`).

## Deployment

See **[Vercel deployment](docs/DEPLOYMENT_VERCEL.md)** and **[Azure database](docs/DATABASE_AZURE.md)**. Vercel supports Flask apps with a top-level WSGI `app` in `src/index.py`. Configure secrets through Vercel Project Settings; verify Azure firewall/network access, compatibility with the pre-provisioned schema and worker startup before publishing. No live deployment has been verified from this documentation alone.

Production requires `APP_ENV=production`, `DATABASE_URL`, a unique `AUDIT_ACCESS_CODE` of at least 16 characters and comma-separated `TRUSTED_HOSTS`. Vercel preview/production default to production checks when `APP_ENV` is unset. Missing production settings fail at startup. The direct development entrypoint has debugging disabled; use Flask's explicit `--debug` only for local development. `GET /health/live` checks the web process; `GET /health/ready` checks database connectivity and labels demonstration storage explicitly.

```bash
# Offline verification and package build
uv sync --locked
uv run pytest -q
uv build
# After genuine human annotation and a recorded model run
uv run fact-or-watt-evaluate --gold eval/reviewed.json --audit-result audit-result.json --require-reviewed --output evaluation-report.json
# Against an actual deployed preview with an approved live audit
uv run fact-or-watt-check-preview https://YOUR-PREVIEW.vercel.app
```

See [annotation instructions](eval/README.md) for evaluation inputs and [Phase 5](docs/PHASE_5.md) for quotas, monitoring, restart, rollback and release gates.

## Verification principles

Evidence agents may use only approved read-only tools; calculations and the initial verdict belong to deterministic Python rules. The Judge may downgrade a verdict to `unverifiable`, never upgrade it. Missing evidence is not proof of falsity. This is an internal Competitive Intelligence tool, not a legal assessment or a public ranking of dishonest suppliers.
