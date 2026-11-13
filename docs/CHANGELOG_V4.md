# Repository descriptions — update to v4 (manual database administration)

Reviewed repository: `KTomsa/competitive-intelligence` main branch, November 2026. The original v2 documents remain a useful historical record; current normative instructions are the v3 replacements.

| File | Change |
|---|---|
| README.md | Full Fact or Watt description, truthful implementation status, src layout, Azure + Vercel topology, setup and endpoints. |
| .github/copilot-instructions.md | Azure SQL persistence, no SQLite, src canonical layout, Vercel entry point, worker, dependencies, database networking and deployment requirements. |
| .github/instructions/flask-backend.instructions.md | Removes SQLite; Azure ORM, session/existing-schema/worker requirements, WSGI entrypoint. |
| .github/instructions/tests.instructions.md | Replaces SQLite temporary-directory tests with mock repository units and isolated Azure integration DB. |
| .github/instructions/evidence-pipeline.instructions.md | Persists evidence, immutable snapshots and agent tool-call traces in Azure-backed stores. |
| docs/ARCHITECTURE.md | Updates deployment diagram and persistence; preserves KI(Q), agent and verification design. |
| docs/IMPLEMENTATION_PLAN.md | Infrastructure verification first, existing-schema compatibility, persistent worker and Vercel smoke tests. |
| docs/ARCHITECTURE_VALIDATION.md | New repository-specific findings and testable blockers. |
| docs/DATABASE_AZURE.md | Database connectivity, existing-schema mapping, runtime permissions and firewall guide. |
| docs/DEPLOYMENT_VERCEL.md | New explicit Flask src-entrypoint, Vercel settings, worker and deployment steps. |
| src/index.py | Thin Vercel app factory adapter. |
| vercel.json | Configures Python function settings without legacy builds or Next.js. |
| .env.example | Non-secret placeholders for Azure SQL, auth, LLM and optional Blob. |

**Not done automatically:** dependency installation, uv.lock updates, Flask app implementation, cloud deployments, manual Azure database administration, or writing any files to GitHub.

## v4 policy clarification
- DDL/DML and seed SQL files are **not part of the Flask repository**. The owner manages them manually in Azure.
- No Alembic migrations, automatic seeding, SQL execution how-tos or schema bootstrap steps in Copilot instructions.
- Normal SQLAlchemy runtime data operations and database connection testing remain part of the app.
