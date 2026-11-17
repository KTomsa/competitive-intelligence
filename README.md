# Fact or Watt — Competitive Intelligence

Studentská ukázka: nástroj, který ověřuje marketingová tvrzení českých dodavatelů elektřiny proti veřejným datům **ERÚ** a **OTE** a proti jejich vlastním ceníkům, a vrací manažerovi krátký briefing s doporučeným krokem. Kde podklady nestačí, nástroj výslovně odpoví „nelze ověřit“.

Rozsah vychází z revize architektury z 13. 11. 2026 (jedna Flask aplikace, žádná databáze, živý úřední zdroj). Code review z 15. 11. 2026 je zapracované; přehled změn je v části [Zapracované code review](#zapracované-code-review).

## Start za pět minut

Podporované prostředí: **Python 3.14** (soubor `.python-version`, na něm běží testy i CI) a správce balíčků `uv`. `pyproject.toml` povoluje Python 3.12 a novější kvůli hostingu; jiná verze než 3.14 ale není vyzkoušená.

```bash
uv sync --locked
```

```bash
uv run pytest -q
```

```bash
uv run flask --app src/index.py run --debug
```

Pak otevřete `http://127.0.0.1:5000`, zvolte „Analýza ze zálohy“ a spusťte analýzu: běží bez internetu i bez klíče k modelu. Testy také běží bez sítě a bez modelu, nad uloženými snapshoty.

### Užitečné příkazy

```bash
uv run python -m competitive_intelligence.snapshot
```

Uloží aktuální živé zdroje jako nové snapshoty (`data/snapshots/`) a u každého ceníku vypíše, zda stále obsahuje cenu a stálý plat z přípravy.

```bash
uv run python -m competitive_intelligence.workflow --save-example --perspective=centropol
```

Spustí živý audit a uloží ho jako ukázkovou zprávu (`data/example_report.json`), kterou rozhraní umí zobrazit bez stahování.

Kontrola živých úředních zdrojů je oddělená od běžných testů a pouští se jen na vyžádání:

```bash
RUN_LIVE_SMOKE=1 uv run pytest tests/test_live_smoke.py -q
```

## Co aplikace dělá

1. **Stáhne stránky dodavatelů** a zkontroluje, že připravené citáty na nich stále jsou. Ve vzorku je jedenáct dodavatelů: ČEZ Prodej, Centropol, E.ON, epet, PRE, MND, Dobrá Energie, SPP CZ, LAMA energy, ARMEX ENERGY a bezDodavatele.
2. **Stáhne jejich ceníky (PDF)** a porovná je s ručně zkontrolovanou verzí (otisk textu). Shodný ceník potvrzuje nabídku; změněný ceník se označí k nové kontrole a nabídka zůstane nepotvrzená.
3. **Stáhne úřední data**: indikativní cenová pásma ERÚ (CSV), počty odběrných míst elektřiny i plynu z OTE a seznam pravomocných rozhodnutí ERÚ podle účastníka řízení.
4. **Vyhodnotí tvrzení pravidly v kódu** (ne jazykovým modelem). Silný verdikt vydá jen tehdy, když podklad odpovídá rozsahu tvrzení: stejná metrika, komodita, subjekt a období.
5. **Srovná fixované nabídky pro nové zákazníky** podle ročního nákladu a ukáže i nabídky, které ze srovnání vyřadil, s důvodem.
6. **Ukáže cenu a růst vedle sebe** za stejné období pro všechny dodavatele a rozdíl cen pro nové a stávající zákazníky.
7. **Sestaví pohled jedné firmy** („Manažerský pohled“): kde stojíme cenou, co je v naší reklamě napadnutelné, co můžeme tvrdit a doložit a co dělá konkurence.
8. **Sestaví briefing** z pravidel v kódu.

## Příklad: od citátu k doporučení

| Krok | Obsah |
|---|---|
| Citát | epet: „Pro naše zákazníky máme vždy tu nejlepší cenu“ (epet.cz, zachyceno 13. 11. 2026) |
| Rozsah | cena pro nového zákazníka; srovnává se sazba D02d, území PRE, stejná délka fixace, roční náklad obchodní části |
| Přípustné důkazy | živě stažené ceníky epet FIX 36 GARANT a ČEZ Elektřina Fix na 3 roky, shodné se zkontrolovanou verzí |
| Výsledek | **v rozporu s daty**: levnější srovnatelnou nabídku mají ČEZ, Centropol, E.ON i ARMEX; např. při tříleté fixaci a 2 MWh ročně 9 871 Kč (epet) proti 8 460 Kč (ČEZ) |
| Omezení | jde o obchodní část ceny, ne celý účet; „nejlepší cena“ může v reklamě znamenat i něco jiného |
| Doporučený krok | tvrzení lze zpochybnit konkrétním protipříkladem z veřejných ceníků; před použitím znovu ověřit oba ceníky |

Opačný příklad: ČEZ „Jsme největší dodavatel elektřiny a plynu“ dostane **nelze ověřit**, protože tvrzení neříká, podle čeho se velikost měří. Počty odběrných míst z OTE (v elektřině 1., v plynu 2. za innogy) se ukážou jen jako kontext.

## Jazykový model (volitelný, ve výchozím stavu vypnutý)

Bez modelu aplikace funguje celá a je to doporučený režim pro ukázku. S modelem (`CI_USE_LLM=1`, `GEMINI_API_KEY`) navíc:

- **Claim Extractor** hledá na stránkách další tvrzení. Kód ověří, že citát na stránce doslova je, a pravidlo navržené modelem přijme jen tehdy, když mu citát odpovídá i podle klíčových slov.
- **Strategist** smí jen seřadit priority mezi kandidáty, které určil kód. Text briefingu, čísla i verdikty jsou vždy ze šablony a pravidel; model nepíše žádnou větu, kterou by aplikace zobrazila.

Do modelu jde nejvýše 20 000 znaků z jedné stránky a 120 000 znaků celkem. Volání se nezačne, když do konce časového limitu auditu zbývá málo času.

## Co je připravené a co se děje při běhu

| Připraveno předem (`suppliers.json`) | Staženo a spočítáno při běhu |
|---|---|
| Dodavatelé, schválené URL, názvy podle OTE a ERÚ | Stránky dodavatelů; kontrola, že citát stále platí |
| Doslovné citáty s datem zachycení, pravidlem a **rozsahem** (metrika, komodity, subjekty, období, srovnání) | Ceníky (PDF); porovnání s otiskem zkontrolované verze |
| Ceny, stálé platy, sazba, území, fixace, platnost a umístění řádku v ceníku | Pásma ERÚ pro sazbu, fixaci a měsíc nabídky |
| Otisk (SHA-256) ručně zkontrolovaného textu ceníku | Počty OPM elektřiny a plynu z OTE |
| Definice „tradičních dodavatelů“ (kontext k pojmu „alternativní“) | Pravomocná rozhodnutí ERÚ podle účastníka řízení |
| | Verdikt, nález, omezení, doporučený krok a stav každého zdroje |

Verdikty se předem nepřipravují.

## Zásady ověřování

- Chybějící nebo nesrovnatelný podklad znamená „nelze ověřit“, ne „nepravdivé“. Chybějící údaj není nula.
- Silný verdikt jen při shodě rozsahu: odběrná místa nejsou zákazníci, elektřina není elektřina a plyn, údaje z různých období se nesčítají.
- Pozice vůči indikativnímu pásmu ERÚ je nález, ne důkaz klamání ani změřené pořadí na trhu.
- Srovnávají se jen nabídky, které projdou jedním společným pravidlem oprávněnosti (sazba, území, fixace, skupina zákazníků, platnost, stav dokumentu). Totéž pravidlo platí pro verdikty, srovnání i doporučení.
- Nejnižší cena ve vzorku neprokazuje nejnižší cenu na trhu; levnější srovnatelná nabídka ale tvrzení o prvenství vyvrátit může.
- Prohlášení dodavatele není nezávislý důkaz.
- Pravomocné rozhodnutí ERÚ je kontext, ne verdikt. „Nenalezeno ve výsledcích hledání“ neznamená „neexistuje“.
- Výsledek není právní posouzení.

## Spolehlivost běhu

- **Manifest zdrojů**: zpráva vypisuje každý očekávaný zdroj a jeho stav (živě, snapshot, chybí, ruční podklad). Běh je „živý“ jen tehdy, když jsou živě všechny očekávané zdroje.
- **Jeden časový limit**: celý audit má limit `AUDIT_DEADLINE_S` (výchozí 45 s, pod limitem hostingu 60 s). Síť a model musí skončit o rezervu dřív (5 s, u krátkých limitů pětina), aby zbyl čas na pravidla a odeslání odpovědi. Každé stažení má tvrdý strop: běží ve vlákně a čeká se na něj nejvýš zbývající čas, takže spojení, hlavičky a data nemohou limit překročit součtem. Po vypršení se nové stažení nezačne a použije se datovaný snapshot.
- **Jedno datum srovnání**: kde stojí dvě nabídky vedle sebe (verdikt o ceně, tabulka srovnání, rozdíl pro nové a stávající zákazníky), musí obě platit k datu auditu. Nepotvrzená nabídka zachycená před víc než 62 dny se nepoužije.
- **Přesměrování**: každý krok přesměrování se kontroluje (HTTPS, schválený hostitel a port) dřív, než se na něj odešle požadavek.

## Nasazení a veřejné demo

Aplikace běží na https://fact-or-watt.vercel.app (`vercel.json`, `src/index.py`). Nasazuje se ručně z `main` a s číslem commitu, které se pak ukáže v patičce stránky a na `/api/v1/version`:

```bash
npx --yes vercel@latest deploy --prod --yes --env APP_BUILD=$(git rev-parse HEAD)
```

Postup pro veřejné demo:

1. Nastavte `DEMO_CODE` (sdílený přístupový kód). Na Vercelu (`VERCEL`) nebo s `PUBLIC_DEMO=1` je bez něj živý audit vypnutý; analýza ze zálohy a uložená ukázka fungují dál.
2. Model zapněte (`CI_USE_LLM=1`, `GEMINI_API_KEY`) jen s vědomím, že bezplatná vrstva občas vrátí chybu 503; aplikace pak hledá tvrzení podle klíčových slov.
3. Aplikace omezuje velikost požadavku (8 kB), souběžné audity (`MAX_CONCURRENT_AUDITS`, výchozí 2) a živé audity z jedné adresy (`LIVE_AUDITS_PER_WINDOW` za 10 minut, výchozí 6). **Tyto limity platí pro jednu instanci**; na serverless hostingu doplňte ochranu hostingu (např. heslo nasazení) a ověřte je na cílové adrese.
4. Před prezentací ověřte, že nasazená aplikace dosáhne na `eru.gov.cz`, `www.ote-cr.cz` a weby dodavatelů.

## Postup na den prezentace

1. Den předem ručně obnovit připravené příklady (ceníky a citáty) a ověřit, že verze v patičce odpovídá commitu v `main`.
1. Spustit živou analýzu a ukázat panel zdrojů: období dat ERÚ a OTE a čas stažení.
2. Projít příklad epet (v rozporu s daty) a ČEZ (nelze ověřit) a ukázat, proč se liší.
3. Přepnout na „Analýza ze zálohy“ a ukázat, že aplikace použití snapshotu výslovně označí.
4. Mít připravenou uloženou ukázku pro případ, že nepůjde internet.

## Struktura

```text
src/
  index.py                      # vstupní bod pro Vercel
  competitive_intelligence/
    app.py                      # Flask, validace požadavků, limity veřejného dema
    workflow.py                 # jeden běh auditu, časový limit, manifest zdrojů
    evidence.py                 # společná brána: rozsah tvrzení, oprávněnost nabídek, období OTE
    rules.py                    # pravidla a verdikty
    insights.py                 # přehled trhu a pohled jedné firmy
    agents.py                   # Claim Extractor, Strategist (jen řazení priorit)
    connectors/                 # http (allowlist, přesměrování, limit), eru, eru_decisions, ote, web, pricelist
    data/suppliers.json         # připravené vstupy
    data/snapshots/             # datované zálohy zdrojů
    data/example_report.json    # uložená ukázka
    templates/, static/
tests/                          # test_adjudicated.py = ručně posouzené případy na reálných datech
docs/ARCHITECTURE.md, RULE_CATALOG.md, DATA_SOURCES.md, historical/
```

## Jak přidat dodavatele nebo ceník

1. Do `suppliers.json` přidejte dodavatele (`ote_name` podle tabulky OTE, `eru_name` podle seznamu rozhodnutí ERÚ), doslovné citáty a pravidlo.
2. U tvrzení vyplňte rozsah (`params.scope`): `metric` (`opm`, `customers`, nebo bez metriky), `commodities`, případně `merged_with`, `period` a `comparator` (`at_least`, `at_most`, `approx`, `exactly`).
3. U nabídky uveďte cenu bez DPH i s DPH, stálý plat s DPH, sazbu, území, fixaci, skupinu zákazníků a odkaz na PDF ceník. Do `markers` dejte řetězce, které v ceníku musí být, do `row_locator` řádek a sloupec, odkud čísla jsou, a po ruční kontrole spusťte příkaz pro snapshoty a zapište otisk do `reviewed_sha256`.
4. Hostitele stránky a ceníku přidejte do `ALLOWED_HOSTS` v `connectors/http.py`.
5. Spusťte testy; ověří, že citáty i ceny ve zdrojích opravdu jsou. U nového důležitého tvrzení doplňte ručně posouzený řádek do `tests/test_adjudicated.py`.

## Zapracované code review

| Nález | Co se změnilo |
|---|---|
| QUAL-001 | Tvrzení mají rozsah (metrika, komodity, subjekty, období, srovnání). Chybějící údaj se nepočítá jako nula, různá období se nesčítají, velikost bez metriky a počty zákazníků dostanou jen kontext. |
| QUAL-002 | Zelený podklad musí patřit ke stejnému produktu; prohlášení dodavatele je nález, ne nezávislý důkaz. |
| QUAL-003 | Pásmo ERÚ se popisuje jako odhad pro sazbu, fixaci a měsíc, ne jako pořadí na trhu. |
| QUAL-004 | Jedno pravidlo oprávněnosti nabídek (`evidence.offer_eligibility`) pro verdikty, srovnání i doporučení; ceníky s otiskem a umístěním řádku; budoucí platnost se nepřijímá; období srovnání se řídí platností nabídky. |
| QUAL-005 | Změny počtu OPM za jedno společné období; trh jen za dodavatele s údajem na začátku i konci. |
| QUAL-006 | Briefing je vždy ze šablony; model smí jen seřadit kandidáty určené kódem. |
| OPS-001 | Manifest všech očekávaných zdrojů; „živý běh“ jen při živých zdrojích všech; „nenalezeno ve výsledcích“ místo „neexistuje“. |
| OPS-002 | Jeden časový limit auditu pro všechna stažení i volání modelu. |
| SEC-001 | Ruční přesměrování s kontrolou před každým krokem. |
| SEC-002 | Povinný kód na veřejném nasazení, limit velikosti, souběhu a četnosti. |
| QUAL-007 | Validace požadavku přes Pydantic, bezpečné porovnání kódu s diakritikou. |
| DEP-001 | Jedna podporovaná verze Pythonu, zámek bez větve pro staré Pythony. |
| TEST-001 | Ručně posouzené případy, testy limitu, přesměrování, manifestu a vstupů, CI úloha, volitelná kontrola živých zdrojů. |
| DOC-001 | Tento README, aktualizované `docs/`, staré návody přesunuté do `docs/historical/`. |

### Opakovaná revize (17. 11. 2026)

Revize potvrdila 10 ze 14 nálezů jako vyřešené. Zbylé čtyři jsou dokončené takto (testy v `tests/test_rereview.py`):

| Nález | Co se změnilo |
|---|---|
| QUAL-004 | Dvojice nabídek se srovnává k jednomu datu (`evidence.comparison_date`, `offer_current`): ve verdiktu o ceně, v tabulce srovnání i v rozdílu pro nové a stávající zákazníky. Nabídka, která k datu srovnání neplatí, se vyřadí s důvodem. |
| QUAL-001 | Velikost a růst respektují rozsah tvrzení (`evidence.ote_scope_gate`): jiný trh nebo období než data OTE → „nelze ověřit“; růst plynu se počítá z tabulky plynu, tvrzení jen o plynu se nehodnotí pořadím v elektřině. |
| OPS-002 | Rezerva na zpracování odpovědi a tvrdý strop každého stažení přes všechny fáze (spojení, hlavičky, data). |
| QUAL-005 | Růst z nulového výchozího stavu: absolutní změna zůstane, procento je „nelze určit“, řazení i text s tím počítají. |
| Původ nasazení | Patička stránky a `/api/v1/version` ukazují commit, ze kterého nasazení běží. |

## Známá omezení

- Ceny jsou z ceníků pro distribuční území PRE a sazbu D02d; jinde se mohou lišit.
- Ceníky E.ON nejdou stáhnout automaticky; jejich ceny dodal tým z PDF stažených 13. 11. 2026 a v přehledu zdrojů jsou označené jako ruční podklad.
- Kontrola ceníku porovnává otisk celého textu a hledá hodnoty v textu; řádek tabulky, odkud čísla pochází, je zapsaný ručně (`row_locator`), automaticky se neověřuje.
- Údaj, zda je produkt pro nové nebo i stávající zákazníky, pochází ze stránek dodavatele; u E.ON je odvozen z názvů produktů.
- Velikost dodavatele umíme měřit jen počtem odběrných míst; objem dodané energie, tržby ani vlastnictví firem v datech nejsou.
- Z rozhodnutí ERÚ se čte jen název, datum zveřejnění a štítky, a hledá se podle názvu; skutek ani výši sankce aplikace nezná.
- Souběh ceny a růstu není důkaz příčiny.
- Odkaz na směrnici (EU) 2024/825 u zelených tvrzení nebyl právně ověřen.
- Nasazení je vyzkoušené jednotlivými běhy (revize 17. 11. 2026: 34 z 37 zdrojů živě, 26,8 s). Nevyzkoušeno: chování pod zátěží, dlouhodobá dostupnost, náklady a kvóty modelu.
- Limity četnosti a souběhu platí jen v rámci jedné instance hostingu.
