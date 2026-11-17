"""Přehled trhu a pohled jedné firmy.

Vše se skládá z údajů, které audit už má: roční náklad z ceníků, pozice vůči pásmu ERÚ,
počty odběrných míst z OTE a vyhodnocená tvrzení. Žádná čísla nedoplňuje jazykový model.
"""
from __future__ import annotations

import re

from . import evidence as ev
from . import rules
from .connectors import ote

OPM_CAVEAT = "Odběrná místa nejsou zákazníci; změna může zahrnovat i převzetí jiného dodavatele."
MIN_SAVING_CZK = 100
PRICE_CAVEAT = (
    "Obchodní část ceny (komodita a stálý plat) pro srovnávací sazbu a území; "
    "před použitím znovu ověřit platnost obou ceníků."
)


def pct_text(value: float | None) -> str:
    return ev.pct_label(value)  # nulový výchozí stav: procento není definováno (QUAL-005)


def _growth_key(row: dict) -> tuple:
    """Řazení podle růstu: nejdřív známé procento, pak růst z nuly (jen absolutně), nakonec bez údaje."""
    change = row["electricity"]
    if change is None:
        return (2, 0)
    if change["pct"] is None:
        return (1, -change["abs"])
    return (0, -change["pct"])


def _series(table_data: dict | None, legal_name: str) -> dict | None:
    """Změna počtu OPM dodavatele ve společném období tabulky; None, když chybí krajní údaj."""
    change = ev.opm_change(table_data, legal_name)
    if change.abs is None:
        return None
    return {
        "first": change.first, "last": change.last, "abs": change.abs, "pct": change.pct,
        "from": f"{change.window.year}-{change.window.start + 1:02d}",
        "to": f"{change.window.year}-{change.window.end + 1:02d}",
    }


def _best_by_supplier(group: dict) -> list[dict]:
    """Nejlevnější nabídka každého dodavatele v dané délce fixace, seřazeno od nejlevnější."""
    best: dict[str, dict] = {}
    for row in group["rows"]:  # řádky jsou už seřazené podle ročního nákladu
        if all(c is not None for c in row["annual_cost"]):
            best.setdefault(row["supplier_id"], row)
    return list(best.values())


def _decision_counts(supplier: dict, ctx: dict) -> dict | None:
    stats = rules.decision_stats(supplier, ctx)
    if stats is None:
        return None
    return {"offences": len(stats["offences"]), "practices": len(stats["practices"]),
            "latest_practice": stats["latest_practice"]}


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
            "decisions": _decision_counts(supplier, ctx),
        })
    rows.sort(key=_growth_key)
    window = ev.common_window(ctx["ote"])
    market = ev.market_change(ctx["ote"])

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
        observations.append(
            "Ve vzorku tedy nejnižší ceníková cena nejde ruku v ruce s růstem počtu odběrných míst. "
            "Jde o souběh dvou údajů, ne o zjištěnou příčinu."
        )
    gaps = loyalty_gaps(suppliers, ctx)
    return {
        "loyalty_gaps": gaps,
        "loyalty_caveat": (
            "Srovnává se cena komodity s DPH u produktů se stejnou délkou fixace; produkty se mohou lišit "
            "podmínkami a službami. Údaj, komu je produkt určen, pochází ze stránek dodavatele, ne z ceníku."
        ),
        "fixation_months": longest["fixation_months"] if longest else None,
        "annual_mwh": longest["annual_mwh"][0] if longest else None,
        "period": {"from": f"{window.year}-{window.start + 1:02d}", "to": f"{window.year}-{window.end + 1:02d}"}
        if window else None,
        "market_electricity_pct": market.pct,
        "market_cohort": {"included": market.cohort, "excluded": market.excluded},
        "observations": observations,
        "rows": rows,
        "caveat": (
            OPM_CAVEAT + " Změny se počítají za stejné období pro všechny; dodavatel bez údaje na začátku nebo "
            "na konci období změnu nemá. Jde o souběh dvou údajů, ne o důkaz příčiny. Ceny jsou nabídky pro nové "
            "zákazníky v jedné sazbě a jednom distribučním území a pořadí platí jen pro tento vzorek."
        ),
    }


def loyalty_gaps(suppliers: list[dict], ctx: dict) -> list[dict]:
    """O kolik víc platí stávající zákazník než nový u téhož dodavatele a stejné délky fixace."""
    profile = ctx["profile"]
    gaps = []
    for supplier in suppliers:
        source = supplier.get("audience_source") or {}
        by_months: dict[int, dict[str, dict]] = {}
        for offer in supplier["offers"]:
            # stejné pravidlo jako verdikty a srovnání, obě nabídky platné k jednomu datu srovnání
            if not ev.offer_eligibility(offer, ctx, current=True).ok:
                continue
            group = {"new": "new", "existing": "existing", "all": "existing"}.get(offer.get("audience"))
            if group is None:
                continue
            slot = by_months.setdefault(offer["fixation_months"], {})
            current = slot.get(group)
            if current is None or offer["price_mwh_incl_vat"] < current["price_mwh_incl_vat"]:
                slot[group] = offer
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
            if (label == "elektřiny" and market_pct is not None and change["pct"] is not None
                    and change["pct"] > market_pct):
                text += (f" Trh za stejné období (dodavatelé s údajem na začátku i na konci) se změnil o "
                         f"{pct_text(market_pct)}.")
            provable.append({"text": text, "source": source, "caveat": OPM_CAVEAT})
        else:
            cautions.append(
                f"Růst u {label} tvrdit nelze: počet odběrných míst se {span} změnil o "
                f"{rules.num(change['abs'])} ({pct_text(change['pct'])})."
            )
    position, count, ranking = rules.ote_position(ctx["ote"], own)
    if position is not None:
        text = (
            f"Podle OTE máme {rules.num(count)} odběrných míst elektřiny "
            f"({position}. místo z {len(ranking)} dodavatelů, {ev.table_period(ctx['ote'])})"
        )
        both = ev.opm_measure(ctx, (own["ote_name"],), (ev.ELECTRICITY, ev.GAS))
        if both.total is not None:
            text += f"; s plynem dohromady {rules.num(both.total)}"
        provable.append({"text": text + ".", "source": "OTE – počty OPM",
                         "caveat": "Uvádět jako odběrná místa, ne jako zákazníky, a s měsícem údaje."})
    marks = [rules.benchmark_offer(offer, ctx) for offer in own["offers"]]
    below = [m["product"] for m in marks if m["eligible"] and m["position"] == "below"]
    above = [m["product"] for m in marks if m["eligible"] and m["position"] == "above"]
    if below:
        provable.append({
            "text": (
                f"Cena komodity našich fixovaných nabídek ({', '.join(below)}) je pod indikativním pásmem ERÚ "
                f"pro sazbu {profile['tariff']}, příslušnou délku fixace a měsíc."
            ),
            "source": "ERÚ – indikativní pásma nabídkových cen",
            "caveat": (
                "Pásmo je odhad ERÚ, ne změřené pořadí na trhu; neříkat „nejlevnější čtvrtina trhu“. Platí jen pro "
                "cenu komodity (bez stálého platu a regulovaných složek) a daný měsíc."
            ),
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
    regulator = []
    for row in market["rows"]:
        counts = row.get("decisions")
        if not counts or not counts["practices"]:
            continue
        if row["id"] == own["id"]:
            cautions.append(
                f"ERÚ eviduje {rules.decisions_phrase(counts['offences'])} o našich přestupcích, z toho se "
                f"obchodních praktik nebo nepravdivých informací týká {counts['practices']} (poslední zveřejněno "
                f"{counts['latest_practice']}). Tvrzení o férovosti a důvěryhodnosti tím může konkurent zpochybnit."
            )
        else:
            regulator.append((counts["practices"], f"{row['name']} {counts['practices']} "
                                                   f"(poslední {counts['latest_practice']})"))
    if regulator:
        regulator.sort(key=lambda item: -item[0])
        watch.append({
            "kind": "regulator",
            "text": "Pravomocná rozhodnutí ERÚ o nekalých či agresivních obchodních praktikách nebo "
                    "nepravdivých informacích: " + ", ".join(text for _, text in regulator) + ".",
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
    if mine_row and mine_row["electricity"] and mine_row["electricity"]["pct"] is not None:
        own_pct = mine_row["electricity"]["pct"]
        faster = [  # růst z nuly nemá procento, proto se do „rychleji“ nepočítá
            r for r in market["rows"]
            if r["id"] != own["id"] and r["electricity"] and r["electricity"]["abs"] > 0
            and r["electricity"]["pct"] is not None and r["electricity"]["pct"] > own_pct
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
