---
applyTo: "src/competitive_intelligence/**/*.py"
---
# Backend Python/Flask — architektura v2
- Implementuj `create_app(config=None)`, Blueprints, Pydantic v2 schémata, oddělený worker a transakční persistenci; nikdy nesvazuj live audit se životností HTTP requestu.
- Web Scout i agentní pipeline musí být injektovatelné/mokovatelné, aby režim fixture nevyžadoval síť ani klíč.
- Pro live audity validuj serverovou autorizaci; publikovaný `GET latest` pouze čte poslední výsledek.
- Na každý audit ulož per-source `origin`, `captured_at`, `valid_as_of`, URL, `canonical_hash`, `baseline_hash`, `fallback_reason`, human_reviewed a event timeline.
- Evidence agenti volají pouze schválené read-only Python nástroje s validovanými parametry; výpočet pravidla mimo LLM.
- Při chybě zdroje pokračuj s transparentním snapshotem, pokud existuje; při absenci snapshotu vypiš chybu, nevyráběj pozitivní verdict.
- SQLite v lokálním MVP; ve funkčním serverless nasazení nepovažuj lokální DB nebo `/tmp` za trvalou frontu.
- Obchodní výpočty používají `Decimal`, měny/DPH/období explicitně. Všechny časové značky UTC timezone-aware.
- Error handler vrací `code`, `message`, bez tracebacků. API endpointy krátké a idempotentní tam, kde to dává smysl.
