# Co se změnilo oproti původnímu balíčku Copilot

**Zdroj změn:** „Fact or Watt: architektura v2“ z 12. 11. 2026. Níže je rozlišení *převzato z v2* a *doporučení validace*.

| Změna | V předchozím balíčku | Nový stav v2 | Původ |
|---|---|---|---|
| Live Web Scout | ingestion primárně snapshoty, live až později | 2–4 stránky na dodavatele při každém live auditu | Převzato z v2, kap. 3–4 |
| Canonical hash a baseline | hlavně uložený raw snapshot | hash→schválené claims, změna→LLM, výpadek→snapshot | Převzato z v2, kap. 4 |
| LLM evidenční agenty | doménové analyzéry mohly být čistý kód | Price/Green/Customer **LLM se skutečným tool calling** | Převzato z v2, kap. 3, 5 |
| Role kódu | kód počítá ceny a verdict | potvrzeno; Judge jen downgrade | Převzato z v2, kap. 5 |
| Katalog | obecná normalizace | uzavřených 11 pravidel, explicitní IDs | Převzato z v2, kap. 6 |
| Verdikty | `unsupported` | **`contradicted`**; 3 další zůstávají | Převzato z v2, kap. 6–7 |
| Claim contract | raw_text, normalized_type | doslovné `quote`, `rule:{id,params}`, `origin`, `human_reviewed` | Převzato z v2, kap. 7 |
| Technologický stack | Flask + Python + HTML/JS | Flask zachován; ne Next.js/Zod ze zdrojové v2 | Požadavek uživatele |
| Výsledky demo | audit a přehled | publikovaný poslední audit; živý audit chráněn přístupovým kódem | Převzato z v2, kap. 8 |
| MVP priorita | fixture-first, live později | 3 dodavatelé, cena+green, live s fallback, golden set | Převzato z v2, kap. 13 |
| OPM vs zákazníci | správné rozlišení v původní validaci | **oprava chybného `customer_count` ve v2** | Doporučení validace |
| Nejlevnější z pěti | původní validace vyžadovala scope | oprava `partially_supported` pro neúplný celotržní superlativ | Doporučení validace |
| Perzistence/nasazení | SQLite + worker s hostem | potvrzeno; Vercel pro Flask pouze s externí persistence/queue | Požadavek stacku + validace |

## Seznam aktualizovaných podkladů
- `.github/copilot-instructions.md` — nové závazné role, příkazy, pravidla a priority.
- `.github/instructions/*.instructions.md` — cílená pravidla Flasku, evidence tool calling, frontendu a testů.
- `docs/ARCHITECTURE.md` — aktualizovaný diagram a v2 workflow.
- `docs/API_CONTRACTS.md` — `quote`, `origin`, `rule.id`, `judge_changed`, nové HTTP API.
- `docs/RULE_CATALOG.md` — nově samostatná explicitní pravidla (11 IDs).
- `docs/DATA_SOURCES.md` — živé/dávkové/backup režimy a rozdělení API/scraping/agent.
- `docs/ARCHITECTURE_VALIDATION.md` — revize v2 (včetně P0/P1 oprav).
- `docs/IMPLEMENTATION_PLAN.md` — plán podle priorit a termínů v2, Flask implementace.

## Rozsah a otevřené otázky
- Dodaný dokument uvádí Next.js a Vercel; toto jsme **záměrně nepřevzali**, protože zadavatel žádá Flask/Python/HTML/JS.
- V rámci technické revize jsme **neověřovali dostupnost konkrétních webů pěti dodavatelů ani přístup ke každému referenčnímu exportu**. Adresy a ceníky je nutné ověřit při implementaci.
- Přesný proces schvalování baseline, hostující platforma, rozdělení dodavatelů a právní závěry o EmpCo jsou konfigurační/projektová rozhodnutí, nikoli empiricky zjištěné skutečnosti.
