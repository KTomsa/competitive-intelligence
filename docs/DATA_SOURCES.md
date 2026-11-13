# Datové zdroje, režimy načítání a kompetence — v2

**Pravidlo:** API/scraping/parser získává fakta, metadata a strojově čitelné tabulky. **LLM** vybírá citáty, klasifikuje formulace a volá schválené evidenční nástroje. **Kód** počítá metriky i verdikt. Předem ověřit licenci, robots.txt, podmínky API a datovou dostupnost konkrétního URL.

| Zdroj a příklady | Frekvence / režim | API, scraping a deterministický kód | LLM agent (pouze role) | Výstup + omezení |
|---|---|---|---|---|
| **Weby dodavatelů**: homepage, produkt, green landing, reklamy | **Live při každém auditu** 2–4 URL/dodavatel | Web Scout: bezpečně stahuje HTML, odstraní boilerplate, uloží text/hash/URL/čas, porovná baseline | Claim Extractor: doslovné marketingové výroky, rule ID, produkt, scope, citace | `Claim` + `origin`; JS/anti-bot/robots mohou znemožnit fetch |
| **Baseline schválených tvrzení** | Při shodném hash live stránky | Načte JSON s verzí a schváleným hashem; žádné nové HTTP | Žádný nový Extractor | `origin=baseline`; musí být znám `baseline_approved_at` |
| **Snapshot neúspěšného live fetchu** | Jen fallback | Načte immutable uložený text z konkrétní URL a datum | Claim Extractor jen pokud snapshot bez schválených claims | `origin=snapshot`, `fallback_reason`, stáří a omezení |
| **Ceníky a podmínky dodavatelů** PDF/HTML | Kurátorované `data/reference/`, průběžná aktualizace | HTTP download / PDF parser / manuálně zkontrolované JSON; metadata produktu, DPH, platnosti | Price agent volá `get_price_offers`, `calculate_annual_cost`; interpretuje, co výrok srovnává | Srovnatelné nabídky/roční obchodní náklad; PDF parser bez kontroly může chybovat |
| **ERÚ — srovnávač/cenové podklady** | Reference; kontrola dostupnosti | Pouze zdokumentované exporty/podporovaný import; nevymýšlet skryté API | Price agent volí důkazní profil, nepočítá samotné ceny | Omezení pokrytí a metodiky zdroje |
| **OTE — počty OPM dodavatelů** XLSX/statistiky | Reference podle zveřejnění | Stáhnout XLSX/CSV a číst přes parser, vypočítat změny OPM | Customer agent volí období a porovnává **výslovně OPM** tvrzení | OPM ≠ unikátní zákazníci |
| **OTE — zbytkový mix a záruky původu** | Reference podle publikačního cyklu | Stažení tabulek/výkazů a parsování | Green agent volí konkrétní produkt / doklad, posoudí souvislost | Zbytkový mix ≠ individuální mix produktu; chybějící GO nemusí znamenat nepravdu |
| **Mix konkrétního produktu/dodavatele** | Reference, obvykle ročně či při změně produktu | Stáhnout deklaraci a doklad, uchovat čas a produkt | Green agent interpretuje formulaci a vyžádá konkrétní evidence | Přednost produktově specifickým důkazům |
| **ERÚ — sankce / rozhodnutí** | Reference, nepravidelně | Download záznamů, normalizace IČO, data a předmětu rozhodnutí | Customer agent určí relevanci; případně jen kontext | Sankce sama nedokazuje nepravdivost obecného sloganu; absence sankcí nedokazuje férovost |
| **ARES** (volitelné) | Reference dle potřeby | REST lookup entity / IČO, bez potřeby LLM | Agent případně jen řeší nejednoznačné jméno firmy | Identifikace entity, ne důkaz marketingového tvrzení |
| **Google hodnocení** (volitelné) | Reference s API podmínkami | Legální API / agregované hodnoty a počet, bez jmen a textů | Customer agent posoudí vztah k přesnému tvrzení | Není reprezentativní spokojenost všech zákazníků |

## Registry zdrojů
Konfigurace v `config/suppliers.yaml` uvádí `supplier_id`, právní entitu/IČO (pokud ověřeno), `allowed_urls` (přesné HTTPS URL a domény), `page_kind`, `commodity`. `data/reference/manifest.json` uvádí `source_id`, `source_url`, `retrieved_at`, `valid_from`, `valid_to`, `content_sha256`, `parser_version`, `scope`, `license_note` a případně `reviewed_by`.

### Přístupová a provozní pravidla
- `mode=live`: stahuj **pouze reklamní stránky** při každém auditu; referenční data se načítají z kontrolovaných verzovaných souborů s datem platnosti.
- Chybí-li oficiální/API přístup, použij legální zveřejněný dokument nebo import; neobcházej ochrany webu a neodvozuj skryté API.
- Každý důkaz potřebuje `source_url`, `source_ref`, `captured_at`, `valid_as_of`, identitu produktu a lokátor údajů; poškozené PDF/HTML data nepodporují kladný verdict.
- Test fixtures uchovávej odděleně od produkčních dat; UI je musí jasně označit jako ukázkové.

## Uživatelské časové označení (UI)
- **Live:** „Z webu načteno: datum a čas“ + údaj o tom, kdy platí referenční ceník.
- **Baseline:** „Web byl načten a beze změny; použitá schválená tvrzení ze dne …“.
- **Snapshot:** „Web se nepodařilo načíst; použit uložený snapshot z …“.
Žádné datum získání referenční ceny nesmí být přepsáno na datum aktuálního web fetchu.

## Oficiální orientační vstupy (ověřit aktuální URL/formát při implementaci)
- OTE OPM elektřina: https://www.ote-cr.cz/cs/statistika/mesicni-zprava-elektrina/pocty-opm-dodavatelu-v-cs-ote
- OTE zbytkový mix: https://www.ote-cr.cz/cs/statistika/zbytkovy-energeticky-mix
- ERÚ: https://eru.gov.cz/
- ARES: https://ares.gov.cz/
