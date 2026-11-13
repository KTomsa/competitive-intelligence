---
applyTo: "src/competitive_intelligence/templates/**/*.html,src/competitive_intelligence/static/**/*.js,src/competitive_intelligence/static/**/*.css"
---
# Frontend — Flask šablony, HTML, JavaScript, Bootstrap
- Používej přístupné HTML, Jinja2 autoescape, vanilla JS modules (`fetch`), volitelně Bootstrap 5; **žádný Next.js ani frontend framework**.
- Tři stavy obrazovky: výběr dodavatele/profilu; živý průběh s kroky a varováními; výsledky a briefing. Výchozí homepage musí umět ukázat poslední **publikovaný** audit i bez autorizačního kódu.
- Živý audit aktivuje jen oprávněný uživatel; přístupový kód neukládej do URL, localStorage ani logů. Zobraz chybové stavy 401/403/429.
- Polluj stav auditu přes `GET /api/v1/audits/<id>`; aktualizace logů podle `event_id` bez duplicit. Po reloadu otevři historii podle `audit_id`.
- U každého claim zobraz `quote`, `verdict` v češtině, `rule_id`, referenci, čas, `origin` štítek, `human_reviewed`, `judge_changed`, omezení a možné regulatorní riziko.
- U `origin=snapshot` ukaž „Použit uložený snapshot ze dne …“ a odlišení od živého webu; `origin=baseline` ukaž „Web ověřen – tvrzení převzata ze schválené baseline“.
- Nepoužívej `innerHTML` pro text z webu/LLM; `textContent` + bezpečné DOM elementy. Validuj externí odkazy `https://` z backendového source registry.
- Nepřidávej celkové skóre konkurenta. Nabídni matici počtů verdiktů podle typů tvrzení; u prázdných kategorií žádné implicitní záporné známky.
