# Release candidate 1.0.0rc1

Implemented Phase 5 application hardening, exact-denominator evaluation, reviewed product binding, bounded preview checks, worker shutdown, CI and operating instructions. Fixed unsupported price ranks and out-of-audit percentage profiles. Prepared 24 unannotated quotations from existing supplier captures. Human annotation, real reference curation, isolated Azure acceptance and Vercel deployment remain pending; see the [release record](PHASE_5.md).

Follow-up: fixed the launch form reloading without an audit when submitted before the latest-publication request finished. Submission now waits only for its own initialization. Added immediate, credential-free worker startup/readiness/claim messages and a [worker logging regression](../tests/test_persistence.py). Reproduced the browser failure and verified the corrected behavior with the latest request held pending; see the [follow-up record](PHASE_5.md#launch-button-and-worker-feedback-follow-up).

Live-runtime follow-up (2026-11-14): corrected SQL Server BIT filters in [source storage](../src/competitive_intelligence/services/sql_sources.py), aligned durable capture timestamps with the existing `datetime2(3)` columns in [Web Scout](../src/competitive_intelligence/services/web_scout.py), and reserved a heartbeat connection by bounding durable intake to two tasks in [orchestration](../src/competitive_intelligence/services/orchestrator.py). [Source regressions](../tests/test_source_persistence.py) reproduce the SQL syntax and timestamp-identity failures. Full offline verification: **297 passed, 5 skipped**; wheel/source archive rebuilt. The [combined VS Code task](../.vscode/tasks.json) starts both required processes. See the [live runtime record](PHASE_5.md#live-runtime-corrections-2026-11-14) for the operational outcome.

## Changed files

- Configuration/build: [.env.example](../.env.example), [pyproject.toml](../pyproject.toml), [uv.lock](../uv.lock), [CI workflow](../.github/workflows/ci.yml).
- Application/security: [app factory](../src/competitive_intelligence/app.py), [settings](../src/competitive_intelligence/config.py), [API](../src/competitive_intelligence/web/api.py), [limiter](../src/competitive_intelligence/web/security.py).
- Durable admission: [limits](../src/competitive_intelligence/services/admission.py), [repository contract/demo](../src/competitive_intelligence/services/repository.py), [Azure repository](../src/competitive_intelligence/services/sql_repository.py).
- Verification/provenance: [audit contracts](../src/competitive_intelligence/schemas/audit.py), [claim contracts](../src/competitive_intelligence/schemas/claim.py), [reviewed imports](../src/competitive_intelligence/services/reference_bundle.py), [reference records](../src/competitive_intelligence/tools/reference.py), [orchestrator](../src/competitive_intelligence/services/orchestrator.py), [evidence adapter](../src/competitive_intelligence/agents/fixture_evidence.py), [rules](../src/competitive_intelligence/rules/engine.py).
- Operations: [worker](../src/competitive_intelligence/worker.py), [evaluation command](../src/competitive_intelligence/evaluation.py), [preview checks](../src/competitive_intelligence/release.py).
- Browser: [JavaScript](../src/competitive_intelligence/static/js/app.js), [audit template](../src/competitive_intelligence/templates/audit.html).
- Evaluation preparation: [annotation instructions](../eval/README.md), [24 review candidates](../eval/review-candidates.json).
- Tests: [environment isolation](../tests/conftest.py), [API](../tests/test_api.py), [Azure admission acceptance](../tests/test_azure_repository.py), [evaluation](../tests/test_evaluation.py), [hardening](../tests/test_hardening.py), [product bindings](../tests/test_product_bindings.py), [preview checks](../tests/test_release.py), [three-supplier journey/shutdown](../tests/test_release_journey.py), [rule-scope regressions](../tests/test_rules.py).
- Documentation: [README](../README.md), [API contracts](API_CONTRACTS.md), [deployment](DEPLOYMENT_VERCEL.md), [implementation plan](IMPLEMENTATION_PLAN.md), [Phase 4 checkpoint](PHASE_4.md), [Phase 5 runbook](PHASE_5.md), this changelog.

## Executed verification

- Final offline pytest: **293 passed, 5 skipped**. No separate Azure write-test target is available.
- Installed fixture CLI: completed, nine traceable fictional claims.
- `uv lock --offline`, `uv sync --locked --offline`, `uv build --offline`: passed.
- Built archive inspection: expected modules/assets/data/commands present; secrets and pytest runtime dependency absent.
- Browser: fictional launch, results and reload passed with nine claims/eight unique events/100% progress.
- Editor diagnostics and `git diff --check`: no errors.
- Actual cloud deployment, Linux CI, human golden-set evaluation and Azure committed restart/contention: **not verified**.
