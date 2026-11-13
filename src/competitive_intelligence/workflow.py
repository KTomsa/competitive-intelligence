"""Jeden ohraničený běh auditu: zdroje → tvrzení → pravidla → briefing. Bez databáze a fronty."""
from __future__ import annotations

import copy
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import agents, insights, rules
from .connectors import eru, ote, pricelist, web
from .connectors.http import FetchError

DATA = Path(__file__).resolve().parent / "data"
EXAMPLE_REPORT = DATA / "example_report.json"
LLM_BUDGET_S = 25  # po této době se briefing složí ze šablony, aby požadavek nepřesáhl limit


class AuditError(Exception):
    pass


def load_config() -> dict:
    return json.loads((DATA / "suppliers.json").read_text(encoding="utf-8"))


def _load_official(loader, format_error, live: bool, warnings: list[str]) -> dict:
    """Živý zdroj; při selhání nebo změně formátu datovaný snapshot s viditelným varováním."""
    try:
        data = loader(live=live)
    except format_error as exc:
        if not live:
            raise AuditError(str(exc)) from exc
        warnings.append(f"Živý zdroj má neočekávaný formát ({exc}); použit uložený snapshot.")
        try:
            data = loader(live=False)
        except (FetchError, format_error) as inner:
            raise AuditError(str(inner)) from inner
    except FetchError as exc:
        raise AuditError(str(exc)) from exc
    if data["source"]["warning"]:
        warnings.append(f"{data['source']['name']}: {data['source']['warning']}")
    data.pop("_fetched", None)
    return data


def _confirm_offer(offer: dict, page: dict | None, price_list_doc: dict | None) -> None:
    """Doplní do nabídky, zda ji živý zdroj potvrzuje (confirmed_live), vyvrací (stale) a jak (confirmation)."""
    price_list = offer.get("price_list") or {}
    if price_list_doc is not None:
        found = pricelist.contains_all(price_list_doc["text"], price_list.get("markers") or [])
        if price_list_doc["source"]["origin"] == "live":
            offer["confirmed_live"], offer["stale"] = found, not found
            offer["confirmation"] = (
                "ceník stažen živě a obsahuje cenu i stálý plat" if found
                else "živě stažený ceník už cenu nebo stálý plat z přípravy neobsahuje"
            )
        else:
            offer["confirmed_live"], offer["stale"] = False, False
            offer["confirmation"] = f"ceník z uloženého snapshotu ({price_list_doc['source']['fetched_at'][:10]})"
        return
    provided = price_list.get("provided_by")
    suffix = f"; stálý plat z ceníku, který dodal {provided}" if provided else ""
    marker = offer.get("page_marker")
    if page is not None and marker:
        on_page = web.contains(page["text"], marker) and web.contains(page["text"], offer["product"])
        if page["source"]["origin"] == "live":
            offer["confirmed_live"], offer["stale"] = on_page, not on_page
            offer["confirmation"] = (
                "cena nalezena na živé stránce dodavatele" if on_page
                else "cena už na živé stránce dodavatele není"
            ) + suffix
        else:
            offer["confirmed_live"], offer["stale"] = False, False
            offer["confirmation"] = "stránka dodavatele z uloženého snapshotu" + suffix
        return
    offer["confirmed_live"], offer["stale"] = False, False
    offer["confirmation"] = f"jen z přípravy: ceník dodal {provided}" if provided else "jen z přípravy"


def _comparison(suppliers: list[dict], ctx: dict) -> list[dict]:
    """Přehled fixovaných nabídek podle délky fixace, seřazený podle ročního nákladu."""
    profile = ctx["profile"]
    groups: dict[int, list[dict]] = {}
    for supplier in suppliers:
        for offer in supplier["offers"]:
            if offer.get("fixation_months") is None or offer.get("stale"):
                continue
            bench = rules.benchmark_offer(offer, ctx)
            price_list = offer.get("price_list") or {}
            groups.setdefault(offer["fixation_months"], []).append({
                "supplier_id": supplier["id"],
                "audience": offer.get("audience"),
                "supplier": supplier["name"],
                "product": offer["product"],
                "price_mwh_excl_vat": offer.get("price_mwh_excl_vat"),
                "price_mwh_incl_vat": offer.get("price_mwh_incl_vat"),
                "monthly_fee_incl_vat": offer.get("monthly_fee_incl_vat"),
                "annual_cost": [rules.annual_cost(offer, mwh) for mwh in profile["annual_mwh"]],
                "position": bench["position"],
                "confirmed_live": offer["confirmed_live"],
                "confirmation": offer["confirmation"],
                "price_list_url": price_list.get("url"),
                "effective_from": price_list.get("effective_from"),
            })
    out = []
    for months in sorted(groups, reverse=True):
        rows = sorted(
            groups[months],
            key=lambda r: (r["annual_cost"][0] is None, r["annual_cost"][0] or 0, r["price_mwh_excl_vat"] or 0),
        )
        out.append({"fixation_months": months, "annual_mwh": profile["annual_mwh"], "rows": rows})
    return out


def run(
    supplier_ids: list[str] | None = None,
    live: bool = True,
    use_llm: bool | None = None,
    perspective: str | None = None,
) -> dict:
    config = load_config()
    suppliers = copy.deepcopy(config["suppliers"])
    known = {s["id"] for s in suppliers}
    selected = [i for i in (supplier_ids or sorted(known)) if i in known]
    if not selected:
        raise AuditError("Nebyl vybrán žádný známý dodavatel.")
    if use_llm is None:
        # běh z uložených dat je záloha pro případ bez internetu, proto bez modelu
        use_llm = live and agents.llm_available()
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    warnings: list[str] = []
    offers = [(s, o) for s in suppliers for o in s["offers"]]

    with ThreadPoolExecutor(max_workers=12) as pool:
        eru_future = pool.submit(_load_official, eru.load, eru.EruFormatError, live, warnings)
        ote_future = pool.submit(_load_official, ote.load, ote.OteFormatError, live, warnings)
        gas_future = pool.submit(_load_official, ote.load_gas, ote.OteFormatError, live, warnings)
        page_futures = {s["id"]: pool.submit(web.load, s, live) for s in suppliers}
        list_futures = {o["id"]: pool.submit(pricelist.load, o, live) for _, o in offers}
        eru_data, ote_data = eru_future.result(), ote_future.result()
        try:
            gas_data = gas_future.result()
        except AuditError as exc:  # plyn je doplňkový zdroj: bez něj audit pokračuje
            gas_data = None
            warnings.append(f"Tabulku OTE pro plyn se nepodařilo načíst: {exc}")
        pages = {}
        for sid, future in page_futures.items():
            try:
                pages[sid] = future.result()
                pages[sid].pop("_fetched", None)
            except FetchError as exc:
                pages[sid] = None
                warnings.append(f"Web dodavatele {sid} se nepodařilo načíst ani ze snapshotu: {exc}")
        price_lists = {}
        for supplier, offer in offers:
            try:
                price_lists[offer["id"]] = list_futures[offer["id"]].result()
            except FetchError as exc:
                price_lists[offer["id"]] = None
                warnings.append(f"{supplier['name']}: ceník „{offer['product']}“ se nepodařilo načíst: {exc}")

    # nabídky: potvrzuje je živý ceník nebo živá stránka?
    price_list_sources = []
    for supplier in suppliers:
        page = pages[supplier["id"]]
        for offer in supplier["offers"]:
            doc = price_lists[offer["id"]]
            _confirm_offer(offer, page, doc)
            offer["captured_at"] = supplier["captured_at"]
            if doc is not None:
                price_list_sources.append({**doc["source"], "supplier": supplier["name"]})
                if doc["source"]["warning"]:
                    warnings.append(f"{supplier['name']} – {doc['source']['name']}: {doc['source']['warning']}")
            if offer["stale"]:
                warnings.append(
                    f"{supplier['name']}: nabídka „{offer['product']}“ zachycená {supplier['captured_at']} "
                    f"už neplatí ({offer['confirmation']}). Do verdiktů se nepočítá, je potřeba ji znovu zachytit."
                )
        for proof in supplier.get("green_evidence") or []:
            marker = proof.get("page_marker")
            if page is None or not marker:
                proof["status"] = "z přípravy"
            elif page["source"]["origin"] != "live":
                proof["status"] = "ze snapshotu stránky"
            elif web.contains(page["text"], marker):
                proof["status"] = "nalezeno na živé stránce"
            else:
                proof["status"], proof["stale"] = "na živé stránce už není", True
        if page and page["source"]["warning"]:
            warnings.append(f"{page['source']['name']}: {page['source']['warning']}")

    current_month = now.strftime("%Y-%m")

    def offer_month(offer: dict) -> str:
        return current_month if offer["confirmed_live"] else offer["captured_at"][:7]

    ctx = {
        "suppliers": suppliers,
        "profile": config["profile"],
        "eru": eru_data,
        "ote": ote_data,
        "ote_gas": gas_data,
        "offer_month": offer_month,
    }

    chosen = [s for s in suppliers if s["id"] in selected and pages[s["id"]] is not None]
    extracted = agents.extract_claims_batch(
        {s["id"]: pages[s["id"]] for s in chosen},
        {s["id"]: [c["quote"] for c in s["claims"]] for s in chosen},
        use_llm,
    )

    def evaluate(supplier: dict, claim: dict) -> dict:
        verdict = rules.evaluate(claim, supplier, ctx)
        item = {
            "id": claim["id"],
            "supplier": supplier["name"],
            "quote": claim["quote"],
            "rule_id": claim["rule_id"],
            "rule_label": rules.RULE_LABELS.get(claim["rule_id"], claim["rule_id"]),
            "source_url": supplier["source_url"],
            "captured_at": supplier["captured_at"],
            "source_status": claim["source_status"],
            "discovered": claim["discovered"],
            "verdict_label": agents.VERDICT_LABEL[verdict["verdict"]],
            **verdict,
        }
        if claim["source_status"] == "changed":
            item["limitation"] += f" Citát už na živé stránce není; byl zachycen {supplier['captured_at']}."
        return item

    # připravená tvrzení všech dodavatelů (pohled firmy potřebuje i ty, které nejsou vybrané k zobrazení)
    evaluated: dict[str, list[dict]] = {}
    for supplier in suppliers:
        page = pages[supplier["id"]]
        items = []
        for claim in supplier["claims"]:
            if page is None:
                status = "prepared_only"
            elif page["source"]["origin"] != "live":
                status = "snapshot"
            elif web.contains(page["text"], claim["quote"]):
                status = "live"
            else:
                status = "changed"
            items.append(evaluate(supplier, {**claim, "source_status": status, "discovered": False}))
        evaluated[supplier["id"]] = items

    report_suppliers, flat = [], []
    for supplier in suppliers:
        if supplier["id"] not in selected:
            continue
        page = pages[supplier["id"]]
        new_claims, extractor_info = extracted.get(
            supplier["id"], ([], {"method": "nespuštěno", "note": None})
        )
        claims_out, candidates = list(evaluated[supplier["id"]]), []
        for n, claim in enumerate(new_claims if page is not None else [], start=1):
            found = {
                **claim, "id": f"{supplier['id']}-n{n}",
                "source_status": page["source"]["origin"], "discovered": True,
            }
            if not extractor_info["method"].startswith("model"):
                # nález podle klíčových slov nikdo nezkontroloval: jen kandidát, bez verdiktu
                candidates.append({
                    "id": found["id"], "supplier": supplier["name"], "quote": found["quote"],
                    "rule_id": found["rule_id"],
                    "rule_label": rules.RULE_LABELS.get(found["rule_id"], found["rule_id"]),
                    "source_url": supplier["source_url"], "captured_at": supplier["captured_at"],
                    "source_status": found["source_status"], "discovered": True,
                })
                continue
            claims_out.append(evaluate(supplier, found))
        flat.extend(claims_out)
        report_suppliers.append({
            "id": supplier["id"],
            "name": supplier["name"],
            "source_url": supplier["source_url"],
            "page": page["source"] if page else None,
            "extractor": extractor_info,
            "offers": [
                {**{k: v for k, v in o.items() if k not in ("page_marker", "price_list")},
                 "price_list": {k: v for k, v in (o.get("price_list") or {}).items() if k != "markers"},
                 "benchmark": {k: v for k, v in rules.benchmark_offer(o, ctx).items() if k != "band"}}
                for o in supplier["offers"]
            ],
            "claims": claims_out,
            "candidates": candidates,
        })

    comparison = _comparison(suppliers, ctx)
    market = insights.market_overview(suppliers, ctx, comparison)
    own = next((s for s in suppliers if s["id"] == perspective), None)
    view = insights.build_perspective(own, ctx, comparison, evaluated, market) if own else None

    all_live = (
        eru_data["source"]["origin"] == "live"
        and ote_data["source"]["origin"] == "live"
        and gas_data is not None and gas_data["source"]["origin"] == "live"
        and all(s["page"] and s["page"]["origin"] == "live" for s in report_suppliers)
        and all(src["origin"] == "live" for src in price_list_sources)
    )
    sources = {"eru": eru_data["source"], "ote": ote_data["source"]}
    if gas_data is not None:
        sources["ote_gas"] = gas_data["source"]
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "mode": "live" if all_live else ("snapshot" if not live else "mixed"),
        "profile": config["profile"],
        "sources": sources,
        "price_lists": price_list_sources,
        "comparison": comparison,
        "market": market,
        "perspective": view,
        "suppliers": report_suppliers,
        "briefing": agents.write_briefing(
            flat, use_llm and time.monotonic() - started < LLM_BUDGET_S
        ),
        "warnings": warnings,
        "llm_used": use_llm,
    }


def main() -> None:
    """python -m competitive_intelligence.workflow [--save-example] [--snapshot] [--perspective=id]"""
    sys.stdout.reconfigure(encoding="utf-8")
    perspective = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--perspective=")), None)
    report = run(live="--snapshot" not in sys.argv, perspective=perspective)
    if "--save-example" in sys.argv:
        report["is_saved_example"] = True
        EXAMPLE_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"uloženo: {EXAMPLE_REPORT}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
