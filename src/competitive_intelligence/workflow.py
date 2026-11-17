"""Jeden ohraničený běh auditu: zdroje → tvrzení → pravidla → briefing. Bez databáze a fronty.

Celý běh má jeden časový limit (AUDIT_DEADLINE_S). Všechna stahování z něj čerpají, a když
čas dojde, další zdroj se už nezačíná: použije se datovaný snapshot nebo se zdroj označí jako
chybějící. Z limitu se předem odečte rezerva na vyhodnocení a odeslání odpovědi, takže síť
nesmí spotřebovat celý čas. Seznam očekávaných zdrojů (manifest) určuje, zda je běh opravdu živý.
"""
from __future__ import annotations

import contextvars
import copy
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import agents, evidence, insights, rules
from .connectors import eru, eru_decisions, http, ote, pricelist, web
from .connectors.http import FetchError

DATA = Path(__file__).resolve().parent / "data"
EXAMPLE_REPORT = DATA / "example_report.json"
DEFAULT_DEADLINE_S = 45.0  # pod limitem hostingu (vercel.json: 60 s)
RESPONSE_RESERVE_S = 5.0  # čas na pravidla, briefing a odeslání odpovědi; síť ho nesmí spotřebovat
MIN_LLM_S = 12.0  # s menším zbytkem času se model nevolá


def network_reserve(budget: float) -> float:
    """Rezerva na zpracování odpovědi: 5 s, u krátkých limitů nejvýš pětina limitu."""
    return min(RESPONSE_RESERVE_S, budget * 0.2)


class AuditError(Exception):
    pass


def load_config() -> dict:
    return json.loads((DATA / "suppliers.json").read_text(encoding="utf-8"))


def deadline_seconds() -> float:
    try:
        return max(5.0, float(os.environ.get("AUDIT_DEADLINE_S", DEFAULT_DEADLINE_S)))
    except ValueError:
        return DEFAULT_DEADLINE_S


def _submit(pool: ThreadPoolExecutor, fn, *args):
    """Spustí úlohu ve vlákně se stejným časovým limitem auditu (contextvars se do vláken nekopírují samy)."""
    return pool.submit(contextvars.copy_context().run, fn, *args)


def _load_official(loader, format_error, live: bool, notes: list[str]) -> dict:
    """Živý zdroj; při selhání nebo změně formátu datovaný snapshot s viditelným varováním."""
    try:
        data = loader(live=live)
    except format_error as exc:
        if not live:
            raise AuditError(str(exc)) from exc
        notes.append(f"Živý zdroj má neočekávaný formát ({exc}); použit uložený snapshot.")
        try:
            data = loader(live=False)
        except (FetchError, format_error) as inner:
            raise AuditError(str(inner)) from inner
    except FetchError as exc:
        raise AuditError(str(exc)) from exc
    data.pop("_fetched", None)
    return data


def _confirm_offer(offer: dict, page: dict | None, price_list_doc: dict | None, today) -> None:
    """Doplní, zda nabídku potvrzuje živý zdroj (confirmed_live), vyvrací (stale) a jak (confirmation)."""
    price_list = offer.get("price_list") or {}
    offer["document_status"] = None
    valid_now = evidence.offer_valid_on(offer, today)
    if price_list_doc is not None:
        found = pricelist.contains_all(price_list_doc["text"], price_list.get("markers") or [])
        same = price_list.get("reviewed_sha256") == price_list_doc["source"].get("text_sha256")
        live = price_list_doc["source"]["origin"] == "live"
        offer["stale"] = live and not found
        offer["document_status"] = "unchanged" if same else ("changed" if found else "missing_values")
        offer["confirmed_live"] = live and found and same and valid_now.ok
        if not live:
            offer["confirmation"] = f"ceník z uloženého snapshotu ({price_list_doc['source']['fetched_at'][:10]})"
        elif not found:
            offer["confirmation"] = "živě stažený ceník už hodnoty z přípravy neobsahuje"
        elif not same:
            offer["confirmation"] = (
                "ceník stažen živě, ale od ruční kontroly se změnil; hodnoty jsou nalezeny (kontrola přítomnosti), "
                "nutná nová kontrola"
            )
        elif not valid_now.ok:
            offer["confirmation"] = f"ceník stažen živě, ale dnes ještě neplatí: {valid_now.reason}"
        else:
            offer["confirmation"] = "ceník stažen živě a shoduje se se zkontrolovanou verzí"
        return
    provided = price_list.get("provided_by")
    suffix = f"; stálý plat z ceníku, který dodal {provided}" if provided else ""
    marker = offer.get("page_marker")
    if page is not None and marker:
        on_page = web.contains(page["text"], marker) and web.contains(page["text"], offer["product"])
        if page["source"]["origin"] == "live":
            offer["confirmed_live"] = on_page and valid_now.ok
            offer["stale"] = not on_page
            offer["confirmation"] = (
                "cena nalezena na živé stránce dodavatele (kontrola přítomnosti)" if on_page
                else "cena už na živé stránce dodavatele není"
            ) + suffix
        else:
            offer["confirmed_live"], offer["stale"] = False, False
            offer["confirmation"] = "stránka dodavatele z uloženého snapshotu" + suffix
        return
    offer["confirmed_live"], offer["stale"] = False, False
    offer["confirmation"] = f"jen z přípravy: ceník dodal {provided}" if provided else "jen z přípravy"


def _comparison(suppliers: list[dict], ctx: dict) -> tuple[list[dict], list[dict]]:
    """Přehled nabídek pro nové zákazníky podle délky fixace, seřazený podle ročního nákladu.

    Používá stejné pravidlo oprávněnosti jako verdikty; vyřazené nabídky vrací zvlášť i s důvodem.
    """
    profile = ctx["profile"]
    groups: dict[int, list[dict]] = {}
    excluded = []
    for supplier in suppliers:
        for offer in supplier["offers"]:
            check = evidence.offer_eligibility(offer, ctx, need_fees=True, audience="new", current=True)
            if not check.ok:
                excluded.append({"supplier": supplier["name"], "product": offer["product"], "reason": check.reason})
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
                "period": check.period,
                "confirmed_live": offer["confirmed_live"],
                "confirmation": offer["confirmation"],
                "price_list_url": price_list.get("url"),
                "row_locator": price_list.get("row_locator"),
                "effective_from": price_list.get("effective_from"),
            })
    out = []
    for months in sorted(groups, reverse=True):
        rows = sorted(groups[months], key=lambda r: (r["annual_cost"][0], r["price_mwh_excl_vat"]))
        out.append({"fixation_months": months, "annual_mwh": profile["annual_mwh"], "rows": rows})
    return out, excluded


def _manifest_entry(entry_id, label, kind, result, url=None, required=True, note=None) -> dict:
    """Stav jednoho očekávaného zdroje: live | snapshot | missing | not_required."""
    if not required:
        state = "not_required"
    elif result is None:
        state = "missing"
    else:
        state = result["source"]["origin"]
    source = (result or {}).get("source") or {}
    return {
        "id": entry_id, "label": label, "kind": kind, "state": state,
        "fetched_at": source.get("fetched_at"), "url": source.get("page_url") or source.get("source_url") or url,
        "note": note or source.get("warning"),
    }


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
    budget = deadline_seconds()
    token = http.DEADLINE.set(started + budget - network_reserve(budget))  # síť a model končí dřív
    try:
        return _run(config, suppliers, selected, live, use_llm, perspective, started, budget)
    finally:
        http.DEADLINE.reset(token)


def _run(config, suppliers, selected, live, use_llm, perspective, started, budget) -> dict:
    now = datetime.now(timezone.utc)
    today = now.date()
    notes: list[str] = []
    offers = [(s, o) for s in suppliers for o in s["offers"]]

    with ThreadPoolExecutor(max_workers=12) as pool:
        eru_future = _submit(pool, _load_official, eru.load, eru.EruFormatError, live, notes)
        ote_future = _submit(pool, _load_official, ote.load, ote.OteFormatError, live, notes)
        gas_future = _submit(pool, _load_official, ote.load_gas, ote.OteFormatError, live, notes)
        page_futures = {s["id"]: _submit(pool, web.load, s, live) for s in suppliers}
        list_futures = {o["id"]: _submit(pool, pricelist.load, o, live) for _, o in offers}
        decision_futures = {s["id"]: _submit(pool, eru_decisions.load, s, live) for s in suppliers}
        eru_data, ote_data = eru_future.result(), ote_future.result()
        try:
            gas_data = gas_future.result()
        except AuditError as exc:  # plyn je doplňkový zdroj: bez něj audit pokračuje
            gas_data = None
            notes.append(f"Tabulku OTE pro plyn se nepodařilo načíst: {exc}")

        def collect(futures: dict) -> dict:
            out = {}
            for key, future in futures.items():
                try:
                    value = future.result()
                except FetchError as exc:
                    value = None
                    notes.append(f"Zdroj {key} se nepodařilo načíst ani ze snapshotu: {exc}")
                if value is not None:
                    value.pop("_fetched", None)
                out[key] = value
            return out

        pages = collect(page_futures)
        price_lists = collect(list_futures)
        decisions = collect(decision_futures)

    # nabídky: potvrzuje je živý ceník nebo živá stránka?
    price_list_sources = []
    for supplier in suppliers:
        page = pages[supplier["id"]]
        for offer in supplier["offers"]:
            offer["captured_at"] = supplier["captured_at"]
            doc = price_lists[offer["id"]]
            _confirm_offer(offer, page, doc, today)
            if doc is not None:
                price_list_sources.append({**doc["source"], "supplier": supplier["name"]})
            if live and offer["stale"]:
                notes.append(
                    f"{supplier['name']}: nabídka „{offer['product']}“ zachycená {supplier['captured_at']} "
                    f"už neplatí ({offer['confirmation']}). Do verdiktů se nepočítá, je potřeba ji znovu zachytit."
                )
            elif live and offer.get("document_status") == "changed":
                notes.append(f"{supplier['name']}: ceník „{offer['product']}“ se od ruční kontroly změnil; "
                             "hodnoty z přípravy se používají jako nepotvrzené.")
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

    ctx = {
        "suppliers": suppliers,
        "profile": config["profile"],
        "eru": eru_data,
        "ote": ote_data,
        "ote_gas": gas_data,
        "decisions": decisions,
        "definitions": config.get("definitions") or {},
        "audit_date": today,
    }

    chosen = [s for s in suppliers if s["id"] in selected and pages[s["id"]] is not None]
    left = http.remaining()
    llm_extract = use_llm and (left is None or left >= MIN_LLM_S)
    if use_llm and not llm_extract:
        notes.append("Na hledání dalších tvrzení modelem nezbyl čas; použita klíčová slova.")
    extracted = agents.extract_claims_batch(
        {s["id"]: pages[s["id"]] for s in chosen},
        {s["id"]: [c["quote"] for c in s["claims"]] for s in chosen},
        llm_extract,
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
        decision_data = decisions[supplier["id"]] or {"decisions": [], "truncated": False}
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
            "decisions": [{k: d[k] for k in ("date", "title", "url", "kind")} for d in decision_data["decisions"]],
            "decisions_truncated": decision_data.get("truncated", False),
            "decisions_missing": decisions[supplier["id"]] is None,
        })

    comparison, excluded = _comparison(suppliers, ctx)
    market = insights.market_overview(suppliers, ctx, comparison)
    own = next((s for s in suppliers if s["id"] == perspective), None)
    view = insights.build_perspective(own, ctx, comparison, evaluated, market) if own else None

    # manifest: každý očekávaný zdroj a jeho stav
    manifest = [
        _manifest_entry("eru", "ERÚ – indikativní pásma cen elektřiny", "official", eru_data),
        _manifest_entry("ote", "OTE – počty OPM elektřiny", "official", ote_data),
        _manifest_entry("ote_gas", "OTE – počty OPM plynu", "official", gas_data, url=ote.COMMODITIES["gas"]["url"]),
    ]
    for supplier in suppliers:
        manifest.append(_manifest_entry(f"web:{supplier['id']}", f"Web dodavatele {supplier['name']}", "page",
                                        pages[supplier["id"]], url=supplier["source_url"]))
        manifest.append(_manifest_entry(f"decisions:{supplier['id']}", f"ERÚ – rozhodnutí: {supplier['name']}",
                                        "decisions", decisions[supplier["id"]], url=eru_decisions.LIST_URL))
        for offer in supplier["offers"]:
            price_list = offer.get("price_list") or {}
            has_url = bool(price_list.get("url"))
            manifest.append(_manifest_entry(
                f"pricelist:{offer['id']}", f"{supplier['name']} – ceník {offer['product']}", "price_list",
                price_lists[offer["id"]], url=price_list.get("url"), required=has_url,
                note=None if has_url else "Ceník dodaný týmem a ručně zkontrolovaný; automaticky ho stáhnout nelze.",
            ))
    states = [m["state"] for m in manifest if m["state"] != "not_required"]
    counts = {state: states.count(state) for state in ("live", "snapshot", "missing")}
    if live and all(state == "live" for state in states):
        mode = "live"
    elif not live:
        mode = "snapshot"
    else:
        mode = "mixed"
    warnings = list(notes)
    if live:
        for entry in manifest:
            if entry["state"] == "snapshot":
                warnings.append(f"{entry['label']}: živé stažení selhalo, použit snapshot z {entry['fetched_at']}.")
            elif entry["state"] == "missing":
                warnings.append(f"{entry['label']}: zdroj chybí (ani snapshot není k dispozici).")
    elif counts["missing"]:
        warnings.append(f"{counts['missing']} zdrojů chybí i v uložených datech.")

    sources = {"eru": eru_data["source"], "ote": ote_data["source"]}
    if gas_data is not None:
        sources["ote_gas"] = gas_data["source"]
    decision_sources = [d["source"] for d in decisions.values() if d]
    if decision_sources:
        sources["eru_decisions"] = {
            "name": "ERÚ – pravomocná rozhodnutí (podle účastníka řízení)",
            "page_url": eru_decisions.LIST_URL,
            "source_url": eru_decisions.LIST_URL,
            "origin": "live" if all(src["origin"] == "live" for src in decision_sources) else "snapshot",
            "fetched_at": max(src["fetched_at"] for src in decision_sources),
            "warning": None,
            "unit": "název, datum zveřejnění a štítky rozhodnutí; obsah rozhodnutí se nečte",
        }

    left = http.remaining()
    briefing = agents.write_briefing(flat, use_llm and (left is None or left >= MIN_LLM_S))
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "mode": mode,
        "coverage": {**counts, "expected": len(states)},
        "manifest": manifest,
        "duration_s": round(time.monotonic() - started, 1),
        "deadline_s": budget,
        "profile": config["profile"],
        "definitions": config.get("definitions") or {},
        "sources": sources,
        "price_lists": price_list_sources,
        "comparison": comparison,
        "comparison_date": evidence.comparison_date(ctx).isoformat(),
        "comparison_excluded": excluded,
        "market": market,
        "perspective": view,
        "suppliers": report_suppliers,
        "briefing": briefing,
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
