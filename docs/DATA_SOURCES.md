# Datové zdroje

Tento dokument popisuje zdroje, které aplikace **skutečně používá** (stav po code review z 15. 11. 2026). Starší návrh s konfigurací `config/suppliers.yaml` a `data/reference/manifest.json` se neimplementoval; je v historii gitu.

**Pravidlo:** konektor zdroj stáhne a rozparsuje, kód počítá čísla i verdikty, jazykový model (volitelný) jen navrhuje další citáty a řadí priority. Každý zdroj ve zprávě nese URL, čas získání, období dat a stav (živě, snapshot, chybí, ruční podklad).

## Matice zdrojů

| Zdroj | Konektor | URL | Období dat | Záloha | Omezení |
|---|---|---|---|---|---|
| ERÚ – indikativní pásma cen elektřiny (CSV) | `connectors/eru.py` | https://eru.gov.cz/ipnc (odkaz na CSV se čte ze stránky) | měsíce, sazby, fixace 12/24/36 | `snapshots/eru_elektrina.csv` | cena komodity bez stálého platu a regulovaných složek; odhad ERÚ, ne pořadí na trhu |
| OTE – počty OPM dodavatelů elektřiny | `connectors/ote.py` | https://www.ote-cr.cz/cs/statistika/mesicni-zprava-elektrina/pocty-opm-dodavatelu-v-cs-ote | měsíce aktuálního roku | `snapshots/ote_opm.html` | odběrná místa, ne zákazníci; prázdná buňka = chybí, ne nula |
| OTE – počty OPM dodavatelů plynu | `connectors/ote.py` | https://www.ote-cr.cz/cs/statistika/mesicni-zprava-plyn/pocty-opm-dodavatelu | měsíce aktuálního roku | `snapshots/ote_opm_gas.html` | tabulka zkracuje názvy na 20 znaků; s elektřinou se sčítá jen při stejném období |
| ERÚ – pravomocná rozhodnutí | `connectors/eru_decisions.py` | https://eru.gov.cz/pravomocna-rozhodnuti?ucastnik_rizeni=… | datum zveřejnění | `snapshots/eru_decisions_<id>.json` | čte se jen název, datum a štítky; nejvýše 3 stránky výsledků (zkrácení se hlásí) |
| Weby dodavatelů | `connectors/web.py` | `source_url` v `suppliers.json` | den stažení | `snapshots/web_<id>.html` | kontrola přítomnosti citátu v textu; JavaScriptové stránky mohou část textu skrýt |
| Ceníky dodavatelů (PDF) | `connectors/pricelist.py` | `price_list.url` v `suppliers.json` | `effective_from` v ceníku | `snapshots/pricelist_<id>.txt` | porovnává se otisk ručně zkontrolovaného textu; řádek tabulky je zapsán ručně |
| Ceníky E.ON | ruční podklad | – (za průvodcem na eon.cz) | `effective_from` | – | v manifestu jako „ruční podklad“; živě se kontroluje jen jedna cena na webu |

Všechna stahování jdou přes `connectors/http.py`: jen HTTPS, schválení hostitelé a port, nejvýše 3 přesměrování kontrolovaná před odesláním, limit 3 MB a jeden časový limit celého auditu.

## Připravená data (`src/competitive_intelligence/data/suppliers.json`)

- **Profil srovnání:** elektřina, sazba D02d, území PREdistribuce, 2 a 4 MWh ročně.
- **Dodavatel:** `ote_name` (název v tabulce OTE), `eru_name` (název v seznamu rozhodnutí ERÚ), `source_url`, datum zachycení.
- **Tvrzení:** doslovný citát, pravidlo a rozsah (`params.scope`: metrika, komodity, subjekty, období, srovnání).
- **Nabídka:** produkt, sazba, území, fixace, skupina zákazníků (`new`, `existing`, `all`), cena s DPH i bez, stálý plat, ceník (`url`, `effective_from`, `markers`, `row_locator`, `reviewed_sha256`, `reviewed_at`).
- **Zelený podklad:** produkt, typ (prohlášení dodavatele), text a značka pro kontrolu na stránce.
- **Definice:** seznam „tradičních dodavatelů“ jako výklad týmu, ne údaj ze zdroje.

## Časové označení v rozhraní

- **Živě:** „Získáno: datum a čas“ a období dat zdroje.
- **Snapshot:** „živé stažení selhalo, použit snapshot z …“, v běhu ze zálohy jedno souhrnné upozornění.
- **Chybí:** zdroj se nepodařilo stáhnout ani načíst ze zálohy; verdikty, které ho potřebují, se nevydají.
- **Ruční podklad:** údaje, které nejdou stáhnout automaticky (ceníky E.ON).

Datum získání nikdy nepřepisuje období dat: stažení v listopadu může vrátit tabulku za září.
