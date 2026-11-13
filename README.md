# Fact or Watt — Competitive Intelligence

Studentská ukázka: nástroj, který ověřuje marketingová tvrzení českých dodavatelů elektřiny proti veřejným datům **ERÚ** a **OTE** a proti jejich vlastním ceníkům, a vrací manažerovi krátký briefing s doporučeným krokem.

Rozsah vychází z revize architektury z 13. 11. 2026 (jedna aplikace, žádná databáze, živý úřední zdroj). Technologie zůstává Flask.

## Co aplikace dělá

1. **Stáhne stránky dodavatelů** a zkontroluje, že připravené citáty na nich stále jsou. Ve vzorku je deset dodavatelů: ČEZ Prodej, Centropol, E.ON, epet, PRE, MND, Dobrá Energie, SPP CZ, LAMA energy a bezDodavatele.
2. **Stáhne jejich ceníky (PDF)** a ověří, že obsahují cenu i stálý měsíční plat z přípravy.
3. **Stáhne úřední data**: indikativní cenová pásma ERÚ (CSV) a počty odběrných míst dodavatelů elektřiny i plynu z OTE.
4. **Vyhodnotí tvrzení pravidly v kódu** (ne jazykovým modelem) a ke každému vrátí kartu: tvrzení, nález, omezení, doporučený krok, podklady s výpočtem.
5. **Srovná fixované nabídky** podle ročního nákladu (cena komodity a stálý plat) a podle pozice vůči pásmu ERÚ.
6. **Sestaví briefing** s nejvýše třemi prioritami.

Když živé stažení selže, použije se datovaný snapshot a rozhraní to viditelně označí. Snapshot se nikdy nevydává za živá data.

## Spuštění

```bash
uv lock
```

```bash
uv sync
```

```bash
uv run flask --app src/index.py run --debug
```

```bash
uv run pytest
```

Bez `uv` stačí virtuální prostředí a `pip install flask httpx beautifulsoup4 pypdf pydantic google-genai python-dotenv pytest`.

Testy běží bez sítě a bez jazykového modelu (nad uloženými snapshoty).

### Užitečné příkazy

```bash
uv run python -m competitive_intelligence.snapshot
```

Uloží aktuální živé zdroje jako nové snapshoty (`data/snapshots/`) a u každého ceníku vypíše, zda stále obsahuje cenu a stálý plat z přípravy.

```bash
uv run python -m competitive_intelligence.workflow --save-example
```

Spustí živý audit a uloží ho jako ukázkovou zprávu (`data/example_report.json`), kterou rozhraní umí zobrazit bez stahování.

## Jazykový model (volitelný)

Bez modelu aplikace funguje celá. S modelem navíc:

- **Claim Extractor** hledá na stránkách další tvrzení; kód ověří, že citát na stránce doslova je.
- **Strategist** vybere priority a napíše shrnutí; kód ověří, že odkazuje jen na existující tvrzení.

Zapnutí: zkopírujte `.env.example` jako `.env`, nastavte `CI_USE_LLM=1` a `GEMINI_API_KEY` (Google Gemini, bezplatná vrstva stačí). Audit s modelem dělá dvě volání (jedno na tvrzení ze všech stránek, jedno na briefing) a s deseti dodavateli trvá zhruba 12 sekund. Bezplatná vrstva Gemini nesnáší souběžná volání, proto se stránky posílají najednou. Při chybě modelu se použije cesta bez modelu a rozhraní to uvede.

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
    connectors/                 # http (allowlist), eru, ote, web, pricelist
    data/suppliers.json         # připravené vstupy: citáty, URL, data zachycení, ceny, odkazy na ceníky
    data/snapshots/             # datované zálohy zdrojů
    data/example_report.json    # uložená ukázka
    templates/, static/
tests/
docs/ARCHITECTURE.md, RULE_CATALOG.md, DATA_SOURCES.md
```

## Co je připravené a co se děje při běhu

| Připraveno předem (`suppliers.json`) | Staženo a spočítáno při běhu |
|---|---|
| Seznam dodavatelů a schválených URL | Stránky dodavatelů; kontrola, že citát stále platí |
| Doslovné citáty s datem zachycení a přiřazeným pravidlem | Ceníky (PDF); kontrola, že obsahují cenu i stálý plat |
| Ceny a stálé platy z ceníků (produkt, sazba, území, fixace) | Pásma ERÚ pro sazbu, fixaci a měsíc nabídky |
| Odkazy na ceníky a jejich účinnost | Počty OPM elektřiny a plynu z OTE |
| | Roční náklad, verdikt, nález, omezení a doporučený krok |

Verdikty se předem nepřipravují.

## Jak přidat dodavatele nebo ceník

1. Do `suppliers.json` přidejte dodavatele (název právnické osoby podle tabulky OTE do `ote_name`), doslovné citáty a pravidlo.
2. U nabídky uveďte cenu bez DPH i s DPH, stálý plat s DPH, sazbu, distribuční území a odkaz na PDF ceník. Do `markers` dejte řetězce, které v ceníku musí být (název produktu, cena, stálý plat).
3. Hostitele stránky a ceníku přidejte do `ALLOWED_HOSTS` v `connectors/http.py`.
4. Spusťte příkaz pro snapshoty a testy; test ověří, že citáty i ceny ve zdrojích opravdu jsou.

## Zásady ověřování

- Chybějící nebo nesrovnatelný podklad znamená „nelze ověřit“, ne „nepravdivé“.
- Pozice vůči pásmu ERÚ je nález, ne důkaz klamání. Pásmo odpovídá zhruba 25. až 50. percentilu nabídek na trhu.
- Srovnávají se jen nabídky se stejnou sazbou, distribučním územím a délkou fixace a s jednou pevnou cenou.
- Nejnižší cena ve vzorku neprokazuje nejnižší cenu na trhu; levnější srovnatelná nabídka ale tvrzení o prvenství vyvrátit může.
- OPM jsou odběrná místa, ne zákazníci.
- Nedefinovaný pojem („český“, „alternativní“ dodavatel) verdikt zastaví; nález přesto uvede čísla.
- Výsledek není právní posouzení.

## Nasazení

`vercel.json` a `src/index.py` jsou připravené pro Vercel, ale **nasazení zatím nikdo nevyzkoušel**. Před prezentací je potřeba ověřit hlavně to, že nasazená aplikace dosáhne na `eru.gov.cz`, `www.ote-cr.cz` a weby dodavatelů. Na veřejném nasazení nastavte `DEMO_CODE` a `GEMINI_API_KEY`.

## Známá omezení

- Ceny jsou z ceníků pro distribuční území PRE a sazbu D02d; jinde se mohou lišit.
- Ceníky E.ON nejdou stáhnout automaticky (jsou za vícekrokovým průvodcem). Jejich ceny dodal tým z PDF stažených 13. 11. 2026; při běhu se živě ověřuje jen cena jednoho produktu na stránce.
- LAMA energy nemá ve vzorku ceník, takže její cenové tvrzení zůstává neověřené.
- Kontrola ceníku hledá v textu PDF název produktu, cenu a stálý plat; neověřuje, že čísla patří ke stejnému řádku tabulky.
- Zelená tvrzení lze podložit jen prohlášením dodavatele; záruky původu v evidenci OTE se neověřují.
- Připravené citáty a ceny zastarávají; když dodavatel změní web nebo ceník, aplikace na to upozorní a údaj je potřeba znovu zachytit.
- Odkaz na směrnici (EU) 2024/825 u zelených tvrzení nebyl právně ověřen.
