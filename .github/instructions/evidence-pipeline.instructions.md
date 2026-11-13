---
applyTo: "src/competitive_intelligence/connectors/**/*.py,src/competitive_intelligence/parsers/**/*.py,src/competitive_intelligence/services/**/*.py,src/competitive_intelligence/agents/**/*.py,src/competitive_intelligence/rules/**/*.py,src/competitive_intelligence/llm/**/*.py"
---
# Pipeline, web scraping, LLM agenti a pravidla
- `WebScout` je kód, **ne LLM**; při každém live runu provede bezpečný fetch předem vybraných 2–4 URL dodavatele. Kanonizuj relevantní obsah a ukládej hash (nikoli pouze raw HTML hash), URL, čas a parser verzi.
- Shoda kanonického hashe s validní schválenou baseline: využij uložené claims a zapiš `origin=baseline`, `fetch_was_live=true`. Nová stránka: `origin=live`, Claim Extractor. Neúspěšný fetch: `origin=snapshot`, `fetch_was_live=false`, poslední snapshot a jasné datum.
- Při extrakci musí být `quote` doslovná citace z parsovaného textu (povoleno jen konzistentní whitespace normalizování), včetně source_ref. Žádné překlady/syntézy jako quote.
- Claim Extractor vybírá výhradně `rule.id` z uzavřeného katalogu; neznámé tvrzení `other`. Typované parametry pravidel se validují.
- Price, Green, Customer jsou **LLM agenty používající tool-calling** k volbě povoleného zdroje a nástroje. Nástroje jsou pouze read-only a deterministické; agent nikdy nevypočítává konečný verdict v promptu.
- Pro každé `Evidence` povinně `source_url`, `source_ref`, `captured_at`, `valid_as_of`, `scope`, `source_content_hash` a `limitations`. Ověř právní osobu/IČO, tarif, komoditu, produkt, období a jednotky.
- `Judge` může `rule_verdict` změnit pouze na `unverifiable`, s `judge_changed` a důvodem. Při selhání Judge, které nelze bezpečně zpracovat, nastav příslušný claim na `unverifiable` a loguj chybu.
- Kódová pravidla musí pokrýt `price_rank`, `price_pct_cheaper`, `price_guarantee`, `green_share`, `green_generic`, `carbon_neutral`, `customer_count`, `customer_growth`, `rating`, `soft_trust`, `other`.
- Čtyři verdict hodnoty: `supported`, `partially_supported`, `contradicted`, `unverifiable`. `partially_supported` jen u rozdělitelných tvrzení, nikoli důsledek neúplného vzorku.
- Žádné neomezené crawling, libovolné LLM tool URL, shell příkazy, přímé SQL ani nedeklarované API. Právní riziko oddělit od faktické správnosti.
