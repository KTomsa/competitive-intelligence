# Same-branch merge validation — 2026-11-13

## Merge scope

The local Azure persistence implementation was saved as `3bdec1c`. Only the current branch's upstream, `origin/feature/phase-1-fixture-pipeline` at `908d63a`, was merged into `feature/phase-1-fixture-pipeline`. Conflict resolution used `-X ours`, followed by integration repairs. No other branch was merged and nothing was pushed.

The combined application keeps Azure enqueue-only HTTP behavior, lease fencing, heartbeat/retry handling and explicit publication, while adding the incoming supplier registry, bounded live fetch, parser and baseline/snapshot fallback.

## Integration repairs

- [Application factory](../src/competitive_intelligence/app.py): retain repository injection and add pipeline-context injection; restore imports and construct the context before seeding or registering services.
- [Worker](../src/competitive_intelligence/worker.py): build the source-intake context and dispatch fixture/live jobs within the existing lease/retry boundary. Keep the fixture CLI offline and support existing fixture-bundle callers.
- [API](../src/competitive_intelligence/web/api.py): preserve enqueue-only behavior for durable repositories; inline processing is limited to the process-local demonstration.
- [SQL repository](../src/competitive_intelligence/services/sql_repository.py): distinguish fixture and live pipeline metadata in saved results.
- [Dependencies](../pyproject.toml) and [lockfile](../uv.lock): resolve the combined dependency graph with `uv`, including HTTPX and Beautiful Soup. The automatic merge initially left dependencies without package records; the regenerated lockfile passes locked synchronization.
- [Persistence tests](../tests/test_persistence.py): add a queued-live-job/worker/publication regression and fixture/live SQL serialization round trips using a session fake.
- [README](../README.md), [Phase 2 operations](PHASE_2.md), [implementation plan](IMPLEMENTATION_PLAN.md), [deployment guide](DEPLOYMENT_VERCEL.md) and [environment example](../.env.example): update the implemented behavior and remaining limits.

The incoming [source intake](../src/competitive_intelligence/services/web_scout.py), [connector](../src/competitive_intelligence/connectors/supplier_web.py), [parser](../src/competitive_intelligence/parsers/html.py), [registry](../src/competitive_intelligence/registry/suppliers.json), [stores](../src/competitive_intelligence/services/stores.py), [pipeline](../src/competitive_intelligence/services/orchestrator.py), [UI](../src/competitive_intelligence/templates/index.html), [JavaScript](../src/competitive_intelligence/static/js/app.js), bundled snapshots and tests were retained.

## Executed checks

| Check | Result |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider -rs` | **111 passed, 3 skipped**; skips are the opt-in Azure integration tests |
| `uv sync --locked` | Passed; 45 packages resolved/checked |
| `.venv/Scripts/python.exe -m competitive_intelligence.worker fixture` | Completed offline; 9 verdicts; live/baseline/snapshot fixture provenance retained |
| `.venv/Scripts/python.exe -m competitive_intelligence.worker check-db` | Azure connectivity, existing operational columns and server UTC conversion passed; read-only |
| Configured application through Flask test client | `/`, JavaScript and CSS: 200; latest publication: 404 (none published); invalid audit ID: 404 |
| Real supplier intake through worker | All 8 registered ČEZ/E.ON/innogy pages returned live sources; audit completed in memory with temporary snapshot storage |
| `uv build --out-dir <temporary-build-directory>` | Source distribution and wheel built successfully |
| Wheel asset inspection | Application models, registry, demo fixture, source snapshot, HTML template and JavaScript included |
| Editor diagnostics | No reported errors after integration repairs |
| `git diff --check` and `git diff --cached --check` | Passed |

### Browser checks

The local test server used the actual Flask app, templates and JavaScript with mock supplier responses, in-memory audits and the test-only access code `validation-only`. It did not write to Azure.

- Homepage loads the public demonstration, its 9 claims, verdict matrix and source-origin labels.
- Wrong-code submission shows the authorization error and clears the code field.
- Empty supplier selection shows the Czech validation message.
- Mode selection switches between real and fictional suppliers.
- Fixture submission navigates to the audit, reaches 100%, renders 9 claims, and exposes evidence details (6 facts for the first price claim).
- Live submission completes with 3 suppliers and 8 source labels; the missing-extraction warning remains visible.
- Reload restores results and 8 fixture events. A simulated results-endpoint 503 shows an error, retries successfully and retains exactly 8 events.
- At a 390 px viewport, the document has no horizontal page overflow; the wide verdict table stays inside its scroll container.
- Keyboard Enter submits the form successfully. Checked fixture/live journeys produced no uncaught JavaScript exceptions. Expected 401 and simulated 503 responses appeared in the console.

## Remaining acceptance limits

The available Azure configuration is production. No production audit, publication, schema or reference-data writes were performed. The 3 Azure integration tests still require explicit opt-in and a separate test database. Cross-process durability, worker contention and restart behavior against Azure therefore remain unverified; session/repository fakes do not prove those properties.

Live extraction from new/changed pages, LLM agents and real reference evidence are unfinished. The real-source smoke run correctly produced **zero assessed claims** with an explicit warning, rather than inventing results. Raw captures, fallback snapshots and baseline approvals still use file adapters; shared SQL/Blob source storage remains unfinished.

Vercel preview deployment and Vercel-to-Azure networking were not exercised. Local package, entrypoint and read-only Azure checks do not certify deployment. The local environment file remains ignored and uncommitted.
