# Uzavřený katalog ověřovacích pravidel — Fact or Watt v2

Každý `Claim.rule.id` musí být jednou z níže uvedených hodnot. `Claim Extractor` nesmí generovat vlastní identifikátory. `params` se validují podle Pydantic discriminated union modelu. Výpočet a výsledný `rule_verdict` patří **kódu**, nikoli LLM.

**Verdikty:** `supported` (relevantní podklady potvrzují), `partially_supported` (pouze doložené oddělitelné části), `contradicted` (validní proti-důkaz), `unverifiable` (nedostatečná nebo nesrovnatelná data). Odděleně `possible_regulatory_risk` + `risk_reason`, případně `review_required`; toto **není** právní závěr.

| Rule ID | Typický výrok | Deterministické hodnocení a hranice |
|---|---|---|
| `price_rank` | „Nejlevnější elektřina v ČR“ | Výpočet ročního nákladu u stejných profilů/období; validní levnější konkurent → `contradicted`; pořadí 1. z 3–5 **nepotvrdí** celotržní výrok → `unverifiable`; při explicitním tvrzení „v našem vzorku“ lze `supported`. |
| `price_pct_cheaper` | „O 15 % levnější než X“ | Potřeba přesného výchozího produktu, metriky, data a profilu. Bez referenčního základu → `unverifiable`; měřené hodnoty se tolerují jen v definované toleranci. |
| `price_guarantee` | „Fixace do roku 2028“ | Potvrdit produkt i fixovanou část ceny a podmínky ze stejného platného ceníku/smlouvy; nejasné výluky → `unverifiable` + kontrola člověkem. |
| `green_share` | „100 % zelená elektřina“ | Doložit konkrétní produkt, období a relevantní mix/záruky původu; samotný národní zbytkový mix → `unverifiable`. |
| `green_generic` | „Zelená, šetrná k přírodě“ | Vágní rámec, bez konkrétní ověřitelné metriky většinou `unverifiable` + možné regulatorní riziko; nelze vydávat `contradicted` pouze kvůli nedostupnosti certifikátu. |
| `carbon_neutral` | „Uhlíkově neutrální produkt“ | Posoudit, zda je uvedená metodika doložená a zda se výrok opírá o offsety; při neúplných podkladech `unverifiable`, oddělený signál možného právního rizika. |
| `customer_count` | „Máme milion zákazníků“ | OTE OPM samo nestačí na počet unikátních zákazníků; použij odpovídající zákaznickou metriku nebo `unverifiable`. |
| `customer_growth` | „Rosteme“ / „Nejrychleji rosteme“ | Na OPM lze ověřovat **růst počtu OPM**, pokud to tvrzení výslovně znamená; jinak nepřevádět OPM na zákazníky. Celotržní superlativ vyžaduje příslušný scope. |
| `rating` | „Máme hodnocení 4,8 na Google“ | Zaznamenat platformu, datum, region/provozovnu, průměr a počet hodnocení; nekonstruovat reprezentativní spokojenost. |
| `soft_trust` | „Férový a spolehlivý“ | Bez testovatelné operacionalizace `unverifiable`. Sankce ERÚ pouze jako kontext, nikoli automatický důkaz proti sloganu nebo jeho potvrzení. |
| `other` | Cokoli jiného | Bez vytvořeného a otestovaného pravidla `unverifiable`. |

## Srovnatelný cenový profil pro pilot
- Komodita: **elektřina**; sazba `D02d`, jistič `3×25 A`, běžný zákazník, geografická oblast a dostupná nabídka; **zaznamenat region a konkrétní produkt**.
- Referenční spotřeba: **2 MWh a 4 MWh za rok** (parametry auditu, nikoli zamlčená konstanta).
- Cíl: standardní nabídka pro nového zákazníka s fixací přibližně 12 měsíců; pokud odpovídající produkt není dostupný, nesrovnávej nesrovnatelné tarify.
- Pro **obchodní složku** vč. DPH: `annual_trade_cost = 12 × monthly_supplier_fee + annual_mwh × supplier_energy_price_mwh` (+ jiné obchodní poplatky/slevy, pokud relevantní). `Decimal` a explicitní měnové jednotky.
- Reklama na **celkovou cenu** vyžaduje kompletní cenovou metodiku včetně regulovaných složek; obchodní složku nelze vydávat za úplný roční účet.
- Cenové údaje validuj pro datum tvrzení i datum porovnání, uveď čas platnosti tarifu a datum získání. Pokud období neodpovídají, `unverifiable`.

## Další závazná pravidla
1. Chybějící podklad ≠ důkaz nepravdivosti. Jeden validní protipříklad univerzálního výroku ≠ automaticky protipříklad produktu či období s jiným scopingem.
2. `partially_supported` vyžaduje strukturu `subclaims`, která obsahuje konkrétně ověřené části; běžný nerozložitelný superlativ nemá tuto možnost.
3. Kódové pravidlo vrací `(rule_verdict, evidence_ids, limitations, metrics, rule_version)`. `Judge` může pouze snížit na `unverifiable` při prokazatelném nesouladu.
4. Použití snapshotu není samo o sobě důvod ke `contradicted`, ale stáří/reference mismatch může vyvolat `unverifiable`.
5. Při relevantní právní obavě `possible_regulatory_risk=true`, `review_required=true`, `risk_reason` s odkazem na posuzované znění; bez právního verdiktu.
