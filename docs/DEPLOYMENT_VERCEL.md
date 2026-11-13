# Deploying Fact or Watt to Vercel (Flask + src layout + Azure SQL)

**Deployment target:** Vercel serves the Flask HTML UI and short HTTP API. Persistent state lives in Azure SQL Database (MSSQL assumed; confirm the actual Azure DB type). A separate Python worker processes live audits; it does not run as a detached HTTP-request thread. This is a **deployment specification**, not proof the current project is deployed.

## 1. Supported project structure and entry point

Keep the factory in `src/competitive_intelligence/app.py` and create the thin, recognized Vercel WSGI entrypoint `src/index.py`:

```python
from competitive_intelligence.app import create_app

app = create_app()
```

Vercel recognizes top-level `app` in `src/index.py` and supports Flask, `uv`/`pyproject.toml`, and Python 3.14. The repository currently has `.python-version=3.14` and `pyproject.toml` declaring `>=3.14`. Do not introduce Next.js, Node or a second Flask application. The app factory must **not** connect to the database or create/alter schema or run SQL seed scripts at module import; fail with a readable configuration error when a DB-dependent endpoint is used without DB configuration.

## 2. Minimal `vercel.json`

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "functions": {
    "src/index.py": {
      "maxDuration": 60,
      "excludeFiles": "{tests/**,docs/**,eval/**}"
    }
  }
}
```

`vercel.json` is optional for basic Flask autodetection but recommended for explicit function settings. **No legacy `builds`, Next.js routing or rewrite is required for a correctly autodetected Flask entrypoint.** Validate the actual resolution using `vercel dev` and a preview deployment. `maxDuration` must stay within your plan's limits; 60 seconds is a conservative initial HTTP timeout, not a worker lease.

## 3. Dependencies and lockfile

Maintain a single `pyproject.toml` + `uv.lock` workflow. Add the Azure SQL driver and SQLAlchemy dependencies:

```bash
uv add 'sqlalchemy[mssql-python]>=2.1' pydantic python-dotenv
uv sync
uv run pytest
```

Vercel supports Python 3.14 and installs from `pyproject.toml`/`uv.lock`. Do not manually freeze the environment into a conflicting `requirements.txt`. Ensure native dependencies actually import on Vercel before claiming that Azure connectivity works.

## 4. Vercel project settings

1. Import GitHub repository `KTomsa/competitive-intelligence` at https://vercel.com/new.
2. Project root directory: repository root (`.`). Framework: Flask (autodetected); do not select Next.js.
3. Set environment variables under Project → Settings → Environment Variables: `DATABASE_URL`, `SECRET_KEY`, `AUDIT_ACCESS_CODE`, `LLM_API_KEY` (only if live agents enabled); other provider variables only if used.
4. Configure production and preview secrets separately; preview should not use production write credentials. Do not expose credentials through `NEXT_PUBLIC_*`, templates, browser JavaScript or logs.
5. Deploy preview; verify `/`, status API, sanitized `latest` and a database health check with no credential leakage. Only then deploy production.
6. Use `vercel dev` locally, `vercel` for preview and `vercel --prod` for production when CLI-based deployment is desired.

## 5. Network path to Azure SQL

Verify that Azure SQL allows connections from the selected Vercel runtime and region. Azure SQL firewall/private endpoint rules may block Vercel dynamic outbound IPs. Vercel Static IPs are available on certain paid plans. A database password **does not** bypass firewall rules. Avoid permanent `0.0.0.0/0` or blanket public access. If constrained networking cannot be provided, host the database-accessing API in Azure behind authenticated HTTPS rather than weakening the DB network policy.

Keep encryption enabled and database permissions least-privileged. Validate driver load, DNS, TLS, connection timeout and a simple read query in a Vercel preview; then test writing a single isolated audit record.

## 6. Worker architecture and where it runs

The Phase 2 fixture worker is implemented. Start it in the separately hosted worker environment with `uv run fact-or-watt-worker run`; use `run --once` for a single delivery and `check-db` for a read-only connectivity/column check. Set the same `DATABASE_URL` as the web backend. Optional settings: `WORKER_LEASE_SECONDS=60`, `WORKER_MAX_ATTEMPTS=3`, `WORKER_RETRY_SECONDS=10`. Publish an approved result using `publish AUDIT_ID --approved-by YOUR_OPERATOR_NAME`. Database mode never seeds or publishes demo data on web startup. Live collection/LLM agents remain unimplemented. See [Phase 2 operations](PHASE_2.md).

**Recommended:** deploy the Python worker separately using Azure Container Apps (or another stable always-on/triggered Python process) in an environment allowed to connect to Azure SQL. Flask creates jobs with `status=queued`; the worker claims jobs atomically and updates audit events; browser polls GET status.

**Vercel-only alternative:** Vercel Daemons became available in public beta in October 2026 for long-running pull-based workers. They require container/daemon configuration and are not equivalent to a normal Flask Function. Only adopt after validating product access, pricing, limits, container configuration and your database networking. The exact Daemon configuration is intentionally **not** included in this MVP setup.

Do not run a background thread after an HTTP response, and do not store job state only in `/tmp`, a local SQLite file or process memory. Use persisted Azure job state, retries, leases and idempotent completion.

## 7. Existing database compatibility and application release

Azure SQL schema and initial reference records are managed separately by the project owner. **Neither Vercel nor Flask runs DDL, seed scripts or migrations.** The deployed application only connects to the existing `ci` tables and uses scoped runtime SQLAlchemy transactions. Before publishing a new version, confirm that its ORM models are compatible with the already provisioned schema and that network, permissions and worker behavior are valid.

## 8. Deployment smoke checks

- `src/index.py` imports and exposes `app`; root `/` returns a page and Flask templates/static assets load.
- Vercel preview functions start with Python 3.14 and declared `uv` dependencies.
- Azure database driver loads; TLS + firewall permit a read. Credentials do not appear in logs or responses.
- Unauthenticated `POST /api/v1/audits` fails, authorized POST persists a queued job and returns 202.
- Worker claims and completes a **fixture** audit; GET status/results sees data after a Vercel instance restart; public `latest` exposes sanitized content.
- Snapshot and baseline origins are clearly visible; no source errors or real/fixture confusion.

## Sources (checked November 2026)

- Vercel Flask deployment: https://vercel.com/kb/guide/ship-a-flask-app-on-vercel
- Vercel Python GA and Daemons: https://vercel.com/changelog/python-on-vercel-is-now-generally-available
- Vercel Daemons public beta: https://vercel.com/changelog/vercel-daemons-is-now-in-public-beta
- Vercel Function duration: https://vercel.com/docs/functions/configuring-functions/duration
- Microsoft SQLAlchemy/mssql-python: https://learn.microsoft.com/en-us/sql/connect/python/mssql-python/sqlalchemy-integration
