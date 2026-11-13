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
    }
    if offer.get("stale"):
        out["reason"] = "Cena z přípravy už na živé stránce není; nabídku je potřeba znovu zachytit."
        return out
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


POSITION_SHORT = {"below": "pod pásmem", "within": "v pásmu", "above": "nad pásmem", "unknown": "nelze určit"}
POSITION_LABEL = {
    "below": "pod pásmem (nejlevnější čtvrtina nabídek na trhu)",
    "within": "v pásmu (mezi 25. a 50. percentilem nabídek)",
    "above": "nad pásmem (dražší než přibližně polovina nabídek)",
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
    "Pásmo ERÚ odpovídá zhruba 25. až 50. percentilu fixovaných nabídek na trhu (dolní mez = hranice "
    "nejlevnější čtvrtiny, horní mez = přibližně medián). Je to cena komodity bez stálého měsíčního "
    "platu a bez regulovaných složek; "
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
    summary = "; ".join(f"{m['product']} {POSITION_SHORT[m['position']]}" for m in eligible)
    all_below = all(m["position"] == "below" for m in eligible)
    if all_below:
        level = (
            "Všechny srovnatelné nabídky jsou pod pásmem ERÚ, tedy podle ERÚ mezi nejlevnější čtvrtinou "
            "fixovaných nabídek na trhu"
        )
    else:
        level = "Srovnatelné nabídky nepřesahují horní hranici pásma ERÚ (přibližně medián trhu)"
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
        action = "Cenou komodity tohoto konkurenta nepodbízet; odlišit se stálým platem, podmínkami a službami."
    else:
        action = "Konkurent je cenou v běžném pásmu, ne mezi nejlevnějšími; na ceně komodity s ním lze soutěžit."
    return result(
        SUPPORTED,
        f"{level} ({summary}).",
        ERU_LIMIT + " „Výhodná cena“ je zde vyložena jako cena nepřesahující pásmo ERÚ.",
        action,
        evidence,
    )


# ---------- cenové prvenství ----------

def _comparable(offer: dict, ctx: dict) -> bool:
    return (
        not offer.get("stale")
        and not offer.get("stepped")  # produkty s postupně se měnící cenou nelze srovnávat jednou cenou
        and offer.get("tariff") == ctx["profile"]["tariff"]
        and offer.get("fixation_months") is not None
        and offer.get("price_mwh_excl_vat") is not None
    )


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
CUSTOMER_TOLERANCE = 1.10  # o kolik smí tvrzený počet zákazníků převýšit součet OPM, než jde o rozpor


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


def _combined_ranking(ctx: dict) -> list[tuple[str, int]]:
    """Součet OPM elektřiny a plynu podle dodavatele."""
    ele = ctx["ote"]["ranking"]
    gas = (ctx.get("ote_gas") or {}).get("ranking") or []
    totals = []
    for name, count in ele:
        extra = next((c for g, c in gas if ote_connector.same_supplier(g, name)), 0)
        totals.append((name, count + extra))
    for g, c in gas:
        if not any(ote_connector.same_supplier(g, name) for name, _ in ele):
            totals.append((g, c))
    return sorted(totals, key=lambda p: -p[1])


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


def market_size(claim, supplier, ctx):
    position, count, ranking = _ote_position(ctx["ote"], supplier)
    period = ctx["ote"]["source"]["data_period"]
    if position is None:
        return result(
            UNVERIFIABLE,
            f"Dodavatel pod názvem „{supplier.get('ote_name')}“ není v tabulce OTE nalezen.",
            OTE_LIMIT, "Ověřit název právnické osoby v tabulce OTE.",
        )
    evidence = [
        {"label": f"Elektřina – {supplier['ote_name']}: {num(count)} OPM ({period})",
         "value": f"{position}. místo z {len(ranking)} dodavatelů", "source": "ote"},
        {"label": "Pět největších v elektřině podle OPM", "value": _top(ranking), "source": "ote"},
    ]
    params = claim.get("params", {})
    limitation = OTE_LIMIT + " Tvrzení může myslet jinou metriku."
    if params.get("scope") in ("czech_owned", "qualified"):
        qualifier = params.get("qualifier", "český")
        merged = [supplier["ote_name"]] + list(params.get("merged_with", []))
        definition = (ctx.get("definitions") or {}).get("traditional_suppliers") or {}
        traditional = list(params.get("exclude") or definition.get("names") or [])
        if not traditional:
            return result(
                UNVERIFIABLE,
                f"Podle OTE je dodavatel {position}. podle počtu odběrných míst elektřiny ({num(count)} OPM, "
                f"{period}). Tvrzení se ale omezuje na „{qualifier}“ dodavatele a OTE takové členění neeviduje.",
                OTE_LIMIT + f" Bez definice pojmu „{qualifier} dodavatel“ nelze pořadí sestavit.",
                f"Příležitost: superlativ „největší {qualifier}“ stojí na nedefinovaném pojmu. Pokud komunikujeme "
                "velikost, uvádět konkrétní číslo a zdroj (OTE).",
                evidence, opportunity=True,
            )

        def among(name, names):
            return any(ote_connector.same_supplier(name, n) for n in names)

        def names_of(items):
            return ", ".join(f"{n} ({num(c)})" for n, c in items[:3])

        combined = _combined_ranking(ctx)
        own_total = sum(c for name, c in combined if among(name, merged))
        who = " + ".join(merged)
        bigger_all = [(n, c) for n, c in combined if c > own_total and not among(n, merged)]
        bigger_alt = [(n, c) for n, c in bigger_all if not among(n, traditional)]
        readings = [
            ("všichni dodavatelé", bigger_all),
            ("dodavatelé mimo tradiční skupiny", bigger_alt),
        ]
        subclaims = [
            {"text": f"Výklad „{label}“",
             "status": "platí" if not items else f"neplatí – víc odběrných míst má {names_of(items)}"}
            for label, items in readings
        ]
        evidence.append({
            "label": f"Součet odběrných míst elektřiny a plynu – {who}: {num(own_total)}",
            "value": ("větší: " + names_of(bigger_all)) if bigger_all else "nikdo není větší",
            "source": "ote",
        })
        limitation = (
            OTE_LIMIT + f" Pojem „{qualifier} dodavatel“ tvrzení nedefinuje, proto se posuzuje podle výkladů "
            "stanovených týmem. " + (definition.get("note") or "") + f" Za tradiční se berou: "
            f"{', '.join(traditional)}. Velikost se měří součtem odběrných míst "
            "elektřiny a plynu; objem dodané energie ani vlastnictví firem v datech nejsou."
        )
        base = f"Podle OTE má {who} dohromady {num(own_total)} odběrných míst elektřiny a plynu."
        if not bigger_all:
            return result(
                SUPPORTED, base + " Nikdo nemá víc, tvrzení platí při obou výkladech.",
                limitation, "Tvrzení o velikosti nenapadat.", evidence, subclaims,
            )
        if bigger_alt:
            return result(
                CONTRADICTED,
                base + f" Víc jich mají i dodavatelé mimo tradiční skupiny: {names_of(bigger_alt)}. "
                "Tvrzení neplatí při žádném z výkladů.",
                limitation,
                "Příležitost: tvrzení o prvenství lze zpochybnit čísly OTE, s výhradou, že dodavatel může "
                "myslet objem dodané energie.",
                evidence, subclaims, opportunity=3,
            )
        return result(
            PARTIAL,
            base + f" Mezi všemi dodavateli jsou větší {names_of(bigger_all)}; mimo tradiční dodavatele je ale "
            "největší.",
            limitation,
            "Příležitost: tvrzení platí jen při užším výkladu. Formulace s konkrétním číslem a zdrojem by "
            "obstála lépe.",
            evidence, subclaims, opportunity=2,
        )
    commodities = list(params.get("commodities", ["elektřina"]))
    if "plyn" in claim.get("quote", "").casefold() and "plyn" not in commodities:
        commodities.append("plyn")
    wants_gas = any(c != "elektřina" for c in commodities)
    leader, leader_count = ranking[0]
    if not wants_gas:
        if position == 1:
            return result(
                SUPPORTED,
                f"Dodavatel je největší podle počtu odběrných míst elektřiny ({num(count)} OPM, {period}).",
                limitation, "Tvrzení o velikosti nenapadat.", evidence,
            )
        return result(
            CONTRADICTED,
            f"Podle počtu odběrných míst elektřiny je dodavatel {position}. ({num(count)} OPM); "
            f"největší je {leader} ({num(leader_count)} OPM, {period}).",
            limitation, "Příležitost: tvrzení o prvenství lze zpochybnit veřejnými daty OTE.",
            evidence, opportunity=3,
        )

    gas_data = ctx.get("ote_gas")
    gas_pos, gas_count, gas_ranking = _ote_position(gas_data, supplier)
    if not gas_data or gas_pos is None:
        if position == 1:
            return result(
                PARTIAL,
                f"U elektřiny je dodavatel největší podle počtu odběrných míst ({num(count)} OPM, {period}). "
                "Údaj o plynu není k dispozici.",
                limitation, "Tvrzení o velikosti u elektřiny nenapadat.", evidence,
                subclaims=[
                    {"text": "Největší dodavatel elektřiny (podle OPM)", "status": "ověřeno"},
                    {"text": "Největší dodavatel plynu", "status": "neověřeno – údaj není k dispozici"},
                ],
            )
        return result(
            CONTRADICTED,
            f"Podle počtu odběrných míst elektřiny je dodavatel {position}. ({num(count)} OPM); "
            f"největší je {leader} ({num(leader_count)} OPM, {period}).",
            limitation, "Příležitost: tvrzení o prvenství lze zpochybnit veřejnými daty OTE.",
            evidence, opportunity=3,
        )
    gas_period = gas_data["source"]["data_period"]
    gas_leader, gas_leader_count = gas_ranking[0]
    gas_leader = _full_name(gas_leader, ctx)
    combined = _combined_ranking(ctx)
    comb_pos, comb_count = next(
        ((i, c) for i, (name, c) in enumerate(combined, start=1)
         if ote_connector.same_supplier(name, supplier["ote_name"])),
        (None, None),
    )
    evidence += [
        {"label": f"Plyn – {supplier['ote_name']}: {num(gas_count)} OPM ({gas_period})",
         "value": f"{gas_pos}. místo z {len(gas_ranking)} dodavatelů", "source": "ote_gas"},
        {"label": "Pět největších v plynu podle OPM", "value": _top(gas_ranking, ctx), "source": "ote_gas"},
        {"label": f"Součet elektřina + plyn: {num(comb_count)} OPM",
         "value": f"{comb_pos}. místo; pět největších: {_top(combined)}", "source": "ote"},
    ]

    def status(pos, who=None):
        if pos == 1:
            return "ověřeno"
        return f"v rozporu s daty OTE – {pos}. místo" + (f", největší je {who}" if who else "")

    subclaims = [
        {"text": "Největší dodavatel elektřiny (podle OPM)", "status": status(position, leader)},
        {"text": "Největší dodavatel plynu (podle OPM)", "status": status(gas_pos, gas_leader)},
        {"text": "Největší v součtu elektřiny a plynu", "status": status(comb_pos, combined[0][0])},
    ]
    if position == 1 and gas_pos == 1:
        return result(
            SUPPORTED,
            f"Dodavatel je největší v elektřině ({num(count)} OPM) i v plynu ({num(gas_count)} OPM).",
            limitation, "Tvrzení o velikosti nenapadat.", evidence, subclaims,
        )
    if position == 1 or comb_pos == 1:
        return result(
            PARTIAL,
            f"V elektřině je dodavatel {position}. ({num(count)} OPM, {period}). V plynu je {gas_pos}. "
            f"({num(gas_count)} OPM, {gas_period}); největší v plynu je {gas_leader} ({num(gas_leader_count)} OPM). "
            f"V součtu elektřiny a plynu je {comb_pos}. ({num(comb_count)} OPM).",
            limitation,
            "Příležitost: tvrzení platí pro elektřinu a pro součet obou komodit, ne pro plyn samostatně. "
            "Kdo je silný v plynu, může to doložit přesnými čísly OTE.",
            evidence, subclaims, opportunity=2,
        )
    return result(
        CONTRADICTED,
        f"Dodavatel není největší v elektřině ({position}. místo), v plynu ({gas_pos}. místo) "
        f"ani v součtu ({comb_pos}. místo).",
        limitation, "Příležitost: tvrzení o prvenství lze zpochybnit veřejnými daty OTE.",
        evidence, subclaims, opportunity=3,
    )


def _monthly_totals(ctx: dict, supplier: dict) -> list[tuple[str, int]]:
    """Součet OPM elektřiny a plynu dodavatele po měsících roku: [(RRRR-MM, počet)]."""
    def series(data):
        if not data or "table" not in data:
            return [None] * 12
        for name, values in data["table"]["suppliers"].items():
            if ote_connector.same_supplier(name, supplier.get("ote_name") or ""):
                return values
        return [None] * 12

    ele, gas = series(ctx["ote"]), series(ctx.get("ote_gas"))
    year = (ctx["ote"].get("table") or {}).get("year") or "rok"
    return [(f"{year}-{i + 1:02d}", e + (g or 0)) for i, (e, g) in enumerate(zip(ele, gas)) if e is not None]


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
    claimed, metric = params.get("claimed"), params.get("metric")
    if claimed is None:
        claimed, parsed_metric = _claimed_from_quote(claim.get("quote", ""))
        metric = metric or parsed_metric
    _, ele, _ = _ote_position(ctx["ote"], supplier)
    gas_data = ctx.get("ote_gas")
    _, gas, _ = _ote_position(gas_data, supplier)
    period = ctx["ote"]["source"]["data_period"]
    evidence = []
    if ele is not None:
        evidence.append({"label": f"Elektřina – {supplier['ote_name']}: {num(ele)} OPM ({period})",
                         "value": f"tvrzeno: {num(claimed)}" if claimed else "tvrzený počet neuveden",
                         "source": "ote"})
    if gas is not None:
        evidence.append({"label": f"Plyn – {supplier['ote_name']}: {num(gas)} OPM "
                                  f"({gas_data['source']['data_period']})",
                         "value": "druhá komodita téhož dodavatele", "source": "ote_gas"})
    if metric == "opm" and ele is not None and claimed:
        # tvrzení mluví výslovně o odběrných místech: lze je porovnat přímo s evidencí OTE
        merged = [supplier["ote_name"]] + list(params.get("merged_with", []))
        total, parts = 0, []
        for name in merged:
            entity = {"ote_name": name}
            _, e, _ = _ote_position(ctx["ote"], entity)
            _, g, _ = _ote_position(gas_data, entity)
            total += (e or 0) + (g or 0)
            parts.append(f"{name}: {num(e or 0)} elektřina" + (f" + {num(g)} plyn" if g else ""))
        breakdown = "; ".join(parts)
        opm_evidence = [{"label": f"Součet odběrných míst podle OTE: {num(total)}",
                         "value": f"{breakdown}; tvrzeno {num(claimed)}", "source": "ote"}]
        limit = OTE_LIMIT + ("" if gas_data else " Údaj o plynu není k dispozici, počítá se jen elektřina.")
        if total >= claimed:
            return result(
                SUPPORTED,
                f"OTE eviduje {num(total)} odběrných míst ({breakdown}). Tvrzených {num(claimed)} tedy platí.",
                limit, "Tvrzení nenapadat; je formulované přesně a opírá se o ověřitelnou metriku.", opm_evidence,
            )
        return result(
            CONTRADICTED,
            f"OTE eviduje {num(total)} odběrných míst ({breakdown}), tedy méně než tvrzených {num(claimed)}.",
            limit, "Příležitost: tvrzení lze zpochybnit veřejnými daty OTE.", opm_evidence, opportunity=3,
        )
    if ele is None or not claimed:
        return result(
            UNVERIFIABLE,
            "Dodavatel není v tabulce OTE nalezen nebo tvrzení neuvádí konkrétní počet.",
            OTE_LIMIT, "Ověřit název právnické osoby v tabulce OTE.", evidence,
        )
    limitation = (
        OTE_LIMIT + " Jeden zákazník může mít více odběrných míst a tvrzení může zahrnovat i jiné služby "
        "než dodávku energií."
    )
    if not gas_data:
        return result(
            UNVERIFIABLE,
            f"OTE eviduje {num(ele)} odběrných míst elektřiny ({period}); údaj o plynu není k dispozici, "
            "takže celkový počet nelze posoudit.",
            limitation, "Bez akce. Údaj OTE použít jen jako kontext řádové velikosti.", evidence,
        )
    total = ele + (gas or 0)
    evidence.append({"label": f"Součet elektřina + plyn: {num(total)} OPM",
                     "value": f"tvrzeno {num(claimed)} zákazníků", "source": "ote"})
    parts = f"{num(ele)} odběrných míst elektřiny a {num(gas or 0)} plynu, dohromady {num(total)}"
    markets = params.get("includes_other_markets")
    if markets:
        share = f"{total / claimed * 100:.1f}".replace(".", ",")
        return result(
            UNVERIFIABLE,
            f"Tvrzení sčítá zákazníky ve více zemích ({markets}). V Česku eviduje OTE {parts}, "
            f"tedy nejvýše {share} % tvrzeného počtu.",
            limitation + " OTE pokrývá jen český trh, zahraniční část ověřit nelze.",
            "Příležitost: číslo působí na českého zákazníka mnohem větší, než jaká je česká část. "
            "Pokud komunikujeme velikost, uvádět český údaj a zdroj.",
            evidence, opportunity=2,
        )
    if claimed <= total:
        return result(
            PARTIAL,
            f"OTE eviduje {parts}. Tvrzených {num(claimed)} zákazníků je s tím řádově v souladu "
            "(zákazníků s dodávkou energií nemůže být víc než odběrných míst).",
            limitation, "Tvrzení o velikosti nenapadat.", evidence,
            subclaims=[
                {"text": "Řádová velikost odpovídá počtu odběrných míst", "status": "ověřeno"},
                {"text": "Jde o počet zákazníků, ne odběrných míst", "status": "neověřeno – OTE zákazníky neeviduje"},
            ],
        )
    months = _monthly_totals(ctx, supplier)
    trend = ""
    if len(months) >= 2:
        peak_month, peak = max(months, key=lambda m: m[1])
        first, last = months[0][1], months[-1][1]
        if claimed > peak:
            trend = (
                f" Tvrzený počet převyšuje součet odběrných míst v každém měsíci roku (nejvíc {num(peak)} "
                f"v {peak_month})"
            )
            if last < first:
                change = f"{(last - first) / first * 100:+.1f}".replace(".", ",")
                trend += f" a počet od začátku roku klesá ({change} %)"
            trend += "."
    if claimed > total * CUSTOMER_TOLERANCE:
        return result(
            CONTRADICTED,
            f"OTE eviduje {parts}. Tvrzených {num(claimed)} zákazníků je víc než všech odběrných míst "
            "dodavatele; zákazníků s dodávkou energií nemůže být víc než odběrných míst." + trend,
            limitation,
            "Příležitost: tvrzený počet zákazníků neodpovídá veřejným datům OTE. Před použitím ověřit, "
            "zda dodavatel nepočítá i jiné služby.",
            evidence, opportunity=3,
        )
    return result(
        UNVERIFIABLE,
        f"OTE eviduje {parts}. Tvrzených {num(claimed)} zákazníků je nad tímto součtem, rozdíl je ale "
        "v toleranci zaokrouhlení, takže to rozpor není." + trend,
        limitation,
        "Příležitost: číslo je nejspíš zaokrouhlené nahoru nebo zastaralé. Lze na to upozornit daty OTE; "
        "u vlastních tvrzení uvádět aktuální počet a datum." if trend else "Bez akce.",
        evidence, opportunity=2 if trend else False,
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
        evidence = [
            {"label": p["label"], "value": f"„{p['value']}“ ({p.get('status', 'z přípravy')})",
             "source": f"web:{supplier['id']}"}
            for p in proof
        ]
        if any(p.get("stale") for p in proof):
            return result(
                UNVERIFIABLE,
                "Prohlášení o krytí zárukami původu, o které se podklad opíral, už na živé stránce není.",
                "Chybějící podklad není důkaz nepravdivosti.",
                "Podklad znovu zachytit z webu dodavatele.", evidence,
            )
        return result(
            SUPPORTED,
            "Dodavatel k produktu výslovně uvádí, že spotřeba je kryta zárukami původu. Tvrzení je doložené "
            "podkladem vázaným na konkrétní produkt.",
            "Podklad je prohlášení dodavatele; samotné záruky původu v evidenci OTE jsme neověřovali. "
            "Krytí zárukami původu neznamená fyzickou dodávku zelené elektřiny.",
            "Bez akce. Dobrý příklad, jak zelené tvrzení formulovat doložitelně a bez přehánění.",
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
            "latest_practice": practices[0]["date"] if practices else None}


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
        finding + " ERÚ u tohoto dodavatele neeviduje žádné pravomocné rozhodnutí o přestupku.",
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
