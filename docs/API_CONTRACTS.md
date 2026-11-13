# API kontrakty a Pydantic schémata — verze 2

Normativní návrh pro implementaci. Příkladové údaje jsou **fiktivní**, nikoli konkrétní zjištění o českých dodavatelích. Kódové názvy anglicky, UI česky; datové časy UTC s offsetem, `valid_as_of` samostatně. Pydantic modely musí zakazovat nečekaná pole v kontraktech přes `ConfigDict(extra='forbid')` tam, kde je to bezpečné.

## 1. Vytvoření a stav auditu
`POST /api/v1/audits` — **autorizováno**, `202 Accepted`:
```json
{
  "supplier_ids": ["supplier_demo_a", "supplier_demo_b", "supplier_demo_c"],
  "profile_ids": ["d02d_2mwh", "d02d_4mwh"],
  "commodity": "electricity",
  "mode": "live"
}
```
Response:
```json
{
  "audit_id": "audit_demo_001",
  "status": "queued",
  "status_url": "/api/v1/audits/audit_demo_001",
  "results_url": "/api/v1/audits/audit_demo_001/results"
}
```

`GET /api/v1/audits/{audit_id}` — `200`, perzistentní stav:
```json
{
  "audit_id": "audit_demo_001",
  "status": "verifying",
  "stage": "green_agent",
  "progress_percent": 62,
  "updated_at": "2026-11-12T14:15:00Z",
  "events": [
    {"event_id": 8, "timestamp": "2026-11-12T14:14:58Z", "stage": "green_agent", "level": "info", "message": "Hledám údaje k původu elektřiny."}
  ],
  "warnings": ["Jeden zdroj byl nedostupný; používá se starší snapshot."],
  "error": null
}
```
`GET /api/v1/audits/{audit_id}/results` — `200` po dokončení, jinak `409 AUDIT_NOT_READY` nebo `404`.
`GET /api/v1/audits/latest` — veřejně dostupný poslední **publikovaný**/sanitizovaný audit; bez citlivých logů a API klíčů. Při absenci publikovaného auditu `404 NOT_PUBLISHED`.

Status: `queued | collecting | extracting | verifying | judging | reporting | completed | failed`. `progress_percent` je orientační, události mají sekvenční ID. Worker checkpointy nejsou uložené v session prohlížeče.

## 2. Zdroj a Claims
Povinné pole `source_ref` lokalizuje citát/údaj (CSS selektor, identifikátor odstavce či stránka PDF). `origin` patří ke konkrétnímu zdroji/claimu; audit může míchat zdroje s různým původem.

```json
{
  "source_id": "src_demo_home_001",
  "supplier_id": "supplier_demo_a",
  "source_url": "https://example.org/demo",
  "origin": "snapshot",
  "fetch_was_live": false,
  "captured_at": "2026-11-11T12:00:00Z",
  "checked_at": "2026-11-12T14:13:00Z",
  "valid_as_of": "2026-11-11",
  "content_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "canonical_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "parser_version": "html-v1",
  "fallback_reason": "HTTP timeout",
  "baseline_approved_at": null
}
```

Claim (výstup Extractoru nebo schválené baseline):
```json
{
  "id": "claim_demo_001",
  "supplier_id": "supplier_demo_a",
  "quote": "Nejlevnější elektřina v ČR",
  "source_id": "src_demo_home_001",
  "source_url": "https://example.org/demo",
  "source_ref": "main > h1",
  "captured_at": "2026-11-11T12:00:00Z",
  "origin": "snapshot",
  "human_reviewed": false,
  "product_id": null,
  "claim_type": "price",
  "rule": {"id": "price_rank", "params": {"scope": "cz_market", "rank": 1}}
}
```
Kód **musí** ověřit, že `quote` je v normalizovaném kanonickém textu odpovídajícího snapshotu. `rule.id` jen z katalogu. `params` validovat zvlášť pro každý rule ID.

## 3. Evidence, deterministické pravidlo, Judge
`Evidence` agent **vybírá a vyžádá** dostupné skutečnosti pomocí tool call; výpočet je deterministický.

```json
{
  "claim_id": "claim_demo_001",
  "facts": [
    {
      "evidence_id": "ev_demo_001",
      "statement": "Srovnání pouze tří dodavatelů pro profil D02d, 2 MWh",
      "value": null,
      "unit": null,
      "source_url": "https://example.org/prices",
      "source_ref": "table:2",
      "captured_at": "2026-11-10T09:00:00Z",
      "valid_as_of": "2026-11-12",
      "source_content_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "scope": "3 comparable suppliers, D02d 2 MWh",
      "limitations": ["Jde pouze o vybraný vzorek."]
    }
  ],
  "rule_result": "unverifiable",
  "rule_version": "price_rank-v2",
  "limitations": ["Nelze potvrdit superlativ pro celou ČR ze tří nabídek."]
}
```

Verdict (výstup Judge po kódovém pravidlu):
```json
{
  "claim_id": "claim_demo_001",
  "rule_verdict": "unverifiable",
  "verdict": "unverifiable",
  "judge_changed": false,
  "judge_reason": null,
  "evidence_ids": ["ev_demo_001"],
  "reasoning": "Srovnávací soubor nepokrývá celý český trh.",
  "limitations": ["Kontrola omezeného vzorku."],
  "possible_regulatory_risk": false,
  "risk_reason": null,
  "review_required": false
}
```
Invariant: `verdict == rule_verdict OR (verdict == 'unverifiable' AND judge_changed == true)`; pokud `judge_changed=true`, musí být `judge_reason`. `possible_regulatory_risk` není verdict. `human_reviewed` je samostatný stav.

## 4. Tool-calling rozhraní (evidence agenti)
Povolené a schválené **read-only** server-side nástroje; LLM dostává deklarované parametry, ne přímou možnost síťového přístupu. Každý výsledek obsahuje `source_id`, `scope`, datum a hash nebo explicitní chybu.

| Tool | Input | Vracený typ | Použití |
|---|---|---|---|
| `get_price_offers` | `supplier_ids`, `profile_id`, `as_of_date` | seznam cenových produktů s podmínkami a source IDs | Price agent |
| `calculate_annual_cost` | `offer_id`, `profile_id`, `component` | `Decimal` částka + breakdown + zdroj | Price agent (kódový nástroj) |
| `get_product_terms` | `supplier_id`, `product_id`, `as_of_date` | typované klauzule fixace a výluk | Price agent |
| `get_fuel_mix` | `supplier_id`, `product_id`, `as_of_date` | mix a dokumentová evidence | Green agent |
| `get_guarantees_of_origin` | `supplier_id`, `product_id`, `period` | doložené vazby/limity, případně unavailable | Green agent |
| `get_ote_opm` | `supplier_id`, `period` | počty OPM (nikoli zákazníků) | Customer agent |
| `get_eru_decisions` | `company_id`, `since` | relevantní vydaná rozhodnutí | Customer agent |

Registry `ToolRegistry` používá explicitní mapu funkcí; validuje vstupy a počty volání, nikdy neprovádí libovolné URL/fetch/SQL příkazy podle textu LLM. Provider adapter převádí LLM tool-calls na interní Python funkce a eviduje audit trail.

## 5. Autorizace, chyby a původ
- POST live/fixture auditu chránit `AUDIT_ACCESS_CODE` v server env. Při jednoduchém MVP lze přijmout kód v `X-Audit-Access-Code` **přes HTTPS**; kód se nesmí objevit v URL ani být uložen ve výsledku/logu. Pro veřejný internet přidej rate limiting a bezpečné porovnání; u session auth CSRF.
- Error JSON: `{"error": {"code": "SOURCE_UNAVAILABLE", "message": "...", "details": {}}}`. Žádné stacktraces ani LLM systémové prompty ve veřejné odpovědi.
- Výsledek musí mít `mode`, `scope_note`, `sources` s origin/fallback daty, `claims`, `evidence`, `verdicts`, `briefing` a `published_at` u publikovaného auditu.
- Všechny veřejné výsledky důsledně oddělují **testovací/fiktivní** data od reálných dat.
