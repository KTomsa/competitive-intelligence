"""Deterministická pravidla. Čísla a verdikty počítá kód, ne jazykový model.

Zásady:
- chybějící nebo nesrovnatelný podklad znamená `unverifiable`, nikdy `contradicted`;
- pozice vůči pásmu ERÚ je nález, ne důkaz klamání;
- vítězství ve vzorku několika dodavatelů neprokazuje prvenství na celém trhu;
- OPM (odběrná místa) nejsou zákazníci.
"""
from __future__ import annotations

import re
from decimal import Decimal

from .connectors import eru as eru_connector

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
    }
    if offer.get("fixation_months") is None:
        out["reason"] = "Produkt nemá fixaci; pásma ERÚ existují jen pro fixované produkty."
        return out
    tariff = offer.get("tariff")
    month = ctx["offer_month"](offer)
    band = eru_connector.select_band(
        ctx["eru"]["rows"], tariff or ctx["profile"]["tariff"], offer["fixation_months"], month
    )
    if band is None:
        out["reason"] = f"ERÚ nemá pásmo pro fixaci {offer['fixation_months']} měsíců a období {month}."
        return out
    out["band"] = band
    excl, incl, unknown = (
        offer.get("price_mwh_excl_vat"), offer.get("price_mwh_incl_vat"), offer.get("price_mwh_unknown_vat")
    )
    if excl is not None:
        out["price"], out["basis"] = excl, "bez DPH"
        if excl < band["low_excl_vat"]:
            out["position"] = "below"
        elif excl <= band["high_excl_vat"]:
            out["position"] = "within"
        else:
            out["position"] = "above"
        out["not_above"] = out["position"] != "above"
    elif incl is not None:
        out["price"], out["basis"] = incl, "s DPH"
        if incl < band["low_incl_vat"]:
            out["position"] = "below"
        elif incl <= band["high_incl_vat"]:
            out["position"] = "within"
        else:
            out["position"] = "above"
        out["not_above"] = out["position"] != "above"
    elif unknown is not None:
        out["price"], out["basis"] = unknown, "základ DPH neuveden"
        if unknown <= band["high_excl_vat"]:
            out["not_above"] = True  # platí při obou výkladech
        elif unknown > band["high_incl_vat"]:
            out["not_above"] = False
            out["position"] = "above"
        out["reason"] = "Stránka neuvádí, zda je cena s DPH; přesnou pozici v pásmu nelze určit."
    else:
        out["reason"] = "Nabídka nemá uvedenou cenu."
        return out
    if tariff is None:
        note = f"Sazba není na stránce uvedena; srovnání předpokládá profil {ctx['profile']['tariff']}."
        out["reason"] = f"{out['reason']} {note}" if out["reason"] else note
    out["eligible"] = tariff is not None and out["not_above"] is not None and out["position"] != "unknown"
    return out


POSITION_LABEL = {"below": "pod pásmem", "within": "v pásmu", "above": "nad pásmem", "unknown": "nelze určit"}


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
    "Pásmo ERÚ je indikativní cena komodity bez stálého měsíčního platu a bez regulovaných složek; "
    "neříká nic o celkovém účtu ani o tom, zda je nabídka nejlevnější na trhu."
)


def price_favorable(claim, supplier, ctx):
    marks = [benchmark_offer(o, ctx) for o in supplier["offers"]]
    eligible = [m for m in marks if m["eligible"]]
    evidence = [band_evidence(m, ctx) for m in marks if m["band"] is not None and m["price"] is not None]
    if not eligible:
        return result(
            UNVERIFIABLE,
            "Pro tohoto dodavatele není k dispozici nabídka, kterou lze srovnat s pásmem ERÚ "
            "(chybí sazba, základ DPH nebo fixace).",
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
    summary = "; ".join(f"{m['product']} {POSITION_LABEL[m['position']]}" for m in eligible)
    if claim.get("params", {}).get("basis") == "customer_saving":
        return result(
            PARTIAL,
            f"Srovnatelné nabídky nejsou nad pásmem ERÚ ({summary}). Skutečná úspora konkrétního zákazníka "
            "ale závisí na jeho současné smlouvě.",
            ERU_LIMIT,
            "Tvrzení o úspoře je pro zákazníka neověřitelné předem; můžeme nabídnout konkrétní kalkulaci úspory.",
            evidence,
            subclaims=[
                {"text": "Cena není nad indikativním pásmem ERÚ", "status": "ověřeno"},
                {"text": "Zákazník přechodem ušetří", "status": "neověřeno – závisí na jeho současné ceně"},
            ],
        )
    return result(
        SUPPORTED,
        f"Všechny srovnatelné nabídky jsou nejvýše na horní hranici pásma ERÚ ({summary}).",
        ERU_LIMIT + " „Výhodná cena“ je zde vyložena jako cena nepřesahující pásmo ERÚ.",
        "Na ceně komodity tohoto konkurenta nenapadat; hledat rozdíl ve stálém platu, podmínkách a službách.",
        evidence,
    )


# ---------- cenové prvenství ----------

def _comparable(offer: dict, ctx: dict) -> bool:
    return (
        offer.get("tariff") == ctx["profile"]["tariff"]
        and offer.get("fixation_months") is not None
        and offer.get("price_mwh_excl_vat") is not None
    )


def _annual_cost(offer: dict, mwh: float) -> float | None:
    if offer.get("monthly_fee_incl_vat") is None or offer.get("price_mwh_incl_vat") is None:
        return None
    fee = Decimal(str(offer["monthly_fee_incl_vat"]))
    price = Decimal(str(offer["price_mwh_incl_vat"]))
    return float((12 * fee + Decimal(str(mwh)) * price).quantize(Decimal("0.01")))


def price_rank(claim, supplier, ctx):
    kind = claim.get("params", {}).get("kind", "superlative")
    own = [o for o in supplier["offers"] if _comparable(o, ctx)]
    sample = [s["name"] for s in ctx["suppliers"] if s["id"] != supplier["id"]]
    sample_note = (
        f"Srovnání pokrývá jen vzorek: {', '.join(sample)}; sazba {ctx['profile']['tariff']}, "
        "stejná délka fixace, cena komodity bez DPH."
    )
    if not own:
        return result(
            UNVERIFIABLE,
            "Dodavatel nemá ve vzorku fixovanou nabídku se známou sazbou a cenou; "
            "například spotový tarif nelze s fixovanými tarify přímo srovnat.",
            sample_note,
            "Nesrovnávat nesrovnatelné. Pokud chceme reagovat, vysvětlit zákazníkům rozdíl mezi spotem a fixací.",
        )
    evidence, cheaper_unit, cheaper_total = [], [], []
    for mine in own:
        for other_supplier in ctx["suppliers"]:
            if other_supplier["id"] == supplier["id"]:
                continue
            for theirs in other_supplier["offers"]:
                if not _comparable(theirs, ctx) or theirs["fixation_months"] != mine["fixation_months"]:
                    continue
                diff = mine["price_mwh_excl_vat"] - theirs["price_mwh_excl_vat"]
                evidence.append({
                    "label": f"Fixace {mine['fixation_months']} měs.: {supplier['name']} {czk(mine['price_mwh_excl_vat'])}/MWh",
                    "value": (
                        f"{other_supplier['name']} – {theirs['product']}: {czk(theirs['price_mwh_excl_vat'])}/MWh "
                        f"(rozdíl {'+' if diff > 0 else ''}{num(diff)} Kč/MWh)"
                    ),
                    "source": f"web:{other_supplier['id']}",
                })
                if diff > 0:
                    cheaper_unit.append((mine, other_supplier, theirs, diff))
                    costs = [(_annual_cost(mine, m), _annual_cost(theirs, m)) for m in ctx["profile"]["annual_mwh"]]
                    if all(a is not None and b is not None and b < a for a, b in costs):
                        cheaper_total.append((mine, other_supplier, theirs, costs))
    if kind == "price_match_guarantee":
        if cheaper_unit:
            mine, other, theirs, diff = cheaper_unit[0]
            finding = (
                f"Jde o závazek dorovnat nižší cenu, ne o tvrzení, že ceník je nejnižší. Veřejně nabízená "
                f"nižší cena komodity existuje: {other['name']} – {theirs['product']} za "
                f"{czk(theirs['price_mwh_excl_vat'])}/MWh proti {czk(mine['price_mwh_excl_vat'])}/MWh "
                f"({mine['product']}), rozdíl {num(diff)} Kč/MWh bez DPH."
            )
            action = (
                "Příležitost: slogan „Garance nejnižší ceny“ nezaručuje nejnižší ceník. V komunikaci můžeme "
                "ukázat konkrétní nižší ceníkovou cenu a nutnost o dorovnání aktivně žádat. Před použitím "
                "ověřit stálé platy v cenících obou produktů."
            )
        else:
            finding = "Ve vzorku jsme nenašli nižší srovnatelnou ceníkovou cenu. Jde o závazek dorovnání, ne o prvenství."
            action = "Bez akce; sledovat ceníky při další kontrole."
        return result(
            UNVERIFIABLE, finding,
            sample_note + " Zda dodavatel garanci skutečně plní, z veřejných dat ověřit nelze. "
            "Stálý měsíční plat není u všech nabídek znám.",
            action, evidence, opportunity=2 if cheaper_unit else False,
        )
    if cheaper_total:
        mine, other, theirs, costs = cheaper_total[0]
        pairs = "; ".join(
            f"{m} MWh/rok: {czk(a)} vs. {czk(b)}" for m, (a, b) in zip(ctx["profile"]["annual_mwh"], costs)
        )
        return result(
            CONTRADICTED,
            f"Existuje levnější srovnatelná nabídka: {other['name']} – {theirs['product']}. "
            f"Roční náklad obchodní části (s DPH) {pairs}.",
            sample_note + " Jde o obchodní část ceny (komodita a stálý plat), ne o celkový účet.",
            "Příležitost: tvrzení o nejnižší ceně lze doložitelně zpochybnit konkrétním protipříkladem. "
            "Před komunikací ověřit oba ceníky a datum platnosti.",
            evidence, opportunity=3,
        )
    if cheaper_unit:
        mine, other, theirs, diff = cheaper_unit[0]
        return result(
            UNVERIFIABLE,
            f"Nižší cena komodity existuje ({other['name']} – {theirs['product']}, o {num(diff)} Kč/MWh bez DPH), "
            "ale stálý měsíční plat není u obou nabídek znám, takže roční náklad nelze srovnat.",
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
    matching = [o for o in supplier["offers"] if months and o.get("fixation_months") == months]
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

def _ote_rank(supplier, ctx):
    ranking = ctx["ote"]["ranking"]
    for position, (name, count) in enumerate(ranking, start=1):
        if name == supplier.get("ote_name"):
            return position, count, ranking
    return None, None, ranking


OTE_LIMIT = "OPM jsou odběrná místa elektřiny, ne zákazníci, objem dodávky ani tržby; plyn tabulka neobsahuje."


def market_size(claim, supplier, ctx):
    position, count, ranking = _ote_rank(supplier, ctx)
    period = ctx["ote"]["source"]["data_period"]
    if position is None:
        return result(
            UNVERIFIABLE,
            f"Dodavatel pod názvem „{supplier.get('ote_name')}“ není v tabulce OTE nalezen.",
            OTE_LIMIT, "Ověřit název právnické osoby v tabulce OTE.",
        )
    top = "; ".join(f"{i}. {n} ({num(c)})" for i, (n, c) in enumerate(ranking[:5], start=1))
    evidence = [
        {"label": f"{supplier['ote_name']}: {num(count)} OPM ({period})",
         "value": f"{position}. místo z {len(ranking)} dodavatelů", "source": "ote"},
        {"label": "Pět největších podle OPM", "value": top, "source": "ote"},
    ]
    params = claim.get("params", {})
    if params.get("scope") == "czech_owned":
        return result(
            UNVERIFIABLE,
            f"Podle OTE je dodavatel {position}. podle počtu odběrných míst elektřiny ({num(count)} OPM, {period}). "
            "Tvrzení se ale omezuje na „české“ dodavatele a OTE vlastnickou strukturu neeviduje.",
            OTE_LIMIT + " Bez definice „českého dodavatele“ nelze pořadí sestavit.",
            "Příležitost: superlativ „největší český“ stojí na nedefinovaném pojmu. Pokud komunikujeme velikost, "
            "uvádět konkrétní číslo a zdroj (OTE).",
            evidence, opportunity=True,
        )
    if position == 1:
        commodities = list(params.get("commodities", ["elektřina"]))
        if "plyn" in claim.get("quote", "").casefold() and "plyn" not in commodities:
            commodities.append("plyn")
        if any(c != "elektřina" for c in commodities):
            return result(
                PARTIAL,
                f"U elektřiny je dodavatel největší podle počtu odběrných míst ({num(count)} OPM, {period}). "
                "Plyn tento zdroj nepokrývá.",
                OTE_LIMIT,
                "Tvrzení o velikosti u elektřiny nenapadat.",
                evidence,
                subclaims=[
                    {"text": "Největší dodavatel elektřiny (podle OPM)", "status": "ověřeno"},
                    {"text": "Největší dodavatel plynu", "status": "neověřeno – mimo pokrytí zdroje"},
                ],
            )
        return result(
            SUPPORTED,
            f"Dodavatel je největší podle počtu odběrných míst elektřiny ({num(count)} OPM, {period}).",
            OTE_LIMIT, "Tvrzení o velikosti nenapadat.", evidence,
        )
    leader, leader_count = ranking[0]
    return result(
        CONTRADICTED,
        f"Podle počtu odběrných míst elektřiny je dodavatel {position}. ({num(count)} OPM); "
        f"největší je {leader} ({num(leader_count)} OPM, {period}).",
        OTE_LIMIT + " Tvrzení může myslet jinou metriku.",
        "Příležitost: tvrzení o prvenství lze zpochybnit veřejnými daty OTE.",
        evidence, opportunity=True,
    )


def customer_count(claim, supplier, ctx):
    position, count, _ = _ote_rank(supplier, ctx)
    period = ctx["ote"]["source"]["data_period"]
    claimed = claim.get("params", {}).get("claimed")
    if claim.get("params", {}).get("metric") == "opm" and count is not None and claimed:
        evidence = [{"label": f"{supplier['ote_name']}: {num(count)} OPM elektřiny ({period})",
                     "value": f"tvrzeno {num(claimed)} OPM", "source": "ote"}]
        if count >= claimed:
            return result(SUPPORTED, "Počet odběrných míst podle OTE tvrzení potvrzuje.",
                          OTE_LIMIT, "Tvrzení nenapadat.", evidence)
        return result(CONTRADICTED, "Počet odběrných míst podle OTE je nižší než tvrzení.", OTE_LIMIT,
                      "Příležitost: tvrzení lze zpochybnit veřejnými daty OTE.", evidence, opportunity=3)
    evidence = []
    context = "Dodavatel není v tabulce OTE nalezen."
    if count is not None:
        evidence.append({
            "label": f"{supplier['ote_name']}: {num(count)} OPM elektřiny ({period})",
            "value": f"tvrzený počet zákazníků: {num(claimed)}" if claimed else "tvrzený počet neuveden",
            "source": "ote",
        })
        context = f"OTE eviduje {num(count)} odběrných míst elektřiny ({period})."
    return result(
        UNVERIFIABLE,
        f"{context} Počet zákazníků z toho odvodit nelze: zákazník může mít více odběrných míst "
        "a tvrzení může zahrnovat i plyn.",
        OTE_LIMIT,
        "Bez akce. Údaj OTE použít jen jako kontext řádové velikosti.",
        evidence,
    )


def customer_growth(claim, supplier, ctx):
    params = claim.get("params", {})
    table = ctx["ote"].get("table") or {}
    series = (table.get("suppliers") or {}).get(supplier.get("ote_name")) or []
    filled = [(i + 1, v) for i, v in enumerate(series) if v is not None]
    year = table.get("year") or "rok neuveden"
    if params.get("superlative"):
        return result(
            UNVERIFIABLE, "Superlativ růstu („nejrychleji“) vyžaduje definici metriky a období.",
            OTE_LIMIT, "Vyžádat si metodiku; případně porovnat růst OPM všech dodavatelů.",
        )
    if len(filled) < 2:
        return result(UNVERIFIABLE, "OTE nemá pro dodavatele alespoň dvě období.", OTE_LIMIT,
                      "Ověřit název právnické osoby v tabulce OTE.")
    (m0, first), (m1, last) = filled[0], filled[-1]
    change = last - first
    sign = "+" if change >= 0 else ""
    evidence = [{
        "label": f"{supplier['ote_name']}: {num(first)} OPM ({year}-{m0:02d}) → {num(last)} OPM ({year}-{m1:02d})",
        "value": f"změna {sign}{num(change)} OPM ({change / first * 100:+.1f} %)",
        "source": "ote",
    }]
    limitation = OTE_LIMIT + " Růst se měří jen v rámci roku, který OTE na stránce zveřejňuje."
    if change > 0:
        return result(SUPPORTED, "Počet odběrných míst elektřiny v průběhu roku vzrostl.",
                      limitation, "Tvrzení o růstu nenapadat.", evidence)
    return result(
        CONTRADICTED, "Počet odběrných míst elektřiny v průběhu roku nevzrostl.", limitation,
        "Příležitost: tvrzení o růstu lze zpochybnit veřejnými daty OTE.", evidence, opportunity=3,
    )


# ---------- zelená a měkká tvrzení ----------

def green_share(claim, supplier, ctx):
    proof = supplier.get("green_evidence") or []
    if proof:
        evidence = [{"label": p["label"], "value": p["value"], "source": f"web:{supplier['id']}"} for p in proof]
        return result(
            SUPPORTED,
            "K produktu existuje doložený podklad o původu elektřiny.",
            "Podklad pochází od dodavatele; nebyl nezávisle auditován.",
            "Bez akce.", evidence,
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


def soft_trust(claim, supplier, ctx):
    return result(
        UNVERIFIABLE,
        "Tvrzení o důvěře nebo férovosti nemá měřitelnou definici, kterou by šlo ověřit veřejnými daty.",
        "Neuvádí zdroj ani metodiku (průzkum, období, vzorek).",
        "Bez akce. Pokud podobné tvrzení používáme sami, uvádět zdroj průzkumu.",
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
