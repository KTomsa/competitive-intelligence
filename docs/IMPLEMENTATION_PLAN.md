# Fact or Watt — implementation plan v3 (src + Azure + Vercel)

This plan replaces outdated SQLite-first steps. The current repository is an early Flask scaffold, not a completed application. Keep each phase independently demonstrable and do not claim unrun tests passed. Meet the source/claim verification rules in ARCHITECTURE.md, API_CONTRACTS.md and RULE_CATALOG.md.

## Phase 0 — confirm infrastructure constraints (blockers)

- Verify the exact Azure database service: **Azure SQL Database (MSSQL)** vs **Azure Database for PostgreSQL**. The code path below assumes MSSQL. Confirm accessible non-production test database.
- Confirm Vercel deployment plan, selected region, allowed outbound network path to Azure and whether Static IPs are available. Avoid broad Azure firewall rules.
- Confirm worker hosting: default **Azure Container Apps**; optionally Vercel Daemons beta after acceptance of beta risks and runtime configuration.
- Record decisions in `docs/DATABASE_AZURE.md` and `docs/DEPLOYMENT_VERCEL.md` before Copilot installs a DB driver.

## Phase 1 — Flask skeleton, Vercel entrypoint, persistence contracts

- Maintain `src/competitive_intelligence/app.py:create_app` and add `src/index.py:app`; use the existing `src` layout for all code, HTML and JS.
- Add `vercel.json` (function settings), `.env.example`, configure `uv` dependencies and update lockfile.
- Implement SQLAlchemy engine + scoped session/repository abstractions. Implement Azure SQL models mapped to the existing `ci` schema, which is manually administered outside the repository. Do not generate migrations, DDL, reference-data seeders or table creation on import/startup.
- Create Flask route/API skeleton plus offline fake-repository fixture that runs without Azure/LLM. Tests must not point at production DB.
- Acceptance: the fixture smoke test runs offline, `pytest` tests pass, and Vercel entrypoint import is tested. Azure network test is separately recorded as passed/blocked.

## Phase 2 — durable audit processing and three-screen UI

Implementation is now present for durable fixture jobs, leased worker processing, bounded retries, versioned fixture results and explicit publication. Offline tests and local read-only Azure connectivity/schema checks pass. Only the production database is available, so Azure write/restart acceptance remains pending a separate test database; do not run these tests against production. Offline implementation can continue, but Phase 2 must not yet be considered release-certified. See [Phase 2 operations and verification](PHASE_2.md).

- Implement authorized `POST /api/v1/audits` → 202 and job insert; GET state/events/results/latest, sanitization and publication.
- Implement a **separately started worker** that claims queued jobs atomically, sets leases, heartbeats, records progress, retries and idempotent completion. Use fake agents first.
- Jinja2/Bootstrap/vanilla JS: supplier selection, polling progress, results with provenance labels.
- Acceptance: after restarting the Flask process, queued/completed audit remains accessible from Azure; worker process can complete a fixture audit and publish a sanitized result.

## Phase 3 — Web Scout + source evidence

Bounded fetching, HTML canonicalization, the supplier registry, baseline reuse and snapshot fallback are implemented and connected to the durable audit worker. Real fetching of all eight registered pages passed locally on 2026-11-13. Shared Azure canonical-text archives, normalized audit-source links and active approved baseline reads are now implemented with lease fencing and bounded capture size. The owner confirmed `ok` as the successful capture status; it is the default. A separate Azure test database cannot be created, so actual write/restart/lock acceptance remains unverified while offline implementation continues. Raw HTML/PDF Blob archival and reference importers remain separate work. See [Phase 3 operations](PHASE_3.md) and the earlier [merge validation](MERGE_VALIDATION.md).

- Safe allowlisted live web fetch, hash of normalized text, immutable source metadata, baseline reuse and snapshot fallback with explicit timestamps and source status.
- Build parsing and reference data importers without relying on undocumented API endpoints. Large raw bytes use Azure Blob or equivalent external persistence.
- Acceptance: tests demonstrate three branches (unchanged/changed/unavailable); source URL, hash, origin and dates survive audit restarts and appear in UI.

## Phase 4 — specialized LLM agents + deterministic verdicts

The agent core is now implemented: literal-quote extraction with code-owned IDs/provenance, native Gemini and optional OpenAI Responses adapters, bounded Price/Green function-calling loops, Pydantic tool inputs, per-audit budgets, private hashed traces, and reviewed reference-bundle imports. The owner selected `gemini-3.5-flash-lite`; native structured extraction and a signed function-call/result round trip passed live smoke checks using the existing key, with no Azure writes. Azure currently blocks read-only inspection from this machine's IP, so database reference-table mappings were not guessed; reviewed file bundles are the implemented reference interface. Reference curation, product binding for new claims and golden-set evaluation remain acceptance work. See [Phase 4 operations](PHASE_4.md) for current test counts.

- Pydantic schemas/closed rule catalog; LLM Claim Extractor produces literal source quotes and valid rule IDs.
- Price and Green agents make actual bounded read-only tool calls; Python tools calculate costs and retrieve product-specific green evidence. Judge only downgrades to `unverifiable` and Strategist cites claim IDs.
- Acceptance: entire audit for one supplier completes with real, traceable sources without silently turning source failures into positive verdicts; record each agent/tool call and its source.

## Phase 5 — evaluation, hardening, deployment, demo

Application hardening and release tooling are implemented in **1.0.0rc1**: shared SQL launch quotas, fail-closed production settings, safe errors/request bounds, health checks, result integrity validation, reviewed exact-source product bindings, graceful worker termination, CI, an evaluation CLI with exact denominators and reviewed-data gates, and bounded deployed-preview smoke checks. A 24-quotation real-source annotation template and a separate 24-case synthetic price/green regression are available. Human annotation and reference curation have not been fabricated. Azure write/restart/contention and Vercel preview/production acceptance remain pending external infrastructure. See [Phase 5 release runbook](PHASE_5.md) and [annotation workflow](../eval/README.md). Phase 5 is not release-certified until those gates pass.

- Golden set of 20–30 human-annotated claims; calculate extraction precision/recall, rule accuracy and verdict accuracy with exact denominators.
- Test 3 suppliers, price and green; optional Customer agent and 5 suppliers only after MVP works. Load an approved last audit for public viewing.
- Deploy a Vercel preview: test Flask entrypoint, package imports, Azure SQL firewall/TLS, public latest, audit auth and worker. Then promote to production.
- Perform rollback/safety tests: DB unavailable, LLM timeout, snapshot fallback, worker restart, repeated job delivery, rate limiting, sanitized public output.
- Acceptance: verified preview/production URL, durable persisted audit, a worker completion trace and a reproducible demo; report explicitly distinguishes fixture vs live data.

## Copilot task prompt

> Implement the next unfinished phase from `docs/IMPLEMENTATION_PLAN.md`, honoring `docs/ARCHITECTURE.md`, `docs/DATABASE_AZURE.md`, `docs/DEPLOYMENT_VERCEL.md` and `.github/copilot-instructions.md`. Keep Python/Flask and `src/` layout. Do not add SQLite or Next.js. Show exact changed files and commands/tests actually executed. If Azure DB type or Vercel network access is unknown, stop only the affected infrastructure step and implement mockable components without inventing credentials.
