"""Deterministická pravidla. Čísla a verdikty počítá kód, ne jazykový model.

Zásady:
- chybějící nebo nesrovnatelný podklad znamená `unverifiable`, nikdy `contradicted`;
- pozice vůči pásmu ERÚ je nález, ne důkaz klamání ani změřené pořadí na trhu;
- silný verdikt jen tehdy, když podklad odpovídá rozsahu tvrzení (metrika, komodita, období);
- vítězství ve vzorku několika dodavatelů neprokazuje prvenství na celém trhu;
- OPM (odběrná místa) nejsou zákazníci.
"""
from __future__ import annotations

import re
from decimal import Decimal

from . import evidence as ev
from .connectors import eru as eru_connector
from .connectors import ote as ote_connector

SUPPORTED = "supported"
PARTIAL = "partially_supported"
CONTRADICTED = "contradicted"
UNVERIFIABLE = "unverifiable"

RULE_LABELS = {
    "price_favorable": "Výhodná cena",
    "price_rank": "Nejnižší cena / cenové prvenství",
    "price_guarantee": "Garance (fixace) ceny",
    "market_size": "Velikost na trhu",
    "price_pct_cheaper": "Procentní úspora proti konkurentovi",
    "customer_count": "Počet zákazníků",
    "customer_growth": "Růst počtu odběrných míst",
    "carbon_neutral": "Uhlíková neutralita a kompenzace",
    "green_share": "Podíl zelené elektřiny",
    "green_generic": "Obecné zelené tvrzení",
    "soft_trust": "Důvěra a férovost",
    "other": "Ostatní",
}


def czk(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") + " Kč"


def num(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


DECISION_KIND = {
    "practice": "přestupek: obchodní praktiky nebo nepravdivé informace",
    "offence": "přestupek: jiné porušení povinností",
    "dispute": "rozhodnutí ve sporu",
    "discontinued": "zastavené řízení",
    "other": "jiné rozhodnutí",
}


def decisions_phrase(count: int) -> str:
    if count == 1:
        return "1 pravomocné rozhodnutí"
    return f"{count} pravomocná rozhodnutí" if count < 5 else f"{count} pravomocných rozhodnutí"


GREEN_RISK_REASON = (
    "Environmentální tvrzení bez doložení může spadat pod směrnici (EU) 2024/825; nutná právní kontrola."
)
PCT_TOLERANCE = Decimal("0.5")  # o kolik procentních bodů smí změřená úspora zaostat za tvrzenou


def result(verdict, finding, limitation, action, evidence=None, subclaims=None, risk=False,
           opportunity=False, risk_reason=None, review_required=False):
    return {
        "risk_reason": risk_reason if risk else None,
        "review_required": bool(review_required or risk),
        "verdict": verdict,
        "finding": finding,
        "limitation": limitation,
        "action": action,
        "evidence": evidence or [],
        "subclaims": subclaims or [],
        "possible_regulatory_risk": risk,
        "opportunity": opportunity,
    }


# ---------- srovnání nabídky s pásmem ERÚ ----------

def benchmark_offer(offer: dict, ctx: dict) -> dict:
    """Pozice jedné nabídky vůči indikativnímu pásmu ERÚ pro stejnou sazbu, fixaci a měsíc."""
    out = {
        "offer_id": offer["id"],
        "product": offer["product"],
        "position": "unknown",
        "not_above": None,
        "eligible": False,
        "reason": None,
        "band": None,
        "price": None,
        "basis": None,
        "period": None,
        "notes": [],
    }
    check = ev.offer_eligibility(offer, ctx)
    if not check.ok:
        out["reason"] = check.reason
        return out
    out["period"], out["notes"] = check.period, list(check.notes)
    band = eru_connector.select_band(
        ctx["eru"]["rows"], offer["tariff"], offer["fixation_months"], check.period
    )
    if band is None:
        out["reason"] = f"ERÚ nemá pásmo pro fixaci {offer['fixation_months']} měsíců a období {check.period}."
        return out
    price = offer["price_mwh_excl_vat"]
    out.update(band=band, price=price, basis="bez DPH")
    if price < band["low_excl_vat"]:
        out["position"] = "below"
    elif price <= band["high_excl_vat"]:
        out["position"] = "within"
    else:
        out["position"] = "above"
    out["not_above"] = out["position"] != "above"
    out["eligible"] = True
    return out


POSITION_SHORT = {"below": "pod pásmem", "within": "v pásmu", "above": "nad pásmem", "unknown": "nelze určit"}
POSITION_LABEL = {
    "below": "pod indikativním pásmem ERÚ",
    "within": "v indikativním pásmu ERÚ",
    "above": "nad indikativním pásmem ERÚ",
    "unknown": "nelze určit",
}


def band_evidence(bm: dict, ctx: dict) -> dict:
    band = bm["band"]
    key = "excl_vat" if bm["basis"] != "s DPH" else "incl_vat"
    return {
        "label": f"{bm['product']}: {czk(bm['price'])}/MWh ({bm['basis']})",
        "value": (
            f"pásmo ERÚ {num(band['low_' + key])}–{num(band['high_' + key])} Kč/MWh "
            f"({'bez DPH' if key == 'excl_vat' else 's DPH'}; {band['tariff']}, fixace "
            f"{band['fixation_months']} měs., období {band['month']}) → {POSITION_LABEL[bm['position']]}"
        ),
        "source": "eru",
        "chart": {
            "low": band["low_" + key], "high": band["high_" + key], "price": bm["price"],
            "position": bm["position"],
        },
    }


ERU_LIMIT = (
    "Indikativní pásmo ERÚ je odhad ceny komodity pro danou sazbu, délku fixace a měsíc, bez stálého "
    "platu a regulovaných složek. Podle metodiky ERÚ dolní mez navazuje na 25. percentil a horní mez "
    "přibližně na medián nabídek ze srovnávače, nabídky se ale od pásma krátkodobě odchylují. Pozice "
    "vůči pásmu proto není změřené pořadí na trhu ani celkový účet."
)


def price_favorable(claim, supplier, ctx):
    marks = [benchmark_offer(o, ctx) for o in supplier["offers"]]
    eligible = [m for m in marks if m["eligible"]]
    evidence = [band_evidence(m, ctx) for m in marks if m["band"] is not None and m["price"] is not None]
    if not eligible:
        return result(
            UNVERIFIABLE,
            "Pro tohoto dodavatele není k dispozici nabídka, kterou lze srovnat s pásmem ERÚ"
            + (": " + "; ".join(sorted({m["reason"] for m in marks if m["reason"]})) + "." if marks else "."),
            ERU_LIMIT,
            "Doplnit ceník dodavatele pro profil D02d a ověřit znovu.",
            evidence,
        )
    above = [m for m in eligible if m["position"] == "above"]
    if above:
        names = ", ".join(m["product"] for m in above)
        return result(
            UNVERIFIABLE,
            f"Nabídky nad horní hranicí pásma ERÚ: {names}. To samo o sobě tvrzení nevyvrací.",
            ERU_LIMIT,
            "Příležitost: v komunikaci můžeme odkázat na veřejné pásmo ERÚ a ukázat, že naše cena je v něm. "
            "Nejprve ověřit vlastní cenu stejným postupem.",
            evidence,
            opportunity=True,
        )
    summary = "; ".join(f"{m['product']} {POSITION_SHORT[m['position']]}" for m in eligible)
    all_below = all(m["position"] == "below" for m in eligible)
    if all_below:
        level = "Všechny srovnatelné nabídky jsou pod indikativním pásmem ERÚ pro svou sazbu, fixaci a měsíc"
    else:
        level = "Srovnatelné nabídky nepřesahují horní mez indikativního pásma ERÚ pro svou sazbu, fixaci a měsíc"
    if claim.get("params", {}).get("basis") == "customer_saving":
        return result(
            PARTIAL,
            f"{level} ({summary}). Skutečná úspora konkrétního zákazníka ale závisí na jeho současné smlouvě.",
            ERU_LIMIT,
            "Tvrzení o úspoře je pro zákazníka neověřitelné předem; můžeme nabídnout konkrétní kalkulaci úspory.",
            evidence,
            subclaims=[
                {"text": "Cena není nad indikativním pásmem ERÚ", "status": "ověřeno"},
                {"text": "Zákazník přechodem ušetří", "status": "neověřeno – závisí na jeho současné ceně"},
            ],
        )
    if all_below:
        action = "Cena komodity konkurenta je vůči pásmu ERÚ nízko; soutěžit spíš stálým platem, podmínkami a službami."
    else:
        action = "Cena komodity konkurenta je uvnitř pásma ERÚ; cenou komodity s ním lze soutěžit."
    return result(
        SUPPORTED,
        f"{level} ({summary}).",
        ERU_LIMIT + " „Výhodná cena“ je zde vyložena jako cena nepřesahující pásmo ERÚ.",
        action,
        evidence,
    )


# ---------- cenové prvenství ----------

def _comparable(offer: dict, ctx: dict) -> bool:
    return ev.offer_eligibility(offer, ctx, audience="new").ok  # nabídky pro zákazníka, který mění dodavatele


def _same_area(a: dict, b: dict) -> bool:
    return not a.get("area") or not b.get("area") or a["area"] == b["area"]


def _annual_cost(offer: dict, mwh: float) -> float | None:
    if offer.get("monthly_fee_incl_vat") is None or offer.get("price_mwh_incl_vat") is None:
        return None
    fee = Decimal(str(offer["monthly_fee_incl_vat"]))
    price = Decimal(str(offer["price_mwh_incl_vat"]))
    return float((12 * fee + Decimal(str(mwh)) * price).quantize(Decimal("0.01")))


annual_cost = _annual_cost  # veřejný název pro přehled nabídek


def _cost_pairs(costs, profile) -> str:
    return "; ".join(f"{m} MWh/rok: {czk(a)} vs. {czk(b)}" for m, (a, b) in zip(profile["annual_mwh"], costs))


def price_rank(claim, supplier, ctx):
    kind = claim.get("params", {}).get("kind")
    if kind is None:  # automaticky nalezené tvrzení: „garance nejnižší ceny“ je závazek dorovnání
        kind = "price_match_guarantee" if "garanc" in claim.get("quote", "").casefold() else "superlative"
    profile = ctx["profile"]
    own = [o for o in supplier["offers"] if _comparable(o, ctx)]
    sample = sorted({
        s["name"] for s in ctx["suppliers"]
        if s["id"] != supplier["id"] and any(_comparable(o, ctx) for o in s["offers"])
    })
    sample_note = (
        f"Srovnání pokrývá jen vzorek ({', '.join(sample) or 'žádný další dodavatel'}): sazba {profile['tariff']}"
        + (f", distribuční území {profile['area']}" if profile.get("area") else "")
        + ", stejná délka fixace, jen produkty s jednou pevnou cenou."
    )
    if not own:
        return result(
            UNVERIFIABLE,
            "Pro dodavatele nemáme ceník fixovaného produktu se známou sazbou a cenou, takže ho nelze "
            "srovnat (spotový tarif s fixovanými tarify srovnávat nelze vůbec).",
            sample_note,
            "Doplnit ceník dodavatele pro stejnou sazbu a distribuční území; do té doby tvrzení nehodnotit.",
        )
    evidence, cheaper_unit, cheaper_total = [], [], []
    for mine in own:
        for other in ctx["suppliers"]:
            if other["id"] == supplier["id"]:
                continue
            for theirs in other["offers"]:
                if (
                    not _comparable(theirs, ctx)
                    or theirs["fixation_months"] != mine["fixation_months"]
                    or not _same_area(mine, theirs)
                ):
                    continue
                diff = mine["price_mwh_excl_vat"] - theirs["price_mwh_excl_vat"]
                costs = [(_annual_cost(mine, m), _annual_cost(theirs, m)) for m in profile["annual_mwh"]]
                known = all(a is not None and b is not None for a, b in costs)
                value = (
                    f"{other['name']} – {theirs['product']}: {czk(theirs['price_mwh_excl_vat'])}/MWh "
                    f"(rozdíl {'+' if diff > 0 else ''}{num(diff)} Kč/MWh bez DPH)"
                )
                if known:
                    value += "; roční náklad obchodní části s DPH " + _cost_pairs(costs, profile)
                evidence.append({
                    "label": (
                        f"Fixace {mine['fixation_months']} měs.: {supplier['name']} – {mine['product']} "
                        f"{czk(mine['price_mwh_excl_vat'])}/MWh"
                    ),
                    "value": value,
                    "source": f"web:{other['id']}",
                })
                if diff > 0:
                    cheaper_unit.append((mine, other, theirs, diff))
                if known and all(b < a for a, b in costs):
                    cheaper_total.append((mine, other, theirs, costs))
    cheaper_unit.sort(key=lambda item: -item[3])
    cheaper_total.sort(key=lambda item: -(item[3][0][0] - item[3][0][1]))  # největší úspora první

    if kind == "price_match_guarantee":
        limitation = sample_note + " Zda dodavatel garanci skutečně plní, z veřejných dat ověřit nelze."
        if cheaper_total:
            mine, other, theirs, costs = cheaper_total[0]
            return result(
                UNVERIFIABLE,
                "Jde o závazek dorovnat nižší cenu, ne o tvrzení, že ceník je nejnižší. Levnější srovnatelná "
                f"nabídka veřejně existuje: {other['name']} – {theirs['product']} proti {mine['product']}. "
                f"Roční náklad obchodní části (s DPH) {_cost_pairs(costs, profile)}.",
                limitation,
                "Příležitost: slogan „Garance nejnižší ceny“ nezaručuje nejnižší ceník; zákazník musí o dorovnání "
                "sám požádat a nižší cenu doložit. V komunikaci lze ukázat konkrétní levnější ceníkovou nabídku.",
                evidence, opportunity=2,
            )
        if cheaper_unit:
            mine, other, theirs, diff = cheaper_unit[0]
            return result(
                UNVERIFIABLE,
                "Jde o závazek dorovnat nižší cenu, ne o tvrzení, že ceník je nejnižší. Nižší cena komodity "
                f"veřejně existuje: {other['name']} – {theirs['product']} za {czk(theirs['price_mwh_excl_vat'])}/MWh "
                f"proti {czk(mine['price_mwh_excl_vat'])}/MWh ({mine['product']}), rozdíl {num(diff)} Kč/MWh bez DPH.",
                limitation + " Stálý měsíční plat není u obou nabídek znám.",
                "Doplnit stálé platy z ceníků obou produktů; pak lze ukázat rozdíl v ročním nákladu.",
                evidence, opportunity=2,
            )
        return result(
            UNVERIFIABLE,
            "Ve vzorku jsme nenašli nižší srovnatelnou ceníkovou cenu. Jde o závazek dorovnání, ne o prvenství.",
            limitation, "Bez akce; sledovat ceníky při další kontrole.", evidence,
        )
    if cheaper_total:
        mine, other, theirs, costs = cheaper_total[0]
        others = sorted({o["name"] for _, o, _, _ in cheaper_total})
        return result(
            CONTRADICTED,
            f"Existuje levnější srovnatelná nabídka: {other['name']} – {theirs['product']} proti "
            f"{mine['product']}. Roční náklad obchodní části (s DPH) {_cost_pairs(costs, profile)}. "
            f"Levnější srovnatelnou nabídku ve vzorku mají: {', '.join(others)}.",
            sample_note + " Jde o obchodní část ceny (komodita a stálý plat), ne o celkový účet. "
            "Tvrzení může „nejlepší cenou“ myslet i něco jiného než ceníkovou cenu.",
            "Příležitost: tvrzení o nejlepší ceně lze doložitelně zpochybnit konkrétním protipříkladem z veřejných "
            "ceníků. Před komunikací znovu ověřit oba ceníky a datum platnosti.",
            evidence, opportunity=3,
        )
    if cheaper_unit:
        mine, other, theirs, diff = cheaper_unit[0]
        return result(
            UNVERIFIABLE,
            f"Nižší cena komodity existuje ({other['name']} – {theirs['product']}, o {num(diff)} Kč/MWh bez DPH), "
            "ale roční náklad včetně stálého platu nevychází nižší ve všech profilech spotřeby, nebo stálý plat "
            "není znám.",
            sample_note,
            "Doplnit stálé platy z ceníků; pokud vyjde nižší i roční náklad, lze tvrzení doložitelně zpochybnit.",
            evidence, opportunity=True,
        )
    if claim.get("params", {}).get("scope") == "sample" and evidence:
        return result(
            SUPPORTED,
            "Ve srovnávaném vzorku je nabídka dodavatele nejlevnější; tvrzení se výslovně omezuje na vzorek.",
            sample_note, "Bez akce.", evidence,
        )
    return result(
        UNVERIFIABLE,
        "Ve vzorku jsme nenašli levnější srovnatelnou nabídku. Prvenství ve vzorku ale neprokazuje "
        "prvenství na celém trhu.",
        sample_note,
        "Bez akce; pro silnější závěr rozšířit vzorek dodavatelů.",
        evidence,
    )


def price_pct_cheaper(claim, supplier, ctx):
    params = claim.get("params", {})
    pct, rival_id = params.get("pct"), params.get("baseline_supplier_id")
    rival = next((s for s in ctx["suppliers"] if s["id"] == rival_id), None)
    limitation = "Počítáno z obchodní části ceny (komodita a stálý plat) včetně DPH, ne z celého účtu."
    if pct is None or rival is None or params.get("basis", "annual_trade_cost") != "annual_trade_cost":
        return result(
            UNVERIFIABLE,
            "Tvrzení neuvádí ověřitelný srovnávací základ (s kým, o kolik a na jaké části ceny).",
            limitation, "Vyžádat si od dodavatele metodiku srovnání.",
        )
    pairs = [
        (mine, theirs)
        for mine in supplier["offers"] if _comparable(mine, ctx)
        for theirs in rival["offers"]
        if _comparable(theirs, ctx) and theirs["fixation_months"] == mine["fixation_months"]
        and _same_area(mine, theirs)
    ]
    holds, evidence = [], []
    for mine, theirs in pairs:
        for mwh in ctx["profile"]["annual_mwh"]:
            own, base = _annual_cost(mine, mwh), _annual_cost(theirs, mwh)
            if own is None or base is None or base <= 0:
                continue
            saving = (Decimal(str(base)) - Decimal(str(own))) / Decimal(str(base)) * 100
            holds.append(saving >= Decimal(str(pct)) - PCT_TOLERANCE)
            evidence.append({
                "label": f"{mwh} MWh/rok, fixace {mine['fixation_months']} měs.: {czk(own)} vs. {czk(base)}",
                "value": f"úspora {saving:.1f} % (tvrzeno {pct} %)",
                "source": f"web:{rival['id']}",
            })
    if not holds:
        return result(
            UNVERIFIABLE,
            "Chybí srovnatelné nabídky obou dodavatelů se známým stálým platem.",
            limitation, "Doplnit stálé platy z ceníků obou dodavatelů.",
        )
    if all(holds):
        return result(SUPPORTED, "Úspora dosahuje uvedeného procenta ve všech profilech spotřeby.",
                      limitation, "Tvrzení nenapadat.", evidence)
    if not any(holds):
        return result(
            CONTRADICTED, "Úspora nedosahuje uvedeného procenta v žádném profilu spotřeby.", limitation,
            "Příležitost: tvrzenou úsporu lze zpochybnit výpočtem z veřejných ceníků.", evidence, opportunity=3,
        )
    return result(
        UNVERIFIABLE, "Úspora závisí na spotřebě: v některých profilech platí, v jiných ne.",
        limitation + " Tvrzení neuvádí, pro jakou spotřebu platí.",
        "Příležitost: upozornit, že úspora platí jen pro část zákazníků.", evidence, opportunity=True,
    )


def price_guarantee(claim, supplier, ctx):
    months = claim.get("params", {}).get("fixation_months")
    if months is None:  # automaticky nalezené tvrzení: délku fixace vyčti z citátu
        found = re.search(r"(\d+)\s*(rok|roky|roků|let|měsíc)", claim.get("quote", ""))
        if found:
            months = int(found.group(1)) * (1 if found.group(2).startswith("měsíc") else 12)
    matching = [
        o for o in supplier["offers"]
        if months and o.get("fixation_months") == months and not o.get("stale")
    ]
    if not matching:
        return result(
            UNVERIFIABLE,
            f"V připravených nabídkách dodavatele není produkt s fixací {months} měsíců.",
            "Smluvní podmínky a výluky nebyly posuzovány.",
            "Vyžádat si ceník a obchodní podmínky produktu.",
        )
    marks = [benchmark_offer(o, ctx) for o in matching]
    evidence = [band_evidence(m, ctx) for m in marks if m["band"] is not None and m["price"] is not None]
    confirmed = [o for o in matching if o.get("confirmed_live")]
    subclaims = [{
        "text": f"Produkt s fixací ceny na {months} měsíců je nabízen ({matching[0]['product']})",
        "status": "ověřeno na stránce dodavatele" if confirmed else "podle uloženého záznamu",
    }]
    price_ok = [m for m in marks if m["eligible"] and m["not_above"]]
    if price_ok:
        subclaims.append({"text": "Cena je výhodná (nepřesahuje pásmo ERÚ)", "status": "ověřeno"})
        verdict = SUPPORTED
        finding = f"Produkt s fixací na {months} měsíců existuje a jeho cena nepřesahuje pásmo ERÚ."
    else:
        reason = next((m["reason"] for m in marks if m["reason"]), "Cenu nelze srovnat s pásmem ERÚ.")
        subclaims.append({"text": "Cena je „výhodnější“", "status": f"neověřeno – {reason}"})
        verdict = PARTIAL
        finding = (
            f"Produkt s fixací na {months} měsíců existuje ({matching[0]['product']}). "
            f"Výhodnost ceny ověřit nelze: {reason}"
        )
    return result(
        verdict, finding,
        "Ověřena existence produktu a cena komodity; smluvní výluky (daně, regulované složky, sankce za "
        "předčasné ukončení) posouzeny nebyly.",
        "Příležitost: pokud u nás uvádíme cenu jednoznačně (s DPH i bez, se sazbou), můžeme transparentnost "
        "ceny použít jako odlišení." if verdict == PARTIAL else "Bez akce.",
        evidence, subclaims, opportunity=verdict == PARTIAL,
    )


# ---------- OTE: velikost a počty ----------

OTE_LIMIT = "OPM jsou odběrná místa, ne zákazníci, objem dodávky ani tržby."
NO_METRIC = (
    "Tvrzení neuvádí, podle čeho se velikost měří (zákazníci, odběrná místa, objem dodávky, tržby). "
    "OTE zveřejňuje jen počty odběrných míst, proto je níže jen jako kontext, ne jako verdikt."
)


def _ote_position(data: dict | None, supplier: dict):
    """(pořadí, počet OPM, celé pořadí) dodavatele v jedné tabulce OTE; (None, None, …), když v ní není."""
    if not data:
        return None, None, []
    ranking = data["ranking"]
    for position, (name, count) in enumerate(ranking, start=1):
        if ote_connector.same_supplier(name, supplier.get("ote_name") or ""):
            return position, count, ranking
    return None, None, ranking


ote_position = _ote_position  # veřejný název pro pohled firmy


def _full_name(name: str, ctx: dict) -> str:
    """Tabulka plynu zkracuje názvy; vrátí celý název z tabulky elektřiny, pokud tam dodavatel je."""
    for full, _ in ctx["ote"]["ranking"]:
        if ote_connector.same_supplier(name, full):
            return full
    return name


def _top(ranking, ctx=None, n=5) -> str:
    return "; ".join(
        f"{i}. {_full_name(name, ctx) if ctx else name} ({num(count)})"
        for i, (name, count) in enumerate(ranking[:n], start=1)
    )


def _size_context(supplier: dict, ctx: dict, scope: ev.ClaimScope) -> tuple[list, list, dict]:
    """Pořadí podle OPM pro elektřinu, plyn a jejich součet: důkazy, dílčí řádky a údaje."""
    position, count, ranking = _ote_position(ctx["ote"], supplier)
    period = ctx["ote"]["source"]["data_period"]
    evidence = [
        {"label": f"Elektřina – {supplier['ote_name']}: {num(count)} OPM ({period})",
         "value": f"{position}. místo z {len(ranking)} dodavatelů", "source": "ote"},
        {"label": "Pět největších v elektřině podle OPM", "value": _top(ranking), "source": "ote"},
    ]
    info = {"position": position, "count": count, "leader": ranking[0] if ranking else None, "period": period}
    rows = [{"text": "Elektřina podle OPM", "status": f"{position}. místo" + (
        "" if position == 1 else f"; největší je {ranking[0][0]}")}]
    gas_data = ctx.get("ote_gas")
    gas_pos, gas_count, gas_ranking = _ote_position(gas_data, supplier)
    if ev.GAS in scope.commodities:
        if gas_data and gas_pos is not None:
            gas_leader = _full_name(gas_ranking[0][0], ctx)
            evidence += [
                {"label": f"Plyn – {supplier['ote_name']}: {num(gas_count)} OPM ({gas_data['source']['data_period']})",
                 "value": f"{gas_pos}. místo z {len(gas_ranking)} dodavatelů", "source": "ote_gas"},
                {"label": "Pět největších v plynu podle OPM", "value": _top(gas_ranking, ctx), "source": "ote_gas"},
            ]
            rows.append({"text": "Plyn podle OPM", "status": f"{gas_pos}. místo" + (
                "" if gas_pos == 1 else f"; největší je {gas_leader}")})
        else:
            rows.append({"text": "Plyn podle OPM", "status": "údaj OTE chybí"})
        measure = ev.opm_measure(ctx, (supplier["ote_name"],), (ev.ELECTRICITY, ev.GAS))
        if measure.total is not None:
            evidence.append({"label": f"Součet elektřina + plyn ({measure.period}): {num(measure.total)} OPM",
                             "value": measure.breakdown(), "source": "ote"})
    info.update(gas_position=gas_pos, gas_count=gas_count)
    return evidence, rows, info


def market_size(claim, supplier, ctx):
    position, count, ranking = _ote_position(ctx["ote"], supplier)
    if position is None:
        return result(
            UNVERIFIABLE,
            f"Dodavatel pod názvem „{supplier.get('ote_name')}“ není v tabulce OTE nalezen.",
            OTE_LIMIT, "Ověřit název právnické osoby v tabulce OTE.",
        )
    scope = ev.claim_scope(claim, supplier)
    if ev.GAS not in scope.commodities and "plyn" in claim.get("quote", "").casefold():
        scope = ev.ClaimScope(**{**scope.__dict__, "commodities": (ev.ELECTRICITY, ev.GAS)})
    evidence, rows, info = _size_context(supplier, ctx, scope)

    if scope.qualifier:
        return _qualified_size(claim, supplier, ctx, scope, evidence, rows, info)

    if scope.metric != "opm":
        summary = "; ".join(f"{r['text']}: {r['status']}" for r in rows)
        bigger = info["position"] != 1 or (ev.GAS in scope.commodities and info.get("gas_position") not in (None, 1))
        return result(
            UNVERIFIABLE,
            f"{NO_METRIC} Podle odběrných míst (OTE, {info['period']}): {summary}.",
            OTE_LIMIT,
            "Příležitost: podle odběrných míst tvrzení neplatí ve všech komoditách. Lze se zeptat, jakou metriku "
            "dodavatel myslí; vlastní tvrzení o velikosti formulovat s metrikou a zdrojem."
            if bigger else "Tvrzení o velikosti nenapadat; podle odběrných míst je dodavatel první.",
            evidence, rows, opportunity=2 if bigger else False,
        )

    # tvrzení výslovně o odběrných místech: lze vydat verdikt
    statuses = [info["position"] == 1]
    if ev.GAS in scope.commodities:
        if info.get("gas_position") is None:
            return result(
                UNVERIFIABLE, "Tvrzení zahrnuje plyn, ale údaj OTE o plynu pro dodavatele chybí.",
                OTE_LIMIT, "Doplnit údaj o plynu; chybějící údaj není nula.", evidence, rows,
            )
        statuses.append(info["gas_position"] == 1)
    if all(statuses):
        return result(SUPPORTED, "Podle počtu odběrných míst je dodavatel první ve všech uvedených komoditách.",
                      OTE_LIMIT, "Tvrzení o velikosti nenapadat.", evidence, rows)
    if any(statuses):
        return result(PARTIAL, "Podle počtu odběrných míst je dodavatel první jen v části uvedených komodit.",
                      OTE_LIMIT, "Příležitost: tvrzení platí jen pro část komodit.", evidence, rows, opportunity=2)
    return result(CONTRADICTED, "Podle počtu odběrných míst dodavatel není první v žádné z uvedených komodit.",
                  OTE_LIMIT, "Příležitost: tvrzení o prvenství lze zpochybnit veřejnými daty OTE.",
                  evidence, rows, opportunity=3)


def _qualified_size(claim, supplier, ctx, scope, evidence, rows, info):
    """„Největší český / alternativní dodavatel“: pojem, který data OTE nepopisují."""
    qualifier = scope.qualifier
    definition = (ctx.get("definitions") or {}).get("traditional_suppliers") or {}
    traditional = list((claim.get("params") or {}).get("exclude") or definition.get("names") or [])
    measure = ev.opm_measure(ctx, scope.entities, (ev.ELECTRICITY, ev.GAS))
    who = " + ".join(scope.entities)
    if qualifier == "český" or not traditional or measure.total is None:
        missing = (
            "Data OTE neobsahují vlastnictví firem, takže „český dodavatel“ z nich určit nelze."
            if qualifier == "český" else
            (measure.reason or f"Pojem „{qualifier} dodavatel“ nemá v datech oporu.")
        )
        return result(
            UNVERIFIABLE,
            f"{NO_METRIC} {missing} Mezi všemi dodavateli je {supplier['name']} podle odběrných míst elektřiny "
            f"{info['position']}. ({num(info['count'])} OPM, {info['period']}).",
            OTE_LIMIT,
            f"Příležitost: superlativ „největší {qualifier}“ stojí na pojmu, který data nedefinují. Vlastní tvrzení "
            "o velikosti formulovat s konkrétním číslem a zdrojem.",
            evidence, rows, opportunity=True,
        )

    def among(name, names):
        return any(ote_connector.same_supplier(name, n) for n in names)

    bigger = []
    for name, _ in ctx["ote"]["ranking"]:
        if among(name, traditional) or among(name, scope.entities):
            continue
        other = ev.opm_measure(ctx, (name,), (ev.ELECTRICITY, ev.GAS))
        if other.total is not None and other.total > measure.total:
            bigger.append((name, other.total))
    bigger.sort(key=lambda item: -item[1])
    evidence.append({
        "label": f"Součet odběrných míst elektřiny a plynu – {who} ({measure.period}): {num(measure.total)}",
        "value": ("víc mají mimo tradiční skupiny: " + ", ".join(f"{n} ({num(c)})" for n, c in bigger[:3]))
        if bigger else "mimo tradiční skupiny nikdo víc nemá",
        "source": "ote",
    })
    reading = (
        f"Při výkladu týmu („{qualifier}“ = mimo tradiční skupiny: {', '.join(traditional)}) a měření "
        f"odběrnými místy elektřiny a plynu by tvrzení "
        + ("neplatilo: víc jich má " + ", ".join(f"{n} ({num(c)})" for n, c in bigger[:3]) if bigger else "platilo")
        + "."
    )
    return result(
        UNVERIFIABLE,
        f"{NO_METRIC} {reading}",
        OTE_LIMIT + " Výklad pojmu je rozhodnutí týmu, ne údaj z tvrzení; " + (definition.get("note") or ""),
        "Příležitost: podle odběrných míst tvrzení neplatí. Před použitím ověřit, jakou metriku dodavatel myslí."
        if bigger else "Bez akce.",
        evidence, rows, opportunity=3 if bigger else False,
    )


def _monthly_totals(ctx: dict, supplier: dict, commodities=(ev.ELECTRICITY, ev.GAS)) -> list[tuple[str, int]]:
    """Součet OPM po měsících roku jen tam, kde je údaj za všechny komodity (chybějící údaj není nula)."""
    def series(data):
        if not data or "table" not in data:
            return None
        for name, values in data["table"]["suppliers"].items():
            if ote_connector.same_supplier(name, supplier.get("ote_name") or ""):
                return values
        return None

    rows = [series(ctx["ote"] if c == ev.ELECTRICITY else ctx.get("ote_gas")) for c in commodities]
    if any(r is None for r in rows):
        return []
    year = (ctx["ote"].get("table") or {}).get("year") or "rok"
    out = []
    for i in range(12):
        values = [r[i] for r in rows]
        if all(v is not None for v in values):
            out.append((f"{year}-{i + 1:02d}", sum(values)))
    return out


def _claimed_from_quote(quote: str):
    """Vyčte z citátu tvrzený počet a metriku: (počet, "opm" | None). Pro automaticky nalezená tvrzení."""
    found = re.search(
        r"(\d[\d ]*(?:[.,]\d+)?)\s*(milion\w*|mil\.|tis\.|tisíc\w*)?\s*(?:spokojených\s+)?"
        r"(zákazník|odběrných míst|klient|domácnost)",
        quote, re.I,
    )
    if not found:
        return None, None
    number = float(found.group(1).replace(" ", "").replace(",", "."))
    unit = (found.group(2) or "").casefold()
    if unit.startswith("mil"):
        number *= 1_000_000
    elif unit.startswith("tis"):
        number *= 1_000
    return int(number), ("opm" if "odběrných míst" in found.group(3).casefold() else None)


def customer_count(claim, supplier, ctx):
    params = claim.get("params", {})
    claimed = params.get("claimed")
    scope = ev.claim_scope(claim, supplier)
    if claimed is None:
        claimed, parsed_metric = _claimed_from_quote(claim.get("quote", ""))
        if parsed_metric == "opm" and scope.metric == "unspecified":
            scope = ev.ClaimScope(**{**scope.__dict__, "metric": "opm"})
    if not claimed:
        return result(UNVERIFIABLE, "Tvrzení neuvádí konkrétní počet.", OTE_LIMIT, "Bez akce.")
    measure = ev.opm_measure(ctx, scope.entities, scope.commodities)
    evidence = [{
        "label": f"Odběrná místa podle OTE ({measure.period or 'období neurčeno'})",
        "value": (measure.breakdown() or "údaj chybí") + f"; tvrzeno {num(claimed)}",
        "source": "ote",
    }]
    period_note = (
        f" Tvrzení neuvádí datum; porovnává se s posledním zveřejněným měsícem OTE ({measure.period})."
        if not scope.period and measure.period else ""
    )
    if scope.period and measure.period and scope.period != measure.period:
        return result(
            UNVERIFIABLE,
            f"Tvrzení se týká období {scope.period}, OTE je k dispozici za {measure.period}; období se liší.",
            OTE_LIMIT, "Porovnat se stejným obdobím.", evidence,
        )
    if measure.total is None:
        return result(
            UNVERIFIABLE, f"Součet odběrných míst nelze určit. {measure.reason}",
            OTE_LIMIT, "Doplnit chybějící údaj; do té doby tvrzení nehodnotit.", evidence,
        )
    if scope.metric == "opm" and scope.market == "CZ":
        outcome = ev.compare(measure.total, claimed, scope.comparator)
        base = f"OTE eviduje {num(measure.total)} odběrných míst ({measure.breakdown()}) za {measure.period}."
        if outcome is True:
            return result(SUPPORTED, base + f" Tvrzených {num(claimed)} tomu odpovídá.",
                          OTE_LIMIT + period_note, "Tvrzení nenapadat; opírá se o ověřitelnou metriku.", evidence)
        if outcome is False:
            return result(CONTRADICTED, base + f" Tvrzených {num(claimed)} tomu neodpovídá.",
                          OTE_LIMIT + period_note, "Příležitost: tvrzení lze zpochybnit veřejnými daty OTE.",
                          evidence, opportunity=3)
        return result(UNVERIFIABLE, base + f" Rozdíl proti tvrzeným {num(claimed)} je v pásmu nejistoty.",
                      OTE_LIMIT + period_note, "Bez akce.", evidence)

    # tvrzení o zákaznících (nebo přes více zemí): OPM jsou jen kontext
    limitation = (
        OTE_LIMIT + " Jeden zákazník může mít více odběrných míst a tvrzení může zahrnovat jiné služby, "
        "země nebo období." + period_note
    )
    context = f"OTE eviduje {num(measure.total)} odběrných míst ({measure.breakdown()}) za {measure.period}."
    if scope.market != "CZ":
        share = f"{measure.total / claimed * 100:.1f}".replace(".", ",")
        return result(
            UNVERIFIABLE,
            f"Tvrzení sčítá zákazníky ve více zemích. V Česku {context[0].lower() + context[1:]} To je "
            f"{share} % tvrzeného počtu.",
            limitation + " OTE pokrývá jen český trh.",
            "Příležitost: číslo působí na českého zákazníka větší, než jaká je česká část. Vlastní tvrzení "
            "uvádět s českým údajem a zdrojem.",
            evidence, opportunity=2,
        )
    months = _monthly_totals(ctx, supplier, scope.commodities)
    peak = max((value for _, value in months), default=None)
    if peak is not None and claimed > peak:
        return result(
            UNVERIFIABLE,
            f"Tvrzení mluví o zákaznících, OTE eviduje odběrná místa. {context} Tvrzených {num(claimed)} je víc "
            f"než odběrných míst v kterémkoli měsíci roku (nejvíc {num(peak)}). Pokud jde o zákazníky s dodávkou "
            "energie v Česku, číslo nesedí; jiný výklad tvrzení ale vyloučit nelze.",
            limitation,
            "Příležitost: číslo je nejspíš zaokrouhlené nahoru, zastaralé nebo počítá i jiné služby. Před použitím "
            "ověřit, co dodavatel počítá.",
            evidence, opportunity=2,
        )
    return result(
        UNVERIFIABLE,
        f"Tvrzení mluví o zákaznících, OTE eviduje odběrná místa. {context} Řádově to tvrzení neodporuje, "
        "počet zákazníků ale z odběrných míst určit nelze.",
        limitation, "Bez akce. Údaj OTE použít jen jako kontext řádové velikosti.", evidence,
    )


def customer_growth(claim, supplier, ctx):
    params = claim.get("params", {})
    scope = ev.claim_scope(claim, supplier)
    if params.get("superlative"):
        return result(
            UNVERIFIABLE, "Superlativ růstu („nejrychleji“) vyžaduje definici metriky a období.",
            OTE_LIMIT, "Vyžádat si metodiku; případně porovnat růst OPM všech dodavatelů.",
        )
    change = ev.opm_change(ctx["ote"], supplier.get("ote_name") or "")
    if change.abs is None:
        return result(UNVERIFIABLE, f"Změnu počtu odběrných míst nelze určit. {change.reason}", OTE_LIMIT,
                      "Ověřit název právnické osoby a období v tabulce OTE.")
    window = change.window
    evidence = [{
        "label": f"{supplier['ote_name']}: {num(change.first)} → {num(change.last)} OPM elektřiny ({window.label})",
        "value": f"změna {'+' if change.abs >= 0 else ''}{num(change.abs)} OPM ({change.pct:+.1f} %)",
        "source": "ote",
    }]
    limitation = OTE_LIMIT + f" Měří se jen společné období tabulky OTE ({window.label})."
    if scope.metric != "opm":
        return result(
            UNVERIFIABLE,
            "Tvrzení neuvádí, co roste (zákazníci, odběrná místa, objem). Počet odběrných míst elektřiny se "
            f"za {window.label} změnil o {num(change.abs)} ({change.pct:+.1f} %).",
            limitation,
            "Bez akce, pokud odběrná místa rostou; jinak se zeptat, jakou metriku dodavatel myslí."
            if change.abs > 0 else "Příležitost: odběrná místa za období nerostla.",
            evidence, opportunity=False if change.abs > 0 else 2,
        )
    if change.abs > 0:
        return result(SUPPORTED, f"Počet odběrných míst elektřiny za {window.label} vzrostl.",
                      limitation, "Tvrzení o růstu nenapadat.", evidence)
    return result(
        CONTRADICTED, f"Počet odběrných míst elektřiny za {window.label} nevzrostl.", limitation,
        "Příležitost: tvrzení o růstu lze zpochybnit veřejnými daty OTE.", evidence, opportunity=3,
    )


# ---------- zelená a měkká tvrzení ----------

def green_share(claim, supplier, ctx):
    product = (claim.get("params") or {}).get("product")
    proof = [
        p for p in supplier.get("green_evidence") or []
        if product and p.get("product") == product
    ]
    if proof:
        evidence = [
            {"label": f"{p['label']} ({p.get('type', 'prohlášení dodavatele')})",
             "value": f"„{p['value']}“ ({p.get('status', 'z přípravy')})",
             "source": f"web:{supplier['id']}"}
            for p in proof
        ]
        if any(p.get("stale") for p in proof):
            return result(
                UNVERIFIABLE,
                "Prohlášení k produktu, o které se podklad opíral, už na živé stránce není.",
                "Chybějící podklad není důkaz nepravdivosti.",
                "Podklad znovu zachytit z webu dodavatele.", evidence,
            )
        return result(
            UNVERIFIABLE,
            f"Nalezeno prohlášení dodavatele k produktu {product}, že spotřeba je kryta zárukami původu. "
            "Jde o prohlášení samotného dodavatele; podkladové záruky původu jsme nezávisle neověřili.",
            "Prohlášení dodavatele není nezávislý důkaz. Krytí zárukami původu navíc neznamená fyzickou dodávku "
            "zelené elektřiny.",
            "Bez akce. Tvrzení je formulované konkrétně (produkt, záruky původu); tak formulovat i vlastní zelená "
            "tvrzení.",
            evidence,
        )
    return result(
        UNVERIFIABLE,
        "K tvrzení nemáme podklad vázaný na konkrétní produkt a období (záruky původu, certifikát). "
        "Cenová data ERÚ ani celostátní energetický mix zelený původ produktu nedokládají.",
        "Chybějící podklad není důkaz nepravdivosti.",
        "Vyžádat si od dodavatele doklad o zárukách původu. Příležitost: pokud svůj zelený produkt "
        "dokládáme zárukami původu, uvádět to výslovně. Tvrzení předat k posouzení (není to právní závěr).",
        risk=True, risk_reason=GREEN_RISK_REASON, opportunity=True,
    )


def carbon_neutral(claim, supplier, ctx):
    if claim.get("params", {}).get("uses_offsets"):
        finding = "Tvrzení se opírá o kompenzace emisí; jejich metodika a rozsah nejsou veřejně doloženy."
    else:
        finding = "Metodika uhlíkové neutrality není doložena."
    return result(
        UNVERIFIABLE, finding, "Chybějící podklad není důkaz nepravdivosti.",
        "Vyžádat si doklad o kompenzacích (standard, objem, období). Tvrzení předat k právní kontrole.",
        risk=True, risk_reason=GREEN_RISK_REASON, opportunity=True,
    )


def green_generic(claim, supplier, ctx):
    return result(
        UNVERIFIABLE,
        "Obecné zelené tvrzení bez měřitelného údaje nelze ověřit.",
        "Chybějící podklad není důkaz nepravdivosti.",
        "Sledovat; vágní zelená tvrzení mohou nést regulatorní riziko (není to právní závěr).",
        risk=True, risk_reason=GREEN_RISK_REASON,
    )


def decision_stats(supplier: dict, ctx: dict) -> dict | None:
    """Souhrn pravomocných rozhodnutí ERÚ o dodavateli; None, když zdroj není k dispozici."""
    data = (ctx.get("decisions") or {}).get(supplier["id"])
    if not data:
        return None
    items = data["decisions"]
    practices = [d for d in items if d["kind"] == "practice"]
    offences = [d for d in items if d["kind"] in ("practice", "offence")]
    return {"offences": offences, "practices": practices, "all": items,
            "latest_practice": practices[0]["date"] if practices else None,
            "truncated": bool(data.get("truncated"))}


def soft_trust(claim, supplier, ctx):
    finding = "Tvrzení o důvěře nebo férovosti nemá měřitelnou definici, kterou by šlo ověřit veřejnými daty."
    limitation = "Neuvádí zdroj ani metodiku (průzkum, období, vzorek)."
    stats = decision_stats(supplier, ctx)
    if stats is None:
        return result(
            UNVERIFIABLE, finding, limitation,
            "Bez akce. Pokud podobné tvrzení používáme sami, uvádět zdroj průzkumu.",
        )
    offences, practices = stats["offences"], stats["practices"]
    evidence = [
        {"label": f"{d['date']} – {d['title']}", "value": DECISION_KIND[d["kind"]],
         "source": "eru_decisions", "url": d["url"]}
        for d in offences[:6]
    ]
    limitation += (
        " Rozhodnutí o minulém přestupku nedokazuje, že slogan je dnes nepravdivý; z rozhodnutí známe jen "
        "název a datum zveřejnění, ne skutek ani sankci."
    )
    if practices:
        return result(
            UNVERIFIABLE,
            finding + f" ERÚ přitom eviduje {decisions_phrase(len(offences))} o přestupcích tohoto "
            f"dodavatele, z toho se obchodních praktik nebo nepravdivých informací týká {len(practices)} "
            f"(poslední zveřejněno {stats['latest_practice']}).",
            limitation,
            "Příležitost: slogan o důvěryhodnosti stojí vedle pravomocného rozhodnutí regulátora. Před "
            "použitím si rozhodnutí přečíst (skutek, období, sankce).",
            evidence, opportunity=2,
        )
    if offences:
        return result(
            UNVERIFIABLE,
            finding + f" ERÚ eviduje {decisions_phrase(len(offences))} o přestupcích tohoto dodavatele, "
            "žádné se netýká obchodních praktik ani nepravdivých informací.",
            limitation, "Bez akce.", evidence,
        )
    return result(
        UNVERIFIABLE,
        finding + " V načtených výsledcích seznamu pravomocných rozhodnutí ERÚ (hledáno podle názvu "
        "účastníka řízení) jsme žádné rozhodnutí o přestupku tohoto dodavatele nenašli"
        + (", seznam byl ale zkrácen." if stats["truncated"] else "."),
        limitation, "Bez akce. Pokud podobné tvrzení používáme sami, uvádět zdroj průzkumu.",
    )


def other(claim, supplier, ctx):
    return result(
        UNVERIFIABLE,
        "Pro tento typ tvrzení zatím neexistuje ověřovací pravidlo.",
        "Bez pravidla se verdikt nevydává.",
        "Zvážit doplnění pravidla, pokud se tvrzení opakuje.",
    )


RULES = {
    "price_favorable": price_favorable,
    "price_rank": price_rank,
    "price_pct_cheaper": price_pct_cheaper,
    "price_guarantee": price_guarantee,
    "market_size": market_size,
    "customer_count": customer_count,
    "customer_growth": customer_growth,
    "carbon_neutral": carbon_neutral,
    "green_share": green_share,
    "green_generic": green_generic,
    "soft_trust": soft_trust,
    "other": other,
}


def evaluate(claim: dict, supplier: dict, ctx: dict) -> dict:
    rule = RULES.get(claim.get("rule_id"), other)
    return rule(claim, supplier, ctx)
