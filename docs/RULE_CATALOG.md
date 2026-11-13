# Katalog ověřovacích pravidel

Každé tvrzení má právě jedno `rule_id` z tohoto seznamu. Výpočet a verdikt patří kódu (`rules.py`), ne jazykovému modelu.

**Verdikty:** `supported` (podloženo), `partially_supported` (částečně; vždy s rozpisem dílčích tvrzení), `contradicted` (v rozporu s daty), `unverifiable` (nelze ověřit). Odděleně příznak `possible_regulatory_risk`; není to právní závěr.

| Rule ID | Typický výrok | Zdroj | Jak se rozhoduje |
|---|---|---|---|
| `price_favorable` | „Máme výhodné ceny“, „Ušetříte“ | ERÚ | Srovnatelné nabídky (známá sazba, fixace, základ DPH) nejvýše na horní hranici pásma → `supported`. U tvrzení o úspoře zákazníka jen `partially_supported`. Nabídka nad pásmem → `unverifiable` s nálezem, ne rozpor. |
| `price_rank` | „Nejlepší ceny na trhu“, „Garance nejnižší ceny“ | weby dodavatelů | Levnější srovnatelná nabídka (stejná sazba a fixace) s nižším ročním nákladem včetně stálého platu → `contradicted`. Chybí-li stálý plat → `unverifiable` s nálezem. Prvenství ve vzorku → `unverifiable`. Závazek dorovnání ceny → vždy `unverifiable`, nález uvádí nalezenou nižší cenu. |
| `price_guarantee` | „Garance ceny na 3 roky“ | web + ERÚ | Produkt s danou fixací existuje a cena nepřesahuje pásmo → `supported`; existuje, ale cenu nelze srovnat → `partially_supported`. |
| `market_size` | „Největší dodavatel“ | OTE | Pořadí podle počtu OPM elektřiny. 1. místo → `supported` (zahrnuje-li tvrzení i plyn, `partially_supported`); jiné místo → `contradicted`. Nedefinované omezení („český“) → `unverifiable`. |
| `price_pct_cheaper` | „O 7 % levnější než X“ | weby dodavatelů | Roční náklad obchodní části obou dodavatelů ve všech profilech spotřeby. Platí všude → `supported`, nikde → `contradicted`, jen někde nebo bez stálého platu či základu → `unverifiable`. |
| `customer_count` | „1,5 milionu zákazníků“ | OTE | `unverifiable`; počet OPM se uvádí jen jako kontext. Jen když tvrzení výslovně mluví o odběrných místech, porovná se s OPM. |
| `customer_growth` | „Rosteme“ | OTE | Počet OPM mezi prvním a posledním zveřejněným měsícem roku: růst → `supported`, jinak `contradicted`. Superlativ („nejrychleji“) → `unverifiable`. |
| `carbon_neutral` | „Emise kompenzujeme“ | – | `unverifiable` + příznak rizika s důvodem. |
| `green_share` | „100 % zelená elektřina“ | podklad k produktu | Bez produktového dokladu (záruky původu) → `unverifiable` + příznak rizika. |
| `green_generic` | „Šetrná k přírodě“ | – | `unverifiable` + příznak rizika. |
| `soft_trust` | „Férový a spolehlivý“ | – | `unverifiable`. |
| `other` | cokoli jiného | – | `unverifiable`. |

## Srovnávací profil

- Elektřina, sazba `D02d`, roční spotřeba 2 a 4 MWh (parametry v `suppliers.json`).
- Cena komodity se srovnává bez DPH; roční náklad obchodní části s DPH: `12 × stálý plat + MWh × cena`.
- Pásmo ERÚ se bere pro měsíc, ve kterém nabídka platí (živě potvrzená nabídka = aktuální měsíc).

## Závazné zásady

1. Chybějící podklad není důkaz nepravdivosti.
2. Být nad pásmem ERÚ nedokazuje klamání.
3. Vzorek několika dodavatelů může celotržní superlativ vyvrátit, ne potvrdit.
4. OPM nejsou zákazníci; prázdná buňka v tabulce OTE není nula.
5. Pásmo ERÚ je cena komodity bez stálého platu a regulovaných složek; nelze ho vydávat za celkový účet.
6. Neshoda sazby, fixace, období nebo základu DPH zastaví verdikt.
