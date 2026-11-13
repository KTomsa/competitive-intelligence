# Phase 4: literal extraction and bounded evidence agents

## Implemented pipeline

The live worker can now use a model to extract claims from new/changed pages or dated fallback text. Matching approved baselines remain eligible without re-extraction. The fixture pipeline stays offline.

The [extractor](../src/competitive_intelligence/agents/live.py) requests structured candidates, then code validates each quotation against the captured canonical text, validates the closed rule and its parameters, and assigns the claim ID, source identity, normalized character locator, capture date and origin. New claims always have `human_reviewed=false` and no invented product ID. Unquoted, malformed, duplicate or incorrectly classified candidates are discarded with explicit warnings. Only the first 16000 characters/page are submitted; truncation is reported.

Price and Green agents use a closed [function registry](../src/competitive_intelligence/tools/registry.py). Every argument is Pydantic-validated. Supplier scope, profile, source date and product identity are checked by code. Functions cannot fetch arbitrary URLs, construct SQL, execute Python/shell text, change data or call another function by an arbitrary name. Price tools expose reviewed offers, deterministic `Decimal` annual trade-cost calculations/breakdowns, and product terms. Green tools expose fuel mix and product-linked guarantees of origin.

Tools return explicit unavailable results when records are missing. An offer must first be retrieved before it can be used for a cost calculation. The final rule sees only records actually selected through successful tool calls. Own-supplier offers must match the claim's verified product ID. Newly extracted price claims without that binding remain `unverifiable` with human review required. Green support requires a requested certificate for the same product and the same validity period; an unchecked Boolean in a mix record cannot substitute for that evidence.

The existing deterministic rule engine still computes all numbers and verdicts. Model prose is never a verdict input. Judge behavior remains downgrade-only, and the briefing continues citing claim IDs. Completion does not publish an audit.

## Provider configuration

The project is configured for the owner's exact model choice, `gemini-3.5-flash-lite`, through the [native Gemini adapter](../src/competitive_intelligence/llm/gemini.py). It uses the existing `LLM_API_KEY`; no replacement key is required. Local configuration and the environment example select:

```text
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash-lite
LLM_API_KEY=<existing key>
```

The native adapter uses Google's fixed HTTPS `generateContent` endpoint and header authentication (`x-goog-api-key`), keeping the key out of URLs. It sends structured JSON schemas for extraction and only the declared function schemas for evidence work. Function responses retain the exact call names/IDs, and model content parts (including opaque thought signatures) are replayed unchanged during continuation. Thought text is excluded from extracted response text. Signed parts remain transient conversation state and are never stored in agent traces.

The [OpenAI Responses adapter](../src/competitive_intelligence/llm/provider.py) remains optional through explicit OpenAI configuration. Both adapters reject redirects and turn incomplete/refused/malformed responses, timeouts and oversized bodies into safe errors. Raw bodies, prompts and authentication headers are not logged. No key is forwarded to a provider inferred from its format. Inspect non-secret configuration without connecting to any service:

```bash
uv run fact-or-watt-worker check-llm-config
```

The Gemini adapter follows the official [model documentation](https://aistudio.google.com/docs/models/gemini-3.5-flash-lite) and [generateContent API reference](https://aistudio.google.com/api-reference/generate-content/generate-content). The optional OpenAI adapter follows its [function-calling flow](https://developers.openai.com/api/docs/guides/function-calling) and [structured-output format](https://developers.openai.com/api/docs/guides/structured-outputs). The app independently validates quotations, scope, parameters and provider failure states.

## Budgets and failure handling

| Setting | Default | Bound |
|---|---|---|
| `LLM_MAX_REQUESTS` | 32 | Per audit delivery attempt, shared across extraction/evidence roles |
| `LLM_MAX_INPUT_CHARS` | 240000 | Total serialized request characters, including prompts/schemas/tool history |
| `LLM_MAX_OUTPUT_TOKENS` | 2000 | Maximum output tokens per model request |
| `LLM_MAX_CLAIMS` | 20 | Maximum live claims retained for assessment |
| `LLM_TIMEOUT_SECONDS` | 30 | HTTP timeout |
| Tool calls | 4 | Per claim |
| Evidence model rounds | 3 | Per claim |
| Provider response | 2 MB | Maximum buffered response body |

Input budgets are character bounds, not a billing estimate. With the default request/output caps, output is bounded to at most 64000 tokens per delivery attempt. A worker retry receives a fresh per-attempt budget; stored worker attempt limits still apply. Account-side spending limits remain independent.

Provider failures or exhausted limits produce warnings and abstention rather than fabricated evidence. Invalid tool requests return safe rejection codes. Lease/database errors propagate to worker recovery. The durable repository verifies its lease before a paid request and before a tool execution by recording the starting trace; loss of ownership stops processing.

## Private trace persistence

An [agent step](../src/competitive_intelligence/llm/runtime.py) contains role, model/tool name, status, SHA-256 input/output digests and token counts. It has no fields for raw prompts, arguments, provider traces, response bodies or keys.

The in-memory repository keeps steps privately. The Azure repository writes them into existing `ci.audit_events.metadata_json` in separately fenced transactions, so interrupted attempts retain their trace independently of result completion/publication. Status responses contain the generic event message, not private metadata. Public result DTOs do not include agent-step payloads. No new database tables, migrations, schema initialization or reference-data writes were added. Normalized `agent_runs`/`tool_calls` integration is not claimed.

## Reviewed reference imports

The implemented reference interface is a bounded, read-only [reviewed JSON bundle](../src/competitive_intelligence/services/reference_bundle.py):

```text
REFERENCE_BUNDLE_PATH=<path to a manually reviewed reference bundle>
```

Its versioned schema requires reviewer identity/time, a common D02d distribution region and VAT-inclusive comparison scope. Each offer, product term, mix or guarantee needs its original source URL/locator, artifact SHA-256, capture date, valid-as-of date and explicit validity interval. Credential-bearing URLs, missing hashes, invalid intervals, duplicate offer IDs and invalid price/percentage values are rejected. Records outside the claim date or available only after that date are excluded. Original artifact hashes are preserved; the fixture loader's synthetic row hashes are not used for these imports. The maximum input file size is 2 MB.

Schema inspection is available directly from the typed model:

```bash
uv run python -c 'import json; from competitive_intelligence.services.reference_bundle import ReviewedReferenceBundle; print(json.dumps(ReviewedReferenceBundle.model_json_schema(), indent=2))'
```

There is no production reference bundle supplied by this change. The implementation/tests do not invent reference prices or certificates. Real reference data must be curated before it can support a live verdict. Automatic product linking from page names is also not implemented; approved baseline product IDs remain the current verified binding mechanism.

## Executed validation (2026-11-13)

| Check | Result |
|---|---|
| Full pytest suite, with `RUN_AZURE_INTEGRATION=0` | **201 passed, 4 skipped** |
| End-to-end mock-model audit | Literal unreviewed claim, real function-call protocol, tool scope checks, deterministic result, private traces and explicit publication boundary |
| Price scope | Unbound claim cannot inherit an unrelated own tariff; a known-product comparison uses Python's verdict despite contrary model prose |
| Green evidence | A requested matching-product/matching-period certificate is required for support; absent/mismatched certificates cause abstention |
| Failure/security boundaries | Refusals, incomplete/malformed provider output, redirects, unknown tools, URL/SQL argument injection, wrong supplier/product/profile/date, unseen offers, input/request/tool budgets and lease loss |
| Reference imports | Provenance, validity, review, VAT scope, credential-bearing URL rejection, original hash preservation and expiration |
| `check-llm-config` | Gemini enabled, exact model `gemini-3.5-flash-lite`, key present; no key disclosure |
| Gemini live smoke | Structured extraction and native function-call/result continuation passed in 3 requests (877 input tokens, 109 output/thought tokens); no Azure writes |
| Gemini protocol tests | Header-only credentials, schema conversion, thought filtering, exact signed-content replay, parallel result grouping, native/missing call IDs, redirect/error redaction, invalid history/model rejection, and app/worker wiring |
| Package build | Source distribution and wheel built |
| Editor diagnostics | No reported errors |

Offline model journeys use mock HTTP responses/scripted models and clearly synthetic reference records. The additional live smoke used the existing key and a short synthetic Czech claim plus an unavailable-reference tool response. This verifies basic access/structured output/tool continuation for the exact requested model; it does not measure real supplier extraction accuracy or certify a full production audit. All four Azure write integration tests remain skipped because the owner cannot create a test database. Production was not used as a write-test substitute.

A read-only attempt to inspect new reference/agent table mappings was rejected by Azure's firewall for this machine's current IP. No firewall rules or database permissions were changed, and no unverified column mappings were added. Database reference-table integration remains pending; existing verified audit-event metadata is used for private traces. Raw artifact Blob storage, golden-set evaluation, full live-audit acceptance and Vercel deployment remain unfinished.

## Changed files

| Area | Files |
|---|---|
| Model/runtime | [Gemini adapter](../src/competitive_intelligence/llm/gemini.py), [provider interface/OpenAI adapter](../src/competitive_intelligence/llm/provider.py), [budget/trace runtime](../src/competitive_intelligence/llm/runtime.py), [package](../src/competitive_intelligence/llm/__init__.py), [settings](../src/competitive_intelligence/config.py) |
| Agents and references | [live agents](../src/competitive_intelligence/agents/live.py), [tool registry](../src/competitive_intelligence/tools/registry.py), [reference records](../src/competitive_intelligence/tools/reference.py), [reviewed imports](../src/competitive_intelligence/services/reference_bundle.py) |
| App/worker/persistence | [orchestrator](../src/competitive_intelligence/services/orchestrator.py), [repository](../src/competitive_intelligence/services/repository.py), [SQL repository](../src/competitive_intelligence/services/sql_repository.py), [worker](../src/competitive_intelligence/worker.py), [app factory](../src/competitive_intelligence/app.py) |
| Validation | [Gemini protocol tests](../tests/test_gemini.py), [model/tool tests](../tests/test_live_agents.py), [persistence tests](../tests/test_persistence.py), [environment isolation](../tests/conftest.py) |
| Operations | [environment example](../.env.example), [README](../README.md), [plan](IMPLEMENTATION_PLAN.md), [Phase 3](PHASE_3.md), [deployment guide](DEPLOYMENT_VERCEL.md), this document |
