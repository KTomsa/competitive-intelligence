# Implementační plán v2 — zadání pro Copilot po fázích

Fáze se realizují postupně. **Každá fáze musí být spustitelná a otestovaná samostatně.** Rozsah a harmonogram vycházejí z kap. 11–14 dodané architektury v2; doplněná implementační technika je adaptační návrh pro Flask.

## Fáze 1 — celé UI/API, fixture pipeline a úložiště
**Cíl:** webová stránka zobrazí ukázkový poslední audit; autorizovaný uživatel spustí fixture audit; worker zpracuje celý průchod bez externího LLM a webu.
- Flask `create_app`, blueprints, Pydantic Claims/Evidence/Verdicts/Audit/Source, SQL alchemy modely a napojení na AZURE DB a transakční job fronta.
- `/`, `/audits/<id>`, `POST /api/v1/audits`, `GET status/results/latest`, publikace sanitized results.
- Worker CLI a fake/deterministic agent adapters; ukázkové zdroje v `data/fixtures` označené jako fiktivní.
- Bootstrap/HTML/vanilla JS: výběr, polling log, výsledky, legendy/zdroje, omezení.
- `.env.example`, `.gitignore`, testy kontraktů a API.
**Akceptace:** bez klíčů a s vypnutou sítí funguje fixture audit, výsledek přežije restart webu, `pytest` projde.

**Zadání Copilotu:** „Implementuj pouze Fázi 1 z docs/IMPLEMENTATION_PLAN.md. Zachovej API_CONTRACTS v2, Flask a Pydantic. Nepřidávej Next.js/TypeScript/nové služby. Napiš testy a ukaž příkazy spuštění.“

## Fáze 2 — Web Scout, baseline, snapshot a provenance
- Konfigurovaný registry 3 dodavatelů (2–4 HTTPS marketingové URL/dodavatel), standardní HTTP fetch s timeouty/robots/allowlist.
- HTML-to-canonical-text parser, verze parseru, SHA-256, immutable snapshots, schválené baseline claims.
- Větev unchanged / changed / blocked; UI jednoznačně odlišuje `live`, `baseline`, `snapshot` a datová stáří.
- Testy hash stability, neodpovídající stránky, chybějící baseline, changed quote; bez masového crawlování.
**Akceptace:** jedna reálná veřejná stránka → live fetch; při opakování beze změny reuse baseline; při simulovaném výpadku snapshot s varováním.

**Zadání:** „Implementuj Fázi 2: Web Scout a třístupňovou fallback logiku. Pracuj jen na schválených URLs z config, bez obcházení robots. Přidej testy všech tří větví a aktualizuj UI.“

## Fáze 3 — Claim Extractor + skuteční Price/Green agenti s tool calling
- Python LLM provider adapter (např. Claude API) s mock backendem a Pydantic validací; max 2 opravná volání u nevalidního JSON.
- Extractor přesně cituje zdroj, vybírá jeden `rule.id` + typované `params`, nové tvrzení má `human_reviewed=false`.
- `ToolRegistry`: read-only zdroje `get_price_offers`, `calculate_annual_cost`, `get_product_terms`, `get_fuel_mix`, `get_guarantees_of_origin`; omezení počtu/tool timeouts.
- **Price a Green agenty musí skutečně vyvolat tool-calls** (nikoli pouze napsat doporučení, který tool použít). Kód vypočítá verdict dle RULE_CATALOG.
- Judge pouze downgrade, Strategist pouze doložené výstupy; evidence agenti mohou běžet paralelně, až když sekvenční běh spolehlivě funguje.
**Akceptace:** u jednoho dodavatele existuje celý live audit s doloženými nástroji/důkazy, systém nezaměňuje snapshot s živými daty.

**Zadání:** „Implementuj Fázi 3 dle docs/ARCHITECTURE.md a RULE_CATALOG.md. LLM agenty jen vybírají a volají typované tools; výpočty a verdikty pouze kód. Předveď v testu, že Judge neumí verdict vylepšit.“

## Fáze 4 — referenční data + golden set + publikace
- Kurátorované ceníky, mix a evidence pro MVP tři dodavatele; metadata období a revize parseru/importu.
- Golden set **20–30** doslovných tvrzení s anotací `rule.id`, očekávaný verdict, zdroj evidence a confidence v lidském hodnocení.
- Měř a popiš zvlášť: nalezená tvrzení (precision/recall), správné rule ID, shodu verdictů; uchovej počet testovaných a nerozhodnutelných.
- Publikace posledního **úspěšného a očištěného** auditu; veřejný GET read-only a kódově chráněné živé spuštění, limity kreditů.
**Akceptace:** připravený demo audit 3 dodavatelů, dokumentované zdroje, doložitelné metriky golden setu (bez předem vymyšlených hodnot).

## Fáze 5 — rozšíření (pokud zbude čas)
- Dodat Customer agenta s OTE OPM / relevantním ERÚ kontextem, poté rozšířit na 5 dodavatelů.
- Volitelně změnové signály oproti schválené baseline a export briefingu.
- Nepřidávat tyto funkce, pokud není stabilní live průchod pro 3 firmy a 2 evidence agenty.

## Nasazení a demo (Flask stack)
- V první řadě Python hosting s trvalým diskem + worker a Flask/Gunicorn; vyzkoušej restart a obnovu jobů.
- Pro 10min obhajobu: 1 min problém/KIT, 2 min PESTEL/Porter/konkurence, 4 min demo, 2 min architektura/golden set/omezení, 1 min KIQ5. Při live demo spusť jednoho dodavatele a ostatní ukaž ze skutečně uloženého auditu.

## Harmonogram z podkladu v2 (orientační)
| Datum 2026 | Požadovaný milník podle podkladu |
|---|---|
| 12. 11. | Schémata, repozitář, základní pipeline a test live webů |
| 13. 11. | Konzultace, první agenti na jednom dodavateli |
| 14.–15. 11. | Tři až pět dodavatelů, základ analytické části |
| 16. 11. | Celý živý průchod pro 3 firmy; rozhodnutí o rozsahu |
| 17. 11. | Golden set, opravy, uzavření funkcí |
| 18. 11. | Report, prezentace, finální nasazení a záloha |
| 19. 11. 09:00 | Odevzdání reportu, prezentace a odkazu |
