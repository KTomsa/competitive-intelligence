# Fact or Watt — Competitive Intelligence

**Fact or Watt** is a Flask-based web application that helps analysts assess marketing claims made by Czech electricity suppliers against traceable reference evidence. It retrieves approved supplier web pages, extracts literal claims, uses bounded AI agents to select evidence-gathering tools, applies deterministic verification rules, and produces a neutral managerial briefing.

> Project status: early implementation. The current repository contains a Flask skeleton and design documents; production database integration, live agents and Vercel deployment must still be implemented and tested.

## Technology

- **Backend:** Python 3.14, Flask, Jinja2, Pydantic v2, SQLAlchemy, Alembic.
- **Frontend:** server-rendered HTML, vanilla JavaScript, CSS, optionally Bootstrap 5. No Next.js/React/TypeScript.
- **Persistent database:** an **Azure-hosted database**, assumed here to be **Azure SQL Database (Microsoft SQL Server)**; use SQLAlchemy 2.1+ with the `mssql+mssqlpython` dialect after validating the actual service and driver compatibility.
- **Deployment:** Vercel for Flask UI/API; a dedicated Python worker (recommended: Azure Container Apps; optional: Vercel Daemons public beta) for queued audits.
- **Dependencies:** `uv`, `pyproject.toml`, `uv.lock`.

If the existing Azure service is **Azure Database for PostgreSQL** instead, change the SQLAlchemy URL dialect/driver and database-specific migrations accordingly. The repository does not currently identify which database service was provisioned.

## Project layout

```text
src/
  index.py                     # Vercel WSGI entry point (thin adapter)
  competitive_intelligence/
    __init__.py
    app.py                     # Flask application factory
    web/                       # page and API blueprints (planned)
    models/                    # SQLAlchemy models (planned)
    schemas/                   # Pydantic DTOs (planned)
    services/                  # audit orchestration, persistence, publication (planned)
    agents/                    # LLM agents (planned)
    tools/                     # allowlisted deterministic tools (planned)
    rules/                     # deterministic verdict rules (planned)
    connectors/                # approved data sources (planned)
    templates/                 # Jinja2 HTML
    static/                    # JavaScript and CSS
    worker.py                  # independent worker CLI (planned)
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

The directory list is the **target architecture**, not a claim that each module already exists. Follow `docs/IMPLEMENTATION_PLAN.md` instead of generating empty directories.

## Local development

```bash
uv sync
uv run flask --app competitive_intelligence.app:create_app run --debug
uv run pytest
```

Use a local `.env` (never commit it). For real DB-backed functionality, set `DATABASE_URL`, `SECRET_KEY` and the relevant audit/LLM credentials. For fixture-mode tests, fake repository adapters are used without production DB credentials.

## Audits

- `POST /api/v1/audits` (authorized) enqueues an audit and returns HTTP 202.
- `GET /api/v1/audits/<audit_id>` returns its persisted stage, events and warnings.
- `GET /api/v1/audits/<audit_id>/results` returns completed results.
- `GET /api/v1/audits/latest` returns the last explicitly published and sanitized audit.

A **separate worker** processes jobs. Do not run a long-running crawler/LLM pipeline in the Flask request or a detached thread inside a Vercel Function. Claims and evidence retain source URL, source location, timestamps and data origin (`live`, `baseline`, `snapshot`).

## Deployment

See **[Vercel deployment](docs/DEPLOYMENT_VERCEL.md)** and **[Azure database](docs/DATABASE_AZURE.md)**. Vercel supports Flask apps with a top-level WSGI `app` in `src/index.py`. Configure secrets through Vercel Project Settings; verify Azure firewall/network access, migrations and worker startup before publishing. No live deployment has been verified from this documentation alone.

## Verification principles

Evidence agents may use only approved read-only tools; calculations and the initial verdict belong to deterministic Python rules. The Judge may downgrade a verdict to `unverifiable`, never upgrade it. Missing evidence is not proof of falsity. This is an internal Competitive Intelligence tool, not a legal assessment or a public ranking of dishonest suppliers.
