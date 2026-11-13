---
applyTo: "src/competitive_intelligence/connectors/**/*.py,src/competitive_intelligence/parsers/**/*.py,src/competitive_intelligence/services/**/*.py,src/competitive_intelligence/agents/**/*.py,src/competitive_intelligence/rules/**/*.py,src/competitive_intelligence/llm/**/*.py,src/competitive_intelligence/tools/**/*.py"
---
# Agent pipeline and evidence persistence

- Continue to follow docs/RULE_CATALOG.md, DATA_SOURCES.md and API_CONTRACTS.md; these v2 methodological invariants still apply.
- Source records, baseline approvals, snapshots, claims, evidence, verdicts and tool-call audit trails must be persisted through Azure-backed repository abstractions; curated non-sensitive baseline fixtures may be versioned in Git.
- Do not let agents query the DB directly or construct SQL. Bounded typed read-only tools call service methods that load evidence through repositories and perform deterministic calculations.
- Separate ingestion, validation, extraction, verification and publication transactions; an interrupted job must not publish incomplete results.
- Preserve atomic job leases/checkpoints, source hashes, origin (live/baseline/snapshot) and evidence valid-as-of timestamps across retries.
- Reference files are versioned snapshots with provenance; Azure Blob Storage can hold large raw artifacts while Azure SQL holds metadata, hashes and URIs.
- Deterministic verdict engine; Judge only downgrades to unverifiable. No invented evidence or verdict improvements. Limit tool invocations and LLM costs.
