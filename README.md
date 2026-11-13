# Fact or Watt — Competitive Intelligence

Studentská ukázka: nástroj, který ověřuje marketingová tvrzení českých dodavatelů elektřiny proti veřejným datům **ERÚ** a **OTE** a vrací manažerovi krátký briefing s doporučeným krokem.

Rozsah vychází z revize architektury z 13. 11. 2026 (jedna aplikace, žádná databáze, živý úřední zdroj). Technologie zůstává Flask.

## Co aplikace dělá

1. **Stáhne stránky dodavatelů** (ČEZ Prodej, Centropol, E.ON, bezDodavatele) a zkontroluje, že připravené citáty a ceny na nich stále jsou.
2. **Stáhne úřední data**: indikativní cenová pásma ERÚ (CSV) a počty odběrných míst dodavatelů z OTE.
3. **Vyhodnotí tvrzení pravidly v kódu** (ne jazykovým modelem) a ke každému vrátí kartu: tvrzení, nález, omezení, doporučený krok, podklady s výpočtem.
4. **Sestaví briefing** s nejvýše třemi prioritami.

Když živé stažení selže, použije se datovaný snapshot a rozhraní to viditelně označí. Snapshot se nikdy nevydává za živá data.

## Spuštění

```bash
uv sync
```

```bash
uv run flask --app src/index.py run --debug
```

```bash
uv run pytest
```

Bez `uv` stačí virtuální prostředí a `pip install flask httpx beautifulsoup4 pydantic google-genai python-dotenv pytest`.

Testy běží bez sítě a bez jazykového modelu (nad uloženými snapshoty).

### Užitečné příkazy

```bash
uv run python -m competitive_intelligence.snapshot
```

Uloží aktuální živé zdroje jako nové snapshoty (`data/snapshots/`).

```bash
uv run python -m competitive_intelligence.workflow --save-example
```

Spustí živý audit a uloží ho jako ukázkovou zprávu (`data/example_report.json`), kterou rozhraní umí zobrazit bez stahování.

## Jazykový model (volitelný)

Bez modelu aplikace funguje celá. S modelem navíc:

- **Claim Extractor** hledá na stránce další tvrzení; kód ověří, že citát na stránce doslova je.
- **Strategist** přepíše briefing; kód ověří, že odkazuje jen na existující tvrzení.

Zapnutí: zkopírujte `.env.example` jako `.env`, nastavte `CI_USE_LLM=1` a `GEMINI_API_KEY` (Google Gemini, stejný poskytovatel a model jako ve větvi `feature/gemini-backend`). Audit s modelem dělá dvě volání (jedno na tvrzení ze všech stránek, jedno na briefing) a trvá zhruba 6 až 7 sekund. Bezplatná vrstva Gemini nesnáší souběžná volání, proto se stránky posílají najednou. Při chybě modelu se použije cesta bez modelu a rozhraní to uvede.

Model nesmí do briefingu psát čísla: nálezy, ceny a lhůty doplňuje kód. Text s číslicí od modelu se zahodí a použije se šablona.

## Struktura

```text
src/
  index.py                      # vstupní bod pro Vercel
  competitive_intelligence/
    app.py                      # Flask: stránka + POST /api/v1/analyze + GET /api/v1/example
    workflow.py                 # jeden běh auditu
    rules.py                    # pravidla a verdikty
    agents.py                   # Claim Extractor, Strategist
    connectors/                 # http (allowlist), eru, ote, web
    data/suppliers.json         # připravené vstupy: citáty, URL, data zachycení, ceny
    data/snapshots/             # datované zálohy zdrojů
    data/example_report.json    # uložená ukázka
    templates/, static/
tests/
docs/ARCHITECTURE.md, RULE_CATALOG.md, DATA_SOURCES.md
```

## Co je připravené a co se děje při běhu

| Připraveno předem (`suppliers.json`) | Staženo a spočítáno při běhu |
|---|---|
| Seznam dodavatelů a schválených URL | Stránky dodavatelů; kontrola, že citát a cena stále platí |
| Doslovné citáty s datem zachycení a přiřazeným pravidlem | Pásma ERÚ pro sazbu, fixaci a měsíc nabídky |
| Ceny nabídek (produkt, sazba, fixace, základ DPH) | Pořadí dodavatelů podle OPM z OTE |
| | Verdikt, nález, omezení a doporučený krok |

Verdikty se předem nepřipravují.

## Zásady ověřování

- Chybějící nebo nesrovnatelný podklad znamená „nelze ověřit“, ne „nepravdivé“.
- Pozice vůči pásmu ERÚ je nález, ne důkaz klamání.
- Nejnižší cena ve vzorku čtyř dodavatelů neprokazuje nejnižší cenu na trhu.
- OPM jsou odběrná místa, ne zákazníci.
- Výsledek není právní posouzení.

## Nasazení

`vercel.json` a `src/index.py` jsou připravené pro Vercel, ale **nasazení zatím nikdo nevyzkoušel**. Před prezentací je potřeba ověřit hlavně to, že nasazená aplikace dosáhne na `eru.gov.cz` a `www.ote-cr.cz`. Na veřejném nasazení nastavte `DEMO_CODE`.

## Známá omezení

- Stálý měsíční plat je znám jen u jedné nabídky (Centropol, 3 roky), proto pravidlo o cenovém prvenství zatím nikde nevydá „v rozporu s daty“; cesta je pokrytá testem.
- E.ON na stránce neuvádí, zda je cena s DPH, ani sazbu, takže jeho cenu nelze s pásmem ERÚ srovnat.
- Zelená tvrzení nemají produktový podklad (záruky původu), takže končí jako „nelze ověřit“.
- `uv.lock` neodpovídá novým závislostem; po stažení spusťte `uv lock`.
