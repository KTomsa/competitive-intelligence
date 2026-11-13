# Repository descriptions — update to v3

Reviewed repository: `KTomsa/competitive-intelligence` main branch, November 2026. The original v2 documents remain a useful historical record; current normative instructions are the v3 replacements.

| File | Change |
|---|---|
| README.md | Full Fact or Watt description, truthful implementation status, src layout, Azure + Vercel topology, setup and endpoints. |
| .github/copilot-instructions.md | Azure SQL persistence, no SQLite, src canonical layout, Vercel entry point, worker, dependencies, database networking and deployment requirements. |
| .github/instructions/flask-backend.instructions.md | Removes SQLite; Azure ORM, session/migration/worker requirements, WSGI entrypoint. |
| .github/instructions/tests.instructions.md | Replaces SQLite temporary-directory tests with mock repository units and isolated Azure integration DB. |
| .github/instructions/evidence-pipeline.instructions.md | Persists evidence, immutable snapshots and agent tool-call traces in Azure-backed stores. |
| docs/ARCHITECTURE.md | Updates deployment diagram and persistence; preserves KI(Q), agent and verification design. |
| docs/IMPLEMENTATION_PLAN.md | Infrastructure verification first, Azure migrations, persistent worker and Vercel smoke tests. |
| docs/ARCHITECTURE_VALIDATION.md | New repository-specific findings and testable blockers. |
| docs/DATABASE_AZURE.md | New database model, driver, configuration, migrations and firewall guide. |
| docs/DEPLOYMENT_VERCEL.md | New explicit Flask src-entrypoint, Vercel settings, worker and deployment steps. |
| src/index.py | Thin Vercel app factory adapter. |
| vercel.json | Configures Python function settings without legacy builds or Next.js. |
| .env.example | Non-secret placeholders for Azure SQL, auth, LLM and optional Blob. |

**Not done automatically:** dependency installation, uv.lock updates, Flask app implementation, cloud deployments, Azure migrations, or writing any files to GitHub.
