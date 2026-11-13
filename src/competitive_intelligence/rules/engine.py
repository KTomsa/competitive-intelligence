"""Deterministic rule engine.

Every function takes already-validated numbers and returns the initial
verdict. No LLM output can reach these functions except through typed tool
results, and nothing here guesses: a missing or non-comparable input always
ends in `unverifiable`, never in `contradicted`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..schemas import VerdictValue

SUPPORTED = VerdictValue.SUPPORTED
CONTRADICTED = VerdictValue.CONTRADICTED
UNVERIFIABLE = VerdictValue.UNVERIFIABLE

# Percentage points a measured saving may fall short of the claimed one.
PCT_TOLERANCE = Decimal("0.5")
RATING_TOLERANCE = Decimal("0.05")


@dataclass(frozen=True)
class RuleOutcome:
    rule_verdict: VerdictValue
    rule_version: str
    reasoning: str
    limitations: tuple[str, ...] = ()
    possible_regulatory_risk: bool = False
    risk_reason: str | None = None
    review_required: bool = False


def price_rank(
    *,
    supplier_id: str,
    scope: str,
    costs_by_profile: dict[str, dict[str, Decimal]],
) -> RuleOutcome:
    """`costs_by_profile[profile_id][supplier_id]` is the comparable annual trade cost."""
    version = "price_rank-v2"
    profiles = {p: c for p, c in costs_by_profile.items() if supplier_id in c and len(c) > 1}
    if not profiles:
        return RuleOutcome(
            UNVERIFIABLE,
            version,
            "Pro dodavatele chybí srovnatelná nabídka nebo není s čím srovnávat.",
            ("Chybí srovnatelná cenová data.",),
        )
    for profile_id, costs in sorted(profiles.items()):
        own = costs[supplier_id]
        cheaper = sorted(s for s, cost in costs.items() if s != supplier_id and cost < own)
        if cheaper:
            return RuleOutcome(
                CONTRADICTED,
                version,
                f"V profilu {profile_id} existuje srovnatelná levnější nabídka ({', '.join(cheaper)}).",
                ("Srovnává se pouze obchodní složka ceny včetně DPH.",),
            )
    sample_size = max(len(c) for c in profiles.values())
    if scope == "sample":
        return RuleOutcome(
            SUPPORTED,
            version,
            f"Ve srovnávaném vzorku {sample_size} dodavatelů je nabídka nejlevnější ve všech profilech.",
            ("Platí jen pro výslovně uvedený vzorek.",),
        )
    return RuleOutcome(
        UNVERIFIABLE,
        version,
        f"Ve vzorku {sample_size} dodavatelů je nabídka nejlevnější, vzorek ale nepokrývá celý trh.",
        (f"Celotržní superlativ nelze potvrdit ze vzorku {sample_size} dodavatelů.",),
    )


def price_pct_cheaper(
    *,
    claimed_pct: Decimal,
    basis: str | None,
    costs: dict[str, tuple[Decimal, Decimal]] | None,
) -> RuleOutcome:
    """`costs[profile_id] = (own_cost, baseline_cost)` on the basis the claim uses."""
    version = "price_pct_cheaper-v2"
    if basis is None or not costs:
        return RuleOutcome(
            UNVERIFIABLE,
            version,
            "Tvrzení neuvádí srovnávací základ nebo k němu chybí data.",
            ("Bez základu, produktu a cenové složky nelze procento ověřit.",),
        )
    if basis != "annual_trade_cost":
        return RuleOutcome(
            UNVERIFIABLE,
            version,
            "Pro uvedený cenový základ nejsou k dispozici srovnatelná data.",
            ("Ověřit lze zatím jen roční náklad na obchodní složku.",),
        )
    holds: list[bool] = []
    for own, baseline in costs.values():
        if baseline <= 0:
            return RuleOutcome(UNVERIFIABLE, version, "Základ srovnání není kladné číslo.")
        saving = (baseline - own) / baseline * 100
        holds.append(saving >= claimed_pct - PCT_TOLERANCE)
    limitations = ("Počítáno z obchodní složky včetně DPH, ne z celého účtu.",)
    if all(holds):
        return RuleOutcome(
            SUPPORTED, version, "Úspora dosahuje uvedeného procenta ve všech profilech.", limitations
        )
    if not any(holds):
        return RuleOutcome(
            CONTRADICTED,
            version,
            "Úspora nedosahuje uvedeného procenta v žádném profilu.",
            limitations,
        )
    return RuleOutcome(
        UNVERIFIABLE,
        version,
        "Úspora závisí na spotřebě: v některých profilech platí, v jiných ne.",
        (*limitations, "Tvrzení neuvádí, pro jakou spotřebu platí."),
    )


def price_guarantee(
    *, claimed_until: date | None, fixed_until: date | None, exclusions_unclear: bool
) -> RuleOutcome:
    version = "price_guarantee-v2"
    limitation = "Fixace se týká obchodní složky, ne celého účtu."
    if claimed_until is None or fixed_until is None:
        return RuleOutcome(
            UNVERIFIABLE, version, "Chybí datum garance v tvrzení nebo v podmínkách produktu."
        )
    if exclusions_unclear:
        return RuleOutcome(
            UNVERIFIABLE,
            version,
            "Podmínky obsahují nejasné výluky z garance.",
            (limitation,),
            review_required=True,
        )
    if fixed_until >= claimed_until:
        return RuleOutcome(
            SUPPORTED, version, "Podmínky produktu fixaci do uvedeného data potvrzují.", (limitation,)
        )
    return RuleOutcome(
        CONTRADICTED, version, "Podmínky produktu uvádějí kratší fixaci, než tvrzení.", (limitation,)
    )


def green_share(
    *,
    claimed_pct: Decimal,
    renewable_share: Decimal | None,
    product_specific: bool,
    guarantees_documented: bool,
) -> RuleOutcome:
    version = "green_share-v2"
    if renewable_share is None or not product_specific:
        return RuleOutcome(
            UNVERIFIABLE,
            version,
            "K dispozici není mix konkrétního produktu.",
            ("Národní ani dodavatelský mix nedokládá vlastnosti konkrétního produktu.",),
        )
    if not guarantees_documented:
        return RuleOutcome(
            UNVERIFIABLE,
            version,
            "Podíl je deklarovaný, ale bez doložených záruk původu.",
            ("Chybějící záruky původu nejsou důkazem nepravdivosti.",),
        )
    if renewable_share >= claimed_pct:
        return RuleOutcome(
            SUPPORTED, version, "Produktový mix se zárukami původu uvedený podíl dokládá."
        )
    return RuleOutcome(
        CONTRADICTED, version, "Doložený produktový mix je nižší než uvedený podíl."
    )


def green_generic() -> RuleOutcome:
    return RuleOutcome(
        UNVERIFIABLE,
        "green_generic-v2",
        "Obecné ekologické tvrzení bez ověřitelné metriky.",
        ("Vágní formulaci nelze ověřit proti datům.",),
        possible_regulatory_risk=True,
        risk_reason="Obecné environmentální tvrzení bez doložení (směrnice (EU) 2024/825); nutná právní kontrola.",
        review_required=True,
    )


def carbon_neutral(*, uses_offsets: bool | None) -> RuleOutcome:
    version = "carbon_neutral-v2"
    if uses_offsets:
        return RuleOutcome(
            UNVERIFIABLE,
            version,
            "Tvrzení o neutralitě se opírá o kompenzace emisí.",
            ("Metodika kompenzací není veřejně doložena.",),
            possible_regulatory_risk=True,
            risk_reason="Tvrzení o klimatické neutralitě založené na kompenzacích (směrnice (EU) 2024/825); nutná právní kontrola.",
            review_required=True,
        )
    return RuleOutcome(
        UNVERIFIABLE, version, "Metodika uhlíkové neutrality není doložena.", review_required=True
    )


def customer_count(
    *, claimed_count: int, claim_metric: str, observed: int | None, observed_metric: str | None
) -> RuleOutcome:
    version = "customer_count-v2"
    if observed is None or observed_metric != claim_metric:
        return RuleOutcome(
            UNVERIFIABLE,
            version,
            "K dispozici není údaj ve stejné metrice jako tvrzení.",
            ("Počet odběrných míst (OPM) podle OTE není počet unikátních zákazníků.",),
        )
    if observed >= claimed_count:
        return RuleOutcome(SUPPORTED, version, "Údaj ve stejné metrice tvrzení potvrzuje.")
    return RuleOutcome(CONTRADICTED, version, "Údaj ve stejné metrice je nižší než tvrzení.")


def customer_growth(
    *, claim_metric: str, superlative: bool, previous: int | None, latest: int | None
) -> RuleOutcome:
    version = "customer_growth-v2"
    if superlative:
        return RuleOutcome(
            UNVERIFIABLE,
            version,
            "Superlativ růstu vyžaduje srovnání celého trhu.",
            ("Vzorek dodavatelů nepokrývá celý trh.",),
        )
    if claim_metric != "opm" or previous is None or latest is None:
        return RuleOutcome(
            UNVERIFIABLE,
            version,
            "Růst lze ověřit jen u odběrných míst a jen se dvěma obdobími.",
            ("OPM nelze převádět na počet zákazníků.",),
        )
    if latest > previous:
        return RuleOutcome(SUPPORTED, version, "Počet odběrných míst mezi obdobími vzrostl.")
    return RuleOutcome(CONTRADICTED, version, "Počet odběrných míst mezi obdobími nevzrostl.")


def rating(*, claimed: Decimal, platform: str | None, observed: Decimal | None) -> RuleOutcome:
    version = "rating-v2"
    limitation = "Hodnocení na jedné platformě není reprezentativní spokojenost zákazníků."
    if platform is None or observed is None:
        return RuleOutcome(
            UNVERIFIABLE, version, "Tvrzení neuvádí zdroj hodnocení nebo k němu chybí data.", (limitation,)
        )
    if abs(observed - claimed) <= RATING_TOLERANCE:
        return RuleOutcome(SUPPORTED, version, "Hodnota na uvedené platformě odpovídá.", (limitation,))
    return RuleOutcome(CONTRADICTED, version, "Hodnota na uvedené platformě se liší.", (limitation,))


def soft_trust() -> RuleOutcome:
    return RuleOutcome(
        UNVERIFIABLE,
        "soft_trust-v2",
        "Slogan bez testovatelné definice.",
        ("Sankce ERÚ ani jejich absence slogan nepotvrzují ani nevyvracejí.",),
    )


def other() -> RuleOutcome:
    return RuleOutcome(
        UNVERIFIABLE,
        "other-v2",
        "Pro tento typ tvrzení neexistuje ověřovací pravidlo.",
        ("Tvrzení je mimo rozsah katalogu pravidel.",),
    )
