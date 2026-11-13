# Phase 3: durable source archives and approved baseline reads

## Implemented behavior

Database-backed live jobs now replace the demonstration's file adapters with [Azure source storage](../src/competitive_intelligence/services/sql_sources.py). The HTTP endpoint still only enqueues work. The separately started worker binds source storage to the claimed audit and keeps the existing lease heartbeat/retry boundary.

Each successful page fetch stores canonical text, raw-content SHA-256, canonical SHA-256, parser version, original capture time and valid-as-of date in the existing `ci.source_captures` table. Canonical text is bounded to **1,000,000 UTF-16 bytes per capture**. Oversized or invalid content fails without truncation. Raw HTML bytes are not retained by this adapter; their hash records the fetched artifact's identity. Raw HTML/PDF archival to Blob remains separate work.

Captures are immutable. Repeating the same save for an audit/source/time/hash/parser tuple is idempotent; another actual fetch retains a separate capture even if its canonical text is unchanged. Failed or incompatible-parser captures are excluded from fallback reads. A new worker context can load the last successful compatible capture from SQL and retains its original capture date, with a new checked-at time and an explicit fallback reason.

The existing `ci.audit_sources` table links each audit/source pair to its exact capture, origin, live-fetch flag, checked-at time, fallback reason and baseline approval where applicable. Retries update that one audit/source association while preserving capture history. Capture and association transactions both verify current unexpired worker ownership. Database errors propagate to bounded worker recovery; database mode does not substitute file snapshots or memory storage.

## Registry and reference ownership

The [curated registry](../src/competitive_intelligence/registry/suppliers.json) remains the exact network URL allowlist. Public supplier IDs remain `cez`, `eon`, and `innogy`. Explicit `database_code` values map those IDs to the existing `cez_prodej`, `eon_energie`, and `innogy_energie` supplier records verified by read-only inspection.

The adapter reads existing active suppliers. It reuses an existing web-page source with the same supplier and exact URL, or registers operational source metadata for that curated URL during capture. It never creates or modifies suppliers, products, page allowlists, approved baselines or reference evidence. Ambiguous source identities and disabled/missing mappings fail clearly. Source registration is serialized on the supplier row; every write first fences the audit lease.

Approved claims are read from the existing `ci.baselines` and `ci.baseline_claims` tables. Only the latest active approval and its human-reviewed claims are eligible. Its capture/source identity, parser and canonical hash must agree. The existing quote-validation pipeline still checks every quotation against the canonical source text. A revoked or changed approval cannot be recorded as the approval used by that collection. New/changed pages continue producing an explicit missing-extraction warning; no baseline or human approval is created automatically.

Schema and initial/reference data remain manually administered outside this repository. No DDL, migrations, seed data, database bootstrap or reference import command was added.

## Confirmed capture constraints

The database owner confirmed these existing constraints on 2026-11-13:

| Constraint | Allowed values |
|---|---|
| `CK_captures_fetch_status` | `ok`, `blocked`, `error`; only `ok` represents success |
| `CK_captures_http` | `http_status` is NULL or between 100 and 599 |
| `CK_captures_validity` | An open date interval is allowed; when both dates exist, `valid_to >= valid_from` |

Successful captures now default to `ok` in settings, the audit repository and the source adapter. They store HTTP 200 and an open validity interval, satisfying the supplied constraints. Fallback reads exclude both `blocked` and `error` captures. No schema changes are needed.

`DATABASE_URL` continues to activate Azure audit persistence. This setting is optional because `ok` is the confirmed default:

```text
SOURCE_CAPTURE_SUCCESS_STATUS=ok
```

If supplied, the setting must equal `ok`; attempts to treat `blocked`, `error` or an unknown status as success fail before opening a database connection. Missing configuration no longer blocks live source processing.

The [environment example](../.env.example) documents the default. Existing credentials were not edited.

```bash
uv run fact-or-watt-worker check-db
uv run fact-or-watt-worker run
```

`check-db` remains read-only and checks all mapped audit/source columns. It does not certify capture-status constraints, write permissions, cross-process locking or source-write transactions.

## Executed verification (2026-11-13)

| Check | Result |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider -rs` (with `RUN_AZURE_INTEGRATION=0`) | **144 passed, 4 skipped** |
| Source adapter/worker regression tests | Immutable/idempotent saves; a recreated context reuses SQL-backed snapshot data; original timestamps; live/baseline/snapshot history; review/revocation checks; corruption/size/parser rejection; missing supplier and ambiguous source rejection; lease fencing; redacted database outages |
| `.venv/Scripts/python.exe -m competitive_intelligence.worker check-db` | Azure connectivity, audit/source columns and server UTC conversion passed; read-only |
| SQL Server statement compilation | Supplier/source serialization uses SQL Server update/hold locks |
| Editor diagnostics | No reported errors |

The offline source tests use a shared session fake, **not SQLite**. They use the confirmed `ok` status, reject invalid success settings, and verify that `blocked`/`error` captures are not used as fallbacks. The fake verifies adapter behavior across recreated contexts but cannot prove actual SQL Server transactions or locks.

An additional opt-in [Azure regression](../tests/test_azure_repository.py) saves an audit capture and loads it from a recreated worker/repository using rollback-only transactions. Like the existing Azure tests, it requires `RUN_AZURE_INTEGRATION=1`, a separate `TEST_DATABASE_URL` and pre-provisioned supplier reference rows. No schema or reference fixtures are created by the test. The owner confirmed that a test database cannot be created. All four Azure tests therefore remain disabled and unverified; production is not used as a test substitute. Development continues with offline tests and read-only database checks.

## Changed files

| Area | Files |
|---|---|
| Source mappings and SQL adapters | [models](../src/competitive_intelligence/models/__init__.py), [SQL source storage](../src/competitive_intelligence/services/sql_sources.py), [storage contracts](../src/competitive_intelligence/services/stores.py), [schema check](../src/competitive_intelligence/db.py) |
| Lease-bound wiring | [repository contract](../src/competitive_intelligence/services/repository.py), [SQL audit repository](../src/competitive_intelligence/services/sql_repository.py), [repository factory](../src/competitive_intelligence/services/factory.py), [Web Scout](../src/competitive_intelligence/services/web_scout.py), [worker](../src/competitive_intelligence/worker.py) |
| Configuration and supplier mapping | [settings](../src/competitive_intelligence/config.py), [registry schema](../src/competitive_intelligence/registry/__init__.py), [supplier registry](../src/competitive_intelligence/registry/suppliers.json), [environment example](../.env.example) |
| Tests | [source persistence](../tests/test_source_persistence.py), [session fake](../tests/source_fakes.py), [Azure acceptance](../tests/test_azure_repository.py) |
| Documentation | [README](../README.md), [implementation plan](IMPLEMENTATION_PLAN.md), [Azure guide](DATABASE_AZURE.md), [deployment guide](DEPLOYMENT_VERCEL.md), [Phase 2 history](PHASE_2.md), this document |

## Remaining work

The capture-status configuration is resolved. Azure source-write/restart/contended-worker acceptance remains unverified because no separate test database is available. This limitation is recorded rather than blocking further offline implementation. Raw HTML/PDF Blob archival and reference-data parsing/import work remain outside this storage increment. Phase 4 still needs literal-quote LLM extraction, bounded evidence tools and live reference evidence; this change does not invent or assess new claims. No Vercel or worker deployment was performed.
