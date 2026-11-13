# Fact or Watt — pokyny pro Copilot

Studentská ukázka: jedna Flask aplikace, která ověřuje marketingová tvrzení dodavatelů elektřiny proti živým datům ERÚ a OTE. Podrobnosti: `README.md`, `docs/ARCHITECTURE.md`, `docs/RULE_CATALOG.md`.

## Rozsah

- Jedna aplikace, jeden požadavek, jeden běh. **Žádná databáze, fronta, worker, plánovač ani samostatné služby agentů.**
- Žádný Next.js, React ani TypeScript; šablony Jinja2 a čistý JavaScript.
- Rozhraní přijímá jen identifikátory dodavatelů z `data/suppliers.json`, nikdy libovolné URL.

## Pravidla pro kód

- Čísla a verdikty počítá `rules.py`. Jazykový model smí jen vybírat citáty a formulovat briefing; jeho výstup vždy kontroluje kód.
- Stahovat jen přes `connectors/http.py` (seznam schválených hostitelů, limit času a velikosti).
- Každý údaj nese URL zdroje, čas získání, období dat a původ `live` nebo `snapshot`. Snapshot se nikdy nevydává za živá data.
- Chybějící nebo nesrovnatelný podklad vrací `unverifiable`, nikdy `contradicted`.
- Text z webu a z modelu vkládat do stránky přes `textContent`, ne `innerHTML`.
- Do `suppliers.json` patří jen skutečné citáty a ceny s URL a datem zachycení; vymyšlená data jen v testech.

## Testy

- `pytest`, bez sítě a bez modelu (snapshoty, napodobený model).
- U každého pravidla případ podložený, vyvrácený a s nesrovnatelným podkladem.
- Netvrdit, že nasazení na Vercel nebo volání skutečného modelu funguje, dokud to nikdo nespustil.

## Nástroje

Závislosti spravuje `uv` (`pyproject.toml`, `uv.lock`).
