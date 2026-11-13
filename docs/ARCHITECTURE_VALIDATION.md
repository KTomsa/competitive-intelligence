# Fact or Watt — validation of architecture v3

**Result:** appropriate approach, subject to infrastructure verification. Preserves the multiagent methodology validated in v2; updates code layout, Azure persistence, Vercel deployment and current Vercel worker capabilities.

## Findings

| Severity | Finding | Change |
|---|---|---|
| P0 | Copilot instructions mention Azure DB but Flask backend and test instructions still require SQLite. | Unify on Azure-hosted DB with SQLAlchemy and a real isolated Azure test DB; mock repositories for unit tests. |
| P0 | Repository contains `src/competitive_intelligence/app.py` factory but no recognized Vercel entrypoint. | Add thin `src/index.py` exporting `app=create_app()` and check with Vercel preview. |
| P0 | No Vercel deployment guide/config file. | Add `docs/DEPLOYMENT_VERCEL.md`, `vercel.json`, example env, secret and smoke-test checklist. |
| P0 | Azure DB type cannot be inferred from code. | Confirm Azure SQL Database vs Azure Database for PostgreSQL before selecting dialect/driver; default docs assume MSSQL, not proof. |
| P1 | Worker job lifetime described but deployment location unclear. | Use separate Azure Container Apps worker; Vercel Daemons beta is optional, not the normal Flask Function. |
| P1 | Firewall/network path from Vercel to Azure unknown. | Confirm Vercel egress IPs and Azure firewall/TLS; no blanket public DB access. |
| P1 | Raw snapshot persistence cannot depend on Vercel local files. | Azure SQL metadata; Azure Blob for large immutable content. |
| P1 | `pyproject.toml` has no SQLAlchemy/DB driver/Alembic. | Add dependencies through uv, update lockfile, test imports on deployed Python 3.14. |
| P1 | Current README lacks meaningful descriptions. | Restore product overview, current status, layout, local dev and deployment guide. |
| P2 | Existing `project.scripts` points at nonexistent `competitive_intelligence:main`. | Remove or implement a real CLI entrypoint when adding worker/commands. |

## Domain constraints retained from v2

Price rank universal claim cannot be confirmed from a small sample; OPM ≠ customers; national energy residual mix ≠ product mix. Verdicts are code-derived, Judge downgrade-only. LLM evidence agents must use bounded read-only tool calls. Baseline/snapshot provenance and possible regulatory risks remain explicit.

## Preconditions for marking deployment complete

- [ ] Azure service type and database schema confirmed; connections tested with TLS from the actual web runtime.
- [ ] Preview Vercel Flask app imports without errors and serves `/` plus static assets.
- [ ] Authorized POST returns a durable job, worker consumes it, status and result survive web restarts.
- [ ] No secrets logged; public latest returns only approved sanitized results.
- [ ] No SQLite, Next.js or orphaned root-app instructions remain in current normative docs.

This validation covers repository contents and external technical documentation; it does **not** assert that an Azure database or Vercel deployment is already operational.
