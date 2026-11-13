"""Přehled trhu a pohled jedné firmy.

Vše se skládá z údajů, které audit už má: roční náklad z ceníků, pozice vůči pásmu ERÚ,
počty odběrných míst z OTE a vyhodnocená tvrzení. Žádná čísla nedoplňuje jazykový model.
"""
from __future__ import annotations

import re

from . import rules
from .connectors import ote

OPM_CAVEAT = "Odběrná místa nejsou zákazníci; změna může zahrnovat i převzetí jiného dodavatele."
MIN_SAVING_CZK = 100
PRICE_CAVEAT = (
    "Obchodní část ceny (komodita a stálý plat) pro srovnávací sazbu a území; "
    "před použitím znovu ověřit platnost obou ceníků."
)


def pct_text(value: float) -> str:
    return f"{value:+.1f}".replace(".", ",") + " %"


def _series(table_data: dict | None, legal_name: str) -> dict | None:
    """Změna počtu OPM dodavatele mezi prvním a posledním zveřejněným měsícem roku."""
    if not table_data:
        return None
    table = table_data["table"]
    for name, values in table["suppliers"].items():
        if not ote.same_supplier(name, legal_name):
            continue
        filled = [(i + 1, v) for i, v in enumerate(values) if v is not None]
        if len(filled) < 2 or not filled[0][1]:
            return None
        (m0, first), (m1, last) = filled[0], filled[-1]
        year = table.get("year") or "rok"
        return {
            "first": first, "last": last, "abs": last - first,
            "pct": round((last - first) / first * 100, 1),
            "from": f"{year}-{m0:02d}", "to": f"{year}-{m1:02d}",
        }
    return None


def _market_change(table_data: dict | None) -> float | None:
    if not table_data:
        return None
    first = last = 0
    for values in table_data["table"]["suppliers"].values():
        filled = [v for v in values if v is not None]
        if len(filled) >= 2:
            first, last = first + filled[0], last + filled[-1]
    return round((last - first) / first * 100, 1) if first else None


def _best_by_supplier(group: dict) -> list[dict]:
    """Nejlevnější nabídka každého dodavatele v dané délce fixace, seřazeno od nejlevnější."""
    best: dict[str, dict] = {}
    for row in group["rows"]:  # řádky jsou už seřazené podle ročního nákladu
        if all(c is not None for c in row["annual_cost"]):
            best.setdefault(row["supplier_id"], row)
    return list(best.values())


def market_overview(suppliers: list[dict], ctx: dict, comparison: list[dict]) -> dict:
    """Cena a růst vedle sebe: kdo získává odběrná místa a za jakou cenu."""
    longest = comparison[0] if comparison else None
    rows = []
    for supplier in suppliers:
        price = None
        for group in comparison:  # od nejdelší fixace: první, kterou dodavatel nabízí
            ranked = _best_by_supplier(group)
            row = next((r for r in ranked if r["supplier_id"] == supplier["id"]), None)
            if row is not None:
                price = {"fixation_months": group["fixation_months"], "rank": ranked.index(row) + 1,
                         "of": len(ranked), "product": row["product"],
                         "annual_cost": row["annual_cost"][0], "position": row["position"]}
                break
        rows.append({
            "id": supplier["id"], "name": supplier["name"],
            "electricity": _series(ctx["ote"], supplier["ote_name"]),
            "gas": _series(ctx.get("ote_gas"), supplier["ote_name"]),
            "price": price,
        })
    rows.sort(key=lambda r: (r["electricity"] is None, -(r["electricity"] or {"pct": 0})["pct"]))
    period = next((r["electricity"] for r in rows if r["electricity"]), None)

    observations = []
    priced = sorted(
        (r for r in rows if r["price"] and r["electricity"]
         and r["price"]["fixation_months"] == longest["fixation_months"]),
        key=lambda r: r["price"]["rank"],
    ) if longest else []
    if len(priced) >= 2:
        cheapest, priciest = priced[0], priced[-1]
        if cheapest["electricity"]["abs"] < 0:
            observations.append(
                f"Nejlevnější nabídku ve vzorku má {cheapest['name']}, přesto odběrná místa elektřiny ztrácí "
                f"({rules.num(cheapest['electricity']['abs'])}, {pct_text(cheapest['electricity']['pct'])})."
            )
        if priciest["electricity"]["abs"] > 0:
            observations.append(
                f"Nejdražší nabídku ve vzorku má {priciest['name']}, přesto roste "
                f"({pct_text(priciest['electricity']['pct'])})."
            )
    for group in comparison[1:]:  # stejný jev u kratších fixací
        ranked = _best_by_supplier(group)
        if len(ranked) < 2:
            continue
        leader = next((r for r in rows if r["id"] == ranked[0]["supplier_id"]), None)
        if leader and leader["electricity"] and leader["electricity"]["abs"] < 0 and not any(
            leader["name"] in text for text in observations
        ):
            observations.append(
                f"U fixace na {group['fixation_months']} měsíců je nejlevnější {leader['name']} a také ztrácí "
                f"({rules.num(leader['electricity']['abs'])}, {pct_text(leader['electricity']['pct'])})."
            )
    if len(observations) >= 2:
        observations.append("Nejnižší cena tedy sama o sobě zákazníky nepřivádí.")
    gaps = loyalty_gaps(suppliers, ctx["profile"])
    return {
        "loyalty_gaps": gaps,
        "loyalty_caveat": (
            "Srovnává se cena komodity s DPH u produktů se stejnou délkou fixace; produkty se mohou lišit "
            "podmínkami a službami. Údaj, komu je produkt určen, pochází ze stránek dodavatele, ne z ceníku."
        ),
        "fixation_months": longest["fixation_months"] if longest else None,
        "annual_mwh": longest["annual_mwh"][0] if longest else None,
        "period": {"from": period["from"], "to": period["to"]} if period else None,
        "market_electricity_pct": _market_change(ctx["ote"]),
        "observations": observations,
        "rows": rows,
        "caveat": (
            OPM_CAVEAT + " Jde o souběh dvou údajů, ne o důkaz příčiny. Ceny jsou nabídky pro nové zákazníky "
            "v jedné sazbě a jednom distribučním území."
        ),
    }


def loyalty_gaps(suppliers: list[dict], profile: dict) -> list[dict]:
    """O kolik víc platí stávající zákazník než nový u téhož dodavatele a stejné délky fixace."""
    gaps = []
    for supplier in suppliers:
        source = supplier.get("audience_source") or {}
        by_months: dict[int, dict[str, dict]] = {}
        for offer in supplier["offers"]:
            if (
                offer.get("stale") or offer.get("stepped") or offer.get("fixation_months") is None
                or offer.get("audience") not in ("new", "existing")
                or offer.get("price_mwh_incl_vat") is None or offer.get("tariff") != profile["tariff"]
            ):
                continue
            slot = by_months.setdefault(offer["fixation_months"], {})
            current = slot.get(offer["audience"])
            if current is None or offer["price_mwh_incl_vat"] < current["price_mwh_incl_vat"]:
                slot[offer["audience"]] = offer
        for months in sorted(by_months, reverse=True):
            new, existing = by_months[months].get("new"), by_months[months].get("existing")
            if not new or not existing:
                continue
            diff = existing["price_mwh_incl_vat"] - new["price_mwh_incl_vat"]
            if diff <= 0:
                continue
            costs_new = [rules.annual_cost(new, m) for m in profile["annual_mwh"]]
            costs_old = [rules.annual_cost(existing, m) for m in profile["annual_mwh"]]
            annual = [
                round(b - a, 2) if a is not None and b is not None else None
                for a, b in zip(costs_new, costs_old)
            ]
            gaps.append({
                "supplier_id": supplier["id"], "supplier": supplier["name"], "fixation_months": months,
                "new_product": new["product"], "new_price": new["price_mwh_incl_vat"],
                "existing_product": existing["product"], "existing_price": existing["price_mwh_incl_vat"],
                "diff_mwh": round(diff, 2), "pct": round(diff / new["price_mwh_incl_vat"] * 100, 1),
                "annual_diff": annual, "annual_mwh": profile["annual_mwh"],
                "confirmed_live": bool(new.get("confirmed_live") and existing.get("confirmed_live")),
                "inferred": bool(source.get("inferred")), "source_note": source.get("note"),
                "source_url": source.get("url"),
            })
    return gaps


def _gap_text(gap: dict) -> str:
    annual = " až ".join(rules.czk(v) for v in gap["annual_diff"] if v is not None)
    text = (
        f"{gap['supplier']}, fixace {gap['fixation_months']} měs.: nový zákazník {rules.czk(gap['new_price'])}/MWh "
        f"({gap['new_product']}), stávající {rules.czk(gap['existing_price'])}/MWh ({gap['existing_product']}), "
        f"tedy o {str(gap['pct']).replace('.', ',')} % víc"
    )
    return text + (f", ročně o {annual}." if annual else ".")


def _mentions(text: str, supplier: dict) -> bool:
    for name in (supplier["name"], supplier.get("ote_name") or ""):
        if name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text):
            return True
    return False


def build_perspective(own: dict, ctx: dict, comparison: list[dict], evaluated: dict, market: dict) -> dict:
    """Čtyři odpovědi pro manažera jedné firmy: cena, vlastní rizika, doložitelná tvrzení, konkurence."""
    profile = ctx["profile"]
    mwh = profile["annual_mwh"]

    # 1. kde stojíme cenou
    price = []
    for group in comparison:
        ranked = _best_by_supplier(group)
        mine = next((r for r in ranked if r["supplier_id"] == own["id"]), None)
        if mine is None:
            continue
        cheaper = []
        for other in ranked:
            if other is mine:
                continue
            diffs = [a - b for a, b in zip(mine["annual_cost"], other["annual_cost"])]
            if all(d > 0 for d in diffs):
                cheaper.append({"supplier": other["supplier"], "product": other["product"], "saving": diffs})
        price.append({
            "fixation_months": group["fixation_months"], "product": mine["product"],
            "annual_cost": mine["annual_cost"], "annual_mwh": mwh,
            "rank": ranked.index(mine) + 1, "of": len(ranked),
            "position": mine["position"], "cheaper": cheaper,
        })

    # 2. kde je naše vlastní reklama napadnutelná
    risky, solid = [], []
    for claim in evaluated.get(own["id"], []):
        entry = {k: claim[k] for k in ("id", "quote", "verdict", "verdict_label", "finding", "limitation")}
        if claim["verdict"] == rules.CONTRADICTED or claim["opportunity"]:
            risky.append(entry)
        elif claim["verdict"] in (rules.SUPPORTED, rules.PARTIAL):
            solid.append(entry)

    # 3. co můžeme tvrdit a doložit
    provable, cautions = [], []
    market_pct = market.get("market_electricity_pct")
    for data, label, source in (
        (ctx["ote"], "elektřiny", "OTE – počty OPM dodavatelů elektřiny"),
        (ctx.get("ote_gas"), "plynu", "OTE – počty OPM dodavatelů plynu"),
    ):
        change = _series(data, own["ote_name"])
        if change is None:
            continue
        span = f"od {change['from']} do {change['to']}"
        if change["abs"] > 0:
            text = (
                f"Rosteme: počet našich odběrných míst {label} vzrostl {span} o {rules.num(change['abs'])} "
                f"({pct_text(change['pct'])})."
            )
            if label == "elektřiny" and market_pct is not None and change["pct"] > market_pct:
                text += f" Trh jako celek se změnil o {pct_text(market_pct)}."
            provable.append({"text": text, "source": source, "caveat": OPM_CAVEAT})
        else:
            cautions.append(
                f"Růst u {label} tvrdit nelze: počet odběrných míst se {span} změnil o "
                f"{rules.num(change['abs'])} ({pct_text(change['pct'])})."
            )
    position, count, ranking = rules.ote_position(ctx["ote"], own)
    if position is not None:
        _, gas_count, _ = rules.ote_position(ctx.get("ote_gas"), own)
        text = (
            f"Podle OTE máme {rules.num(count)} odběrných míst elektřiny "
            f"({position}. místo z {len(ranking)} dodavatelů)"
        )
        if gas_count:
            text += f" a {rules.num(gas_count)} plynu, dohromady {rules.num(count + gas_count)}"
        provable.append({"text": text + ".", "source": "OTE – počty OPM",
                         "caveat": "Uvádět jako odběrná místa, ne jako zákazníky."})
    marks = [rules.benchmark_offer(offer, ctx) for offer in own["offers"]]
    below = [m["product"] for m in marks if m["eligible"] and m["position"] == "below"]
    above = [m["product"] for m in marks if m["eligible"] and m["position"] == "above"]
    if below:
        provable.append({
            "text": (
                f"Naše fixované nabídky ({', '.join(below)}) jsou pod indikativním pásmem ERÚ, tedy podle ERÚ "
                "mezi nejlevnější čtvrtinou fixovaných nabídek na trhu."
            ),
            "source": "ERÚ – indikativní pásma nabídkových cen",
            "caveat": f"Platí pro cenu komodity v sazbě {profile['tariff']} a aktuální měsíc; pásmo se mění každý měsíc.",
        })
    if above:
        cautions.append(f"Nabídky nad pásmem ERÚ ({', '.join(above)}): tvrzení o výhodné ceně by u nich šlo napadnout.")
    wins = []
    for group in comparison:
        ranked = _best_by_supplier(group)
        mine = next((r for r in ranked if r["supplier_id"] == own["id"]), None)
        if mine is None:
            continue
        for other in ranked:
            diffs = [b - a for a, b in zip(mine["annual_cost"], other["annual_cost"])]
            # rozdíl pod 100 Kč ročně není tvrzení, které by stálo za komunikaci
            if other is not mine and all(d > 0 for d in diffs) and diffs[0] >= MIN_SAVING_CZK:
                wins.append((diffs[0], group["fixation_months"], other, diffs))
    for _, months, other, diffs in sorted(wins, key=lambda w: -w[0])[:3]:
        savings = " a ".join(f"o {rules.czk(d)} ročně při {m} MWh" for m, d in zip(mwh, diffs))
        provable.append({
            "text": f"Při fixaci na {months} měsíců jsme levnější než {other['supplier']} ({other['product']}): {savings}.",
            "source": "ceníky obou dodavatelů",
            "caveat": PRICE_CAVEAT,
        })

    # 4. co dělá konkurence a týká se nás
    watch = []
    cheaper_by: dict[str, list[str]] = {}
    for item in price:
        for other in item["cheaper"]:
            cheaper_by.setdefault(other["supplier"], []).append(
                f"fixace {item['fixation_months']} měs. o {rules.czk(other['saving'][0])}"
            )
    for name, parts in cheaper_by.items():
        watch.append({"kind": "price", "text": f"{name} je levnější než my: {', '.join(parts)} ročně při {mwh[0]} MWh."})
    for supplier_id, claims in evaluated.items():
        if supplier_id == own["id"]:
            continue
        for claim in claims:
            if _mentions(claim["finding"], own):
                watch.append({
                    "kind": "claim", "supplier": claim["supplier"], "quote": claim["quote"],
                    "verdict_label": claim["verdict_label"], "text": claim["finding"],
                })
    for gap in market.get("loyalty_gaps", []):
        note = " Rozdělení na nové a stávající zákazníky je odvozené z názvů produktů." if gap["inferred"] else ""
        if gap["supplier_id"] == own["id"]:
            cautions.append(
                "Naši stávající zákazníci platí víc než noví, což může konkurent použít proti nám. "
                + _gap_text(gap) + note
            )
        else:
            watch.append({
                "kind": "loyalty",
                "text": "Stávající zákazníci platí víc než noví: " + _gap_text(gap)
                        + " Lze je oslovit nabídkou, která je pro ně výhodnější." + note,
            })
    mine_row = next((r for r in market["rows"] if r["id"] == own["id"]), None)
    if mine_row and mine_row["electricity"]:
        own_pct = mine_row["electricity"]["pct"]
        faster = [
            r for r in market["rows"]
            if r["id"] != own["id"] and r["electricity"] and r["electricity"]["abs"] > 0
            and r["electricity"]["pct"] > own_pct
        ]
        faster.sort(key=lambda r: -r["electricity"]["abs"])
        if faster:
            names = ", ".join(
                f"{r['name']} ({pct_text(r['electricity']['pct'])}, {rules.num(r['electricity']['abs'])} OPM)"
                for r in faster[:4]
            )
            watch.append({
                "kind": "growth",
                "text": f"V elektřině rostou rychleji než my ({pct_text(own_pct)}): {names}.",
            })

    return {
        "id": own["id"], "supplier": own["name"],
        "price": price, "risky": risky, "solid": solid,
        "provable": provable, "cautions": cautions, "watch": watch,
        "note": (
            "Pohled se skládá ze stejných dat a pravidel jako zbytek zprávy. Tvrzení v části „Co můžeme tvrdit“ "
            "jsou návrhy doložitelné uvedeným zdrojem, ne hotové reklamní texty ani právní posouzení."
        ),
    }
