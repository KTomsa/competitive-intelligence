# Validace architektury v2 — Fact or Watt

**Vstup:** „Fact or Watt: architektura v2“ (12. 11. 2026, 10 stran). Revize řeší metodiku, agentnost a nasaditelnost při závazné volbě Flask/Python/HTML/JS. Následující závěry jsou **doporučení revize**, nikoli doslovné tvrzení dodaného dokumentu.

## 1. Celkové hodnocení
**Přijmout s podmínkami.** Oproti v1 verze v2 výrazně zlepšuje návrh: live Web Scout a záložní snapshoty, skutečné tool-calling evidence agenty, uzavřený katalog pravidel, přesná provenance dat, oddělený kódový verdict a Judge s jednosměrným downgradováním. Je vhodná pro školní MAS a CI workflow za předpokladu úprav níže.

## 2. Prioritizované připomínky
| Priorita | Sekce v2 | Problém / riziko | Závazná úprava |
|---|---|---|---|
| **P0** | kap. 8, 12 | Next.js, TypeScript, Zod a nasazení pouze na Vercel neodpovídají technologické volbě uživatele. | Flask + Python + Pydantic v2; HTML/Jinja2/vanilla JS/Bootstrap. |
| **P0** | kap. 6, `customer_count` | Používá OTE počty odběrných míst jako důkaz „počet zákazníků“; tyto metriky nejsou totožné. | K důkazu zákazníků vyžaduj skutečný počet klientů, jinak `unverifiable`. OPM lze ověřit jen jako OPM. |
| **P0** | kap. 6, `price_rank` | Výhra v malém vzorku jako `partially_supported` je zavádějící pro univerzální „nejlevnější v ČR“. | Při neúplném trhu a bez protipříkladu `unverifiable`; validní srovnatelný levnější produkt může `contradicted`. |
| **P1** | kap. 4, baseline | Použít baseline pouze podle hashe bez kontroly metodiky a schválení je riziko starých tvrzení / parser noise. | Hash kanonického textu, shoda URL a verzí parseru, `baseline_approved_at`, `fetch_was_live`, auditní stopa. |
| **P1** | kap. 4, fallback | Snapshot může být neaktuální; „live při každém spuštění“ může chybně znět jako živé tvrzení i při výpadku. | Zdrojový `origin=live/baseline/snapshot`, datum a fallback důvod v UI; data platnosti reference zvlášť. |
| **P1** | kap. 3/5, LLM tools | Volání externích nástrojů přes LLM může provést nepovolený fetch, utratit kredit nebo změnit fakta. | Jen allowlisted read-only ToolRegistry s typovanými parametry, limity, logováním, žádné libovolné URL/SQL/shell. |
| **P1** | kap. 5, Judge | Záporný nebo kladný rule verdict vyžaduje evidenci i kontrolu relevantního scope. | Judge jen downgrade na `unverifiable`, `judge_changed` + důvod; bez zdroje nikdy kladný verdict. |
| **P1** | kap. 6, green | Neúplný mix a chybějící veřejné GO neprokazují nepravdu zeleného produktového tvrzení. | Přísné product/time scope; `unverifiable`, oddělený právní risk flag. |
| **P1** | kap. 7, schémata | V2 uvádí stručné JSON bez verze pravidel, source locatoru a granular provenance. | Pydantic modely, `source_ref`, hash, `valid_as_of`, audit ID, `rule_version`, human review. |
| **P1** | kap. 8, provoz | Serverless funkcí nelze bez další infrastruktury nahradit perzistentní background worker/SQLite. | Python hosting s trvalým diskem; při Vercelu externí DB/queue/storage nebo read-only viewer. |
| **P2** | kap. 9, veřejné spuštění | Pouhé sdílené heslo neřeší brute-force a vyčerpání LLM kreditu. | HTTPS, server-side ověření, rate limit, per-user/per-run cost limity, log bez kódu. |
| **P2** | kap. 10, evaluace | Golden set 20–30 výroků může vést k přehnaným výrokům o „přesnosti“. | Oddělené metriky extrakce, rule klasifikace a verdict accuracy; malé N výslovně uvádět. |

## 3. Co bez změny přebíráme z v2
- KIT/KIQ1–KIQ5 a vazba na CI cyklus (kap. 2–3).
- Orchestrátor, Web Scout, Claim Extractor, Price/Green/Customer, Judge a Strategist jako **oddělené role**, bez volné konverzace (kap. 3).
- Rozdíl mezi živými marketingovými výroky a referenčními daty aktualizovanými méně často (kap. 4).
- LLM vybírá nástroj a strukturu, výpočet a verdict jsou deterministické, Judge může jen degradovat (kap. 5).
- Uzavřený katalog 11 pravidel a čtyři specifikované verdikty (kap. 6).
- Veřejně zobrazený poslední audit, živý audit chráněný kódem, tři obrazovky, bez celkového skóre (kap. 8).
- Praktické priority 3 firmy + cena/ekologie; 5 + Customer jako další stupeň; golden set + transparentní demo (kap. 10–14).

## 4. Metodický výklad kritických pravidel
- **Celotržní superlativ:** první místo v omezeném vzorku není „část pravdy“. Pokud výrok nemá dílčí, samostatně testovatelné části, neuděluj `partially_supported`. `contradicted` vyžaduje validní protipříklad ke stejnému datu, produktu, profilu, cenové složce a relevantnímu trhu.
- **„Zákazníci“:** OTE OPM jsou správnou referencí pro *odběrná místa* a jejich časové změny, nikoli pro počet **unikátních zákazníků**.
- **„100% zelená“:** národní zbytkový mix není produktový důkaz; rozsah produktu a evidence záruk původu rozhodují o možnosti ověření, ne automaticky o pravdivosti výroku.
- **EmpCo:** značka možného regulatorního rizika není automatické konstatování porušení práva; výklad transpozice vyžaduje aktuální ověření.
- **Price cost:** pro pilot lze srovnávat **obchodní složku** D02d, 3×25 A, 2/4 MWh, pokud je jasně řečeno, že nejde o celý účet a dodavatelské nabídky jsou srovnatelné.

## 5. Akceptační testy před označením MVP hotovým
1. Nezměněná živá stránka → validní schválená baseline bez placeného LLM, s `fetch_was_live=true`.
2. Změněná živá stránka → nové doslovné Claims, správné `source_ref`, `human_reviewed=false`, evidence tool calls.
3. HTTP timeout nebo robots blokace → `origin=snapshot`, starší datum a důvod veřejně viditelné; není-li snapshot, source error.
4. Agent vybere tool `get_price_offers` a `calculate_annual_cost` se správným profilem; výpočet `Decimal` v čisté funkci, žádný „modelový odhad ceny“.
5. Judge nikdy nezmění `contradicted → supported` ani `unverifiable → supported`; při downgrade existuje záznam důvodu.
6. „Nejlevnější v ČR“ po srovnání 3/5 firem není podloženo; identický srovnatelný levnější produkt může tvrzení vyvrátit.
7. Výrok o zákaznících při jediném OPM důkazu nedostane `supported`.
8. „Zelená“ jen z národního mixu bez produktového důkazu nedostane `contradicted` ani `supported`.
9. POST živého auditu bez kódu = 401/403; GET publikované výsledky funguje i bez přihlášení.
10. Po restartu web serveru přetrvá audit v DB a lze zobrazit poslední publikovanou verzi.
11. Golden set zahrnuje alespoň 20–30 anotovaných tvrzení; žádná nevymyšlená výkonnostní čísla.

## 6. Závěr
Architekturu v2 **schvaluji jako základ**, avšak s povinnými P0/P1 změnami. Je to stále řízený MAS — agentnost nevzniká z volného chatu, ale ze specializovaných LLM rolí, které samostatně volí validované tool calls. **MVP není možné čestně označit za živé, pokud pouze přehrává fixture/snapshot.** Před demo ověř alespoň jeden skutečný živý zdroj a přiznej omezení dalších.

## Externí odkazy použité k technické validaci
- GitHub instrukce: https://docs.github.com/en/copilot/how-tos/copilot-in-your-ide/customize-copilot/configure-custom-instructions/add-repository-instructions-in-your-ide
- Flask/Gunicorn: https://flask.palletsprojects.com/en/stable/deploying/gunicorn/
- Flask na Vercelu: https://vercel.com/kb/guide/ship-a-flask-app-on-vercel
- Perzistence Vercel functions: https://vercel.com/docs/functions/runtimes
- OTE OPM: https://www.ote-cr.cz/cs/statistika/mesicni-zprava-elektrina/pocty-opm-dodavatelu-v-cs-ote
