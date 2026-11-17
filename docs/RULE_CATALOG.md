# Katalog ověřovacích pravidel

Každé tvrzení má právě jedno `rule_id` z tohoto seznamu. Výpočet a verdikt patří kódu (`rules.py`), ne jazykovému modelu.

**Společná brána:** pravidla nedostávají surová data, ale výsledek `evidence.py`: rozsah tvrzení (metrika, komodity, subjekty, období, srovnání), oprávněnost nabídky a součty OPM, u kterých chybějící údaj není nula a různá období se nesčítají. Tvrzení o velikosti a růstu projdou branou `ote_scope_gate`: jiný trh než Česko, komodita mimo elektřinu a plyn nebo jiné období než tabulka OTE → `unverifiable` s kontextem. Dvě nabídky vedle sebe musí platit k jednomu datu srovnání (`comparison_date`, `offer_current`).

**Verdikty:** `supported` (podloženo), `partially_supported` (částečně; vždy s rozpisem dílčích tvrzení), `contradicted` (v rozporu s daty), `unverifiable` (nelze ověřit). Odděleně příznak `possible_regulatory_risk` s důvodem; není to právní závěr.

| Rule ID | Typický výrok | Zdroj | Jak se rozhoduje |
|---|---|---|---|
| `price_favorable` | „Máme výhodné ceny“, „Ušetříte“ | ERÚ + ceníky | Srovnatelné nabídky nejvýše na horní hranici pásma → `supported`; nález uvádí, zda jsou pod pásmem (nejlevnější čtvrtina trhu). U tvrzení o úspoře zákazníka jen `partially_supported`. Nabídka nad pásmem → `unverifiable` s nálezem, ne rozpor. |
| `price_rank` | „Vždy nejlepší cena“, „Garance nejnižší ceny“ | ceníky dodavatelů | Levnější srovnatelná nabídka s nižším ročním nákladem ve všech profilech spotřeby → `contradicted`. Nižší jen cena za MWh nebo chybí stálý plat → `unverifiable` s nálezem. Prvenství ve vzorku → `unverifiable`. Závazek dorovnání ceny → vždy `unverifiable`, nález uvádí levnější nabídku. Bez ceníku dodavatele → `unverifiable`. |
| `price_pct_cheaper` | „O 7 % levnější než X“ | ceníky dodavatelů | Roční náklad obchodní části obou dodavatelů ve všech profilech spotřeby. Platí všude → `supported`, nikde → `contradicted`, jen někde nebo bez stálého platu či základu → `unverifiable`. |
| `price_guarantee` | „Garance ceny na 3 roky“ | ceník + ERÚ | Produkt s danou fixací existuje a cena nepřesahuje pásmo → `supported`; existuje, ale cenu nelze srovnat → `partially_supported`. |
| `market_size` | „Největší dodavatel“ | OTE elektřina + plyn | Bez metriky v tvrzení → `unverifiable`, pořadí podle OPM jako kontext. Jen u tvrzení výslovně o odběrných místech: první ve všech komoditách, o kterých tvrzení mluví → `supported`, v části → `partially_supported`, v žádné → `contradicted` (tvrzení jen o plynu se nehodnotí pořadím v elektřině); jiný trh nebo období než data OTE → `unverifiable`; chybějící údaj o plynu → `unverifiable`. „Český“ (vlastnictví) a „alternativní“ (výklad týmu) → vždy `unverifiable` s kontextem. |
| `customer_count` | „1,5 milionu zákazníků“, „185 000 odběrných míst“ | OTE elektřina + plyn | Tvrzení o odběrných místech v ČR: porovnání podle srovnání (`at_least`, `at_most`, `exactly`, `approx` ±5 % potvrzuje, nad 15 % vyvrací). Tvrzení o zákaznících nebo přes více zemí → vždy `unverifiable`, OPM jen jako kontext (nad počtem OPM v každém měsíci se označí příležitost). Jiné období tvrzení a dat, chybějící údaj nebo komodita z jiného období → `unverifiable`. |
| `customer_growth` | „Rosteme“ | OTE | Změna OPM ve společném období tabulky, pro komodity a subjekty z rozsahu tvrzení (plyn z tabulky plynu; tabulky s jiným obdobím se nesčítají). Jen u tvrzení výslovně o odběrných místech: růst → `supported`, jinak `contradicted`; bez metriky nebo superlativ → `unverifiable`. Z nulového výchozího stavu se uvede jen absolutní změna, procento „nelze určit“. |
| `green_share` | „100 % zelená elektřina“, „krytá zárukami původu“ | podklad k témuž produktu | Podklad jiného produktu se nepoužije. Prohlášení dodavatele → `unverifiable` s nálezem „prohlášení nalezeno, záruky nezávisle neověřeny“. Bez podkladu → `unverifiable` + příznak rizika. |
| `carbon_neutral` | „Emise kompenzujeme“ | – | `unverifiable` + příznak rizika s důvodem. |
| `green_generic` | „Šetrná k přírodě“ | – | `unverifiable` + příznak rizika. |
| `soft_trust` | „Férový a spolehlivý“ | ERÚ – pravomocná rozhodnutí | Vždy `unverifiable`. Nález uvádí počet pravomocných rozhodnutí o přestupcích a kolik se jich týká obchodních praktik; bez nálezu „v načtených výsledcích nenalezeno“, ne „neexistuje“. |
| `other` | cokoli jiného | – | `unverifiable`. |

## Srovnávací profil

- Elektřina, sazba `D02d`, distribuční území PRE, roční spotřeba 2 a 4 MWh (parametry v `suppliers.json`).
- Cena komodity se srovnává bez DPH; roční náklad obchodní části s DPH: `12 × stálý plat + MWh × cena`.
- Pásmo ERÚ se bere pro měsíc, ve kterém nabídka platí (živě potvrzená nabídka = aktuální měsíc).
- Srovnávají se jen nabídky se stejnou sazbou, územím a délkou fixace. Produkty s postupně se měnící cenou a nabídky, které živý ceník už nepotvrzuje, se vynechávají.
- Dvojice nabídek (žebříček, „o X % levnější“, tabulka srovnání, rozdíl pro nové a stávající zákazníky) se srovnává k jednomu datu, datu auditu. Obě nabídky k němu musí platit; nepotvrzená nabídka zachycená víc než 62 dní před ním se nepoužije.

## Jak číst pásmo ERÚ

Indikativní pásmo je odhad ERÚ pro sazbu, délku fixace a měsíc. Podle metodiky dolní mez navazuje na 25. percentil a horní mez přibližně na medián nabídek ze srovnávače, nabídky se ale od pásma krátkodobě odchylují. Pozice vůči pásmu proto není změřené pořadí na trhu; pořadí ve vzorku dodavatelů platí jen pro tento vzorek. Pásmo je cena komodity bez stálého platu a regulovaných složek.

## Závazné zásady

1. Chybějící podklad není důkaz nepravdivosti.
2. Být nad pásmem ERÚ nedokazuje klamání.
3. Vzorek několika dodavatelů může celotržní superlativ vyvrátit, ne potvrdit.
4. OPM nejsou zákazníci; prázdná buňka v tabulce OTE není nula. Zákazníků s dodávkou energií ale nemůže být víc než odběrných míst.
5. Pásmo ERÚ nelze vydávat za celkový účet.
6. Neshoda sazby, území, fixace, období nebo základu DPH zastaví verdikt.
7. Pojem, který tvrzení nedefinuje, se posuzuje jen podle výkladů zapsaných týmem; karta je vždy vyjmenuje.
8. Rozhodnutí regulátora o minulém přestupku je kontext, ne důkaz proti dnešnímu sloganu.
