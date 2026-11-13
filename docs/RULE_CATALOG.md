# Katalog ověřovacích pravidel

Každé tvrzení má právě jedno `rule_id` z tohoto seznamu. Výpočet a verdikt patří kódu (`rules.py`), ne jazykovému modelu.

**Verdikty:** `supported` (podloženo), `partially_supported` (částečně; vždy s rozpisem dílčích tvrzení), `contradicted` (v rozporu s daty), `unverifiable` (nelze ověřit). Odděleně příznak `possible_regulatory_risk` s důvodem; není to právní závěr.

| Rule ID | Typický výrok | Zdroj | Jak se rozhoduje |
|---|---|---|---|
| `price_favorable` | „Máme výhodné ceny“, „Ušetříte“ | ERÚ + ceníky | Srovnatelné nabídky nejvýše na horní hranici pásma → `supported`; nález uvádí, zda jsou pod pásmem (nejlevnější čtvrtina trhu). U tvrzení o úspoře zákazníka jen `partially_supported`. Nabídka nad pásmem → `unverifiable` s nálezem, ne rozpor. |
| `price_rank` | „Vždy nejlepší cena“, „Garance nejnižší ceny“ | ceníky dodavatelů | Levnější srovnatelná nabídka s nižším ročním nákladem ve všech profilech spotřeby → `contradicted`. Nižší jen cena za MWh nebo chybí stálý plat → `unverifiable` s nálezem. Prvenství ve vzorku → `unverifiable`. Závazek dorovnání ceny → vždy `unverifiable`, nález uvádí levnější nabídku. Bez ceníku dodavatele → `unverifiable`. |
| `price_pct_cheaper` | „O 7 % levnější než X“ | ceníky dodavatelů | Roční náklad obchodní části obou dodavatelů ve všech profilech spotřeby. Platí všude → `supported`, nikde → `contradicted`, jen někde nebo bez stálého platu či základu → `unverifiable`. |
| `price_guarantee` | „Garance ceny na 3 roky“ | ceník + ERÚ | Produkt s danou fixací existuje a cena nepřesahuje pásmo → `supported`; existuje, ale cenu nelze srovnat → `partially_supported`. |
| `market_size` | „Největší dodavatel“ | OTE elektřina + plyn | Pořadí podle počtu OPM. Jen elektřina: 1. místo → `supported`, jinak `contradicted`. Elektřina i plyn: první v obou → `supported`; první jen v elektřině nebo v součtu → `partially_supported` s rozpisem; jinak `contradicted`. Přívlastek, který tvrzení nedefinuje („český“, „alternativní“), se posuzuje podle výkladů týmu (všichni dodavatelé; dodavatelé mimo tradiční skupiny): platí při obou → `supported`, jen při užším → `partially_supported`, při žádném → `contradicted`. Bez definice v `suppliers.json` → `unverifiable`. |
| `customer_count` | „1,5 milionu zákazníků“ | OTE elektřina + plyn | Tvrzený počet nejvýše roven součtu OPM elektřiny a plynu → `partially_supported` (řádově sedí). Tvrzený počet o víc než 10 % nad součtem → `contradicted`. Tvrzení přes více zemí → `unverifiable`, nález uvádí český podíl. Výslovně o odběrných místech → porovnání s OPM elektřiny. |
| `customer_growth` | „Rosteme“ | OTE | Počet OPM mezi prvním a posledním zveřejněným měsícem roku: růst → `supported`, jinak `contradicted`. Superlativ („nejrychleji“) → `unverifiable`. |
| `green_share` | „100 % zelená elektřina“, „krytá zárukami původu“ | podklad k produktu | Výslovné prohlášení dodavatele vázané na produkt, ověřené na živé stránce → `supported`. Bez produktového podkladu → `unverifiable` + příznak rizika. |
| `carbon_neutral` | „Emise kompenzujeme“ | – | `unverifiable` + příznak rizika s důvodem. |
| `green_generic` | „Šetrná k přírodě“ | – | `unverifiable` + příznak rizika. |
| `soft_trust` | „Férový a spolehlivý“ | ERÚ – pravomocná rozhodnutí | Vždy `unverifiable`. Nález uvádí počet pravomocných rozhodnutí o přestupcích a kolik se jich týká obchodních praktik nebo nepravdivých informací; zastavená řízení a spory se nepočítají. |
| `other` | cokoli jiného | – | `unverifiable`. |

## Srovnávací profil

- Elektřina, sazba `D02d`, distribuční území PRE, roční spotřeba 2 a 4 MWh (parametry v `suppliers.json`).
- Cena komodity se srovnává bez DPH; roční náklad obchodní části s DPH: `12 × stálý plat + MWh × cena`.
- Pásmo ERÚ se bere pro měsíc, ve kterém nabídka platí (živě potvrzená nabídka = aktuální měsíc).
- Srovnávají se jen nabídky se stejnou sazbou, územím a délkou fixace. Produkty s postupně se měnící cenou a nabídky, které živý ceník už nepotvrzuje, se vynechávají.

## Jak číst pásmo ERÚ

Podle metodiky ERÚ odpovídá dolní hranice pásma 25. percentilu a horní hranice přibližně mediánu fixovaných nabídek na trhu. Pod pásmem tedy znamená „mezi nejlevnější čtvrtinou nabídek“, nad pásmem „dražší než přibližně polovina nabídek“. Pásmo je cena komodity bez stálého platu a regulovaných složek.

## Závazné zásady

1. Chybějící podklad není důkaz nepravdivosti.
2. Být nad pásmem ERÚ nedokazuje klamání.
3. Vzorek několika dodavatelů může celotržní superlativ vyvrátit, ne potvrdit.
4. OPM nejsou zákazníci; prázdná buňka v tabulce OTE není nula. Zákazníků s dodávkou energií ale nemůže být víc než odběrných míst.
5. Pásmo ERÚ nelze vydávat za celkový účet.
6. Neshoda sazby, území, fixace, období nebo základu DPH zastaví verdikt.
7. Pojem, který tvrzení nedefinuje, se posuzuje jen podle výkladů zapsaných týmem; karta je vždy vyjmenuje.
8. Rozhodnutí regulátora o minulém přestupku je kontext, ne důkaz proti dnešnímu sloganu.
