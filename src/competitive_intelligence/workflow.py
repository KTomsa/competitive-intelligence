"""Jeden ohraničený běh auditu: zdroje → tvrzení → pravidla → briefing. Bez databáze a fronty."""
from __future__ import annotations

import copy
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import agents, rules
from .connectors import eru, ote, web
from .connectors.http import FetchError

DATA = Path(__file__).resolve().parent / "data"
EXAMPLE_REPORT = DATA / "example_report.json"


class AuditError(Exception):
    pass


def load_config() -> dict:
    return json.loads((DATA / "suppliers.json").read_text(encoding="utf-8"))


def _load_official(connector, format_error, live: bool, warnings: list[str]) -> dict:
    """Živý zdroj; při selhání nebo změně formátu datovaný snapshot s viditelným varováním."""
    try:
        data = connector.load(live=live)
    except format_error as exc:
        if not live:
            raise AuditError(str(exc)) from exc
        warnings.append(f"Živý zdroj má neočekávaný formát ({exc}); použit uložený snapshot.")
        try:
            data = connector.load(live=False)
        except (FetchError, format_error) as inner:
            raise AuditError(str(inner)) from inner
    except FetchError as exc:
        raise AuditError(str(exc)) from exc
    if data["source"]["warning"]:
        warnings.append(f"{data['source']['name']}: {data['source']['warning']}")
    data.pop("_fetched", None)
    return data


def run(supplier_ids: list[str] | None = None, live: bool = True, use_llm: bool | None = None) -> dict:
    config = load_config()
    suppliers = copy.deepcopy(config["suppliers"])
    known = {s["id"] for s in suppliers}
    selected = [i for i in (supplier_ids or sorted(known)) if i in known]
    if not selected:
        raise AuditError("Nebyl vybrán žádný známý dodavatel.")
    if use_llm is None:
        use_llm = agents.llm_available()
    now = datetime.now(timezone.utc)
    warnings: list[str] = []

    with ThreadPoolExecutor(max_workers=6) as pool:
        eru_future = pool.submit(_load_official, eru, eru.EruFormatError, live, warnings)
        ote_future = pool.submit(_load_official, ote, ote.OteFormatError, live, warnings)
        page_futures = {s["id"]: pool.submit(web.load, s, live) for s in suppliers}
        eru_data, ote_data = eru_future.result(), ote_future.result()
        pages = {}
        for sid, future in page_futures.items():
            try:
                pages[sid] = future.result()
                pages[sid].pop("_fetched", None)
            except FetchError as exc:
                pages[sid] = None
                warnings.append(f"Web dodavatele {sid} se nepodařilo načíst ani ze snapshotu: {exc}")

    # nabídky: je cena z přípravy stále na stránce?
    for supplier in suppliers:
        page = pages[supplier["id"]]
        for offer in supplier["offers"]:
            offer["confirmed_live"] = bool(
                page and page["source"]["origin"] == "live" and web.contains(page["text"], offer["page_marker"])
            )
            offer["captured_at"] = supplier["captured_at"]
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
        "offer_month": offer_month,
    }

    chosen = [s for s in suppliers if s["id"] in selected and pages[s["id"]] is not None]
    extracted = agents.extract_claims_batch(
        {s["id"]: pages[s["id"]] for s in chosen},
        {s["id"]: [c["quote"] for c in s["claims"]] for s in chosen},
        use_llm,
    )

    report_suppliers, flat = [], []
    for supplier in suppliers:
        if supplier["id"] not in selected:
            continue
        page = pages[supplier["id"]]
        claims_in = []
        for claim in supplier["claims"]:
            if page is None:
                status = "prepared_only"
            elif page["source"]["origin"] != "live":
                status = "snapshot"
            elif web.contains(page["text"], claim["quote"]):
                status = "live"
            else:
                status = "changed"
            claims_in.append({**claim, "source_status": status, "discovered": False})
        new_claims, extractor_info = extracted.get(
            supplier["id"], ([], {"method": "nespuštěno", "note": None})
        )
        if page is not None:
            for n, claim in enumerate(new_claims, start=1):
                claims_in.append({
                    **claim, "id": f"{supplier['id']}-n{n}",
                    "source_status": page["source"]["origin"], "discovered": True,
                })
        claims_out, candidates = [], []
        for claim in claims_in:
            base = {
                "id": claim["id"],
                "supplier": supplier["name"],
                "quote": claim["quote"],
                "rule_id": claim["rule_id"],
                "rule_label": rules.RULE_LABELS.get(claim["rule_id"], claim["rule_id"]),
                "source_url": supplier["source_url"],
                "captured_at": supplier["captured_at"],
                "source_status": claim["source_status"],
                "discovered": claim["discovered"],
            }
            if claim["discovered"] and not extractor_info["method"].startswith("model"):
                # nález podle klíčových slov nikdo nezkontroloval: jen kandidát, bez verdiktu
                candidates.append(base)
                continue
            verdict = rules.evaluate(claim, supplier, ctx)
            item = {**base, "verdict_label": agents.VERDICT_LABEL[verdict["verdict"]], **verdict}
            if claim["source_status"] == "changed":
                item["limitation"] += (
                    f" Citát už na živé stránce není; byl zachycen {supplier['captured_at']}."
                )
            claims_out.append(item)
            flat.append(item)
        report_suppliers.append({
            "id": supplier["id"],
            "name": supplier["name"],
            "source_url": supplier["source_url"],
            "page": page["source"] if page else None,
            "extractor": extractor_info,
            "offers": [
                {**{k: v for k, v in o.items() if k != "page_marker"},
                 "benchmark": {k: v for k, v in rules.benchmark_offer(o, ctx).items() if k != "band"}}
                for o in supplier["offers"]
            ],
            "claims": claims_out,
            "candidates": candidates,
        })

    all_live = (
        eru_data["source"]["origin"] == "live"
        and ote_data["source"]["origin"] == "live"
        and all(s["page"] and s["page"]["origin"] == "live" for s in report_suppliers)
    )
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "mode": "live" if all_live else ("snapshot" if not live else "mixed"),
        "profile": config["profile"],
        "sources": {"eru": eru_data["source"], "ote": ote_data["source"]},
        "suppliers": report_suppliers,
        "briefing": agents.write_briefing(flat, use_llm),
        "warnings": warnings,
        "llm_used": use_llm,
    }


def main() -> None:
    """python -m competitive_intelligence.workflow [--save-example] [--snapshot]"""
    sys.stdout.reconfigure(encoding="utf-8")
    report = run(live="--snapshot" not in sys.argv)
    if "--save-example" in sys.argv:
        report["is_saved_example"] = True
        EXAMPLE_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"uloženo: {EXAMPLE_REPORT}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
