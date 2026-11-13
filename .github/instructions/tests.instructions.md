---
applyTo: "tests/**/*.py"
---
# Testy a evaluace
- `pytest`, Flask test client, SQLite v temporary directory, bez skutečných API callů/LLM; všechny volání mockované.
- Web Scout: nezměněná stránka → bez LLM, schválená baseline; změněná stránka → Claim Extractor, `human_reviewed=false`; timeout/robots blokace → snapshot s označením a stářím; bez snapshotu → explicitní source error.
- Claim Extractor: doslovná citace musí existovat na stránce; jiné/neznámé `rule.id` → `other`; nevalidní LLM JSON nejvýš dva retrysy.
- Price agent: musí existovat skutečná tool-call invokace, správné argumenty a deterministický výsledek; agent nesmí vygenerovat vlastní čísla.
- `price_rank`: první v 5 dodavatelích nepotvrzuje „nejlevnější v ČR“, validní levnější srovnatelná nabídka může výrok `contradicted`.
- OPM != zákazníci; národní zbytkový mix != mix produktu; absence sankcí nezakládá `supported`.
- Judge: může pouze downgradovat `supported/partially_supported/contradicted` → `unverifiable`, nikdy změnit na lepší výsledek; `judge_changed=true` a důvod.
- `origin`/čas/validita/evidence traceability musí projít i fallbackem; chybějící evidence URL/valid_as_of = chyba nebo `unverifiable`.
- Testuj ochranu proti prompt injection, neautorizovanému POST, rate limit, SSRF a XSS v UI.
- Evaluace: ručně označený golden set 20–30 claimů, měř precision/recall extrakce, přesnost rule_id a verdict accuracy; reportuj velikost vzorku a nevyhodnocená data.
