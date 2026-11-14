"""Deterministic stand-in for the Price / Green / Customer evidence agents.

In live mode an LLM decides which read-only tool to call and with which typed
parameters. In fixture mode that choice is hard-wired per rule ID so the whole
pipeline runs offline, without an API key. Both modes call the same tools and
the same rule engine, so the verdict logic under test is the real one.
"""

from decimal import Decimal

from ..rules import RuleOutcome, engine
from ..schemas import Claim, EvidenceFact, VerdictValue
from ..schemas.claim import (
    CarbonNeutralRule,
    CustomerCountRule,
    CustomerGrowthRule,
    GreenGenericRule,
    GreenShareRule,
    PriceGuaranteeRule,
    PricePctCheaperRule,
    PriceRankRule,
    RatingRule,
    SoftTrustRule,
)
from ..tools import ReferenceStore, calculate_annual_cost
from ..tools.reference import ReferenceRecord

Gathered = tuple[list[EvidenceFact], RuleOutcome]


class FixtureEvidenceAgent:
    def __init__(self, store: ReferenceStore, sample_supplier_ids: list[str]) -> None:
        self._store = store
        self._sample = sample_supplier_ids

    def gather(self, claim: Claim, profile_ids: list[str]) -> Gathered:
        rule = claim.rule
        match rule:
            case PriceRankRule():
                return self._price_rank(claim, rule, profile_ids)
            case PricePctCheaperRule():
                return self._price_pct(claim, rule, profile_ids)
            case PriceGuaranteeRule():
                return self._price_guarantee(claim, rule)
            case GreenShareRule():
                return self._green_share(claim, rule)
            case GreenGenericRule():
                return [], engine.green_generic()
            case CarbonNeutralRule():
                return [], engine.carbon_neutral(uses_offsets=rule.params.uses_offsets)
            case CustomerCountRule():
                return self._customer_count(claim, rule)
            case CustomerGrowthRule():
                return self._customer_growth(claim, rule)
            case RatingRule():
                # No licensed rating source is connected, so there is nothing to compare.
                return [], engine.rating(
                    claimed=rule.params.value, platform=rule.params.platform, observed=None
                )
            case SoftTrustRule():
                return [], engine.soft_trust()
            case _:
                return [], engine.other()

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _fact(
        claim: Claim,
        index: int,
        statement: str,
        record: ReferenceRecord,
        scope: str,
        value: Decimal | None = None,
        unit: str | None = None,
        limitations: tuple[str, ...] = (),
    ) -> EvidenceFact:
        return EvidenceFact(
            evidence_id=f"ev_{claim.id}_{index:02d}",
            statement=statement,
            value=value,
            unit=unit,
            source_url=record.source_url,
            source_ref=record.source_ref,
            captured_at=record.captured_at,
            valid_as_of=record.valid_as_of,
            source_content_hash=record.source_content_hash,
            scope=scope,
            limitations=list(limitations),
        )

    def _costs(
        self, supplier_ids: list[str], profile_ids: list[str]
    ) -> dict[str, dict[str, tuple[Decimal, ReferenceRecord]]]:
        """profile_id -> supplier_id -> (annual trade cost, offer it came from)."""
        offers = {o.supplier_id: o for o in reversed(self._store.get_price_offers(supplier_ids))}
        result: dict[str, dict[str, tuple[Decimal, ReferenceRecord]]] = {}
        for profile_id in profile_ids:
            annual_mwh = self._store.profiles.get(profile_id)
            if annual_mwh is None:
                continue
            result[profile_id] = {
                supplier_id: (calculate_annual_cost(offer, annual_mwh), offer)
                for supplier_id, offer in offers.items()
            }
        return result

    # -- price ------------------------------------------------------------

    def _price_rank(self, claim: Claim, rule: PriceRankRule, profile_ids: list[str]) -> Gathered:
        costs = self._costs(self._sample, profile_ids)
        facts: list[EvidenceFact] = []
        for profile_id, by_supplier in costs.items():
            for supplier_id, (cost, offer) in sorted(by_supplier.items()):
                facts.append(
                    self._fact(
                        claim,
                        len(facts) + 1,
                        f"Roční náklad na obchodní složku, {supplier_id}, profil {profile_id}",
                        offer,
                        f"{len(by_supplier)} srovnatelní dodavatelé, {profile_id}",
                        value=cost,
                        unit="Kč/rok",
                        limitations=("Jde pouze o vybraný vzorek dodavatelů.",),
                    )
                )
        outcome = engine.price_rank(
            supplier_id=claim.supplier_id,
            scope=rule.params.scope,
            rank=rule.params.rank,
            costs_by_profile={
                p: {s: cost for s, (cost, _) in by_supplier.items()} for p, by_supplier in costs.items()
            },
        )
        return facts, outcome

    def _price_pct(self, claim: Claim, rule: PricePctCheaperRule, profile_ids: list[str]) -> Gathered:
        params = rule.params
        if params.profile_id is not None and params.profile_id not in profile_ids:
            return [], RuleOutcome(VerdictValue.UNVERIFIABLE, "price_pct_cheaper-v2",
                                   "Profil uvedený v tvrzení není součástí tohoto auditu.", review_required=True)
        baseline_id = params.baseline_supplier_id
        if baseline_id is None or params.basis is None:
            return [], engine.price_pct_cheaper(claimed_pct=params.pct, basis=params.basis, costs=None)
        wanted_profiles = [params.profile_id] if params.profile_id else profile_ids
        costs = self._costs([claim.supplier_id, baseline_id], wanted_profiles)
        facts: list[EvidenceFact] = []
        pairs: dict[str, tuple[Decimal, Decimal]] = {}
        for profile_id, by_supplier in costs.items():
            if claim.supplier_id not in by_supplier or baseline_id not in by_supplier:
                continue
            own, own_offer = by_supplier[claim.supplier_id]
            base, base_offer = by_supplier[baseline_id]
            pairs[profile_id] = (own, base)
            for supplier_id, cost, offer in (
                (claim.supplier_id, own, own_offer),
                (baseline_id, base, base_offer),
            ):
                facts.append(
                    self._fact(
                        claim,
                        len(facts) + 1,
                        f"Roční náklad na obchodní složku, {supplier_id}, profil {profile_id}",
                        offer,
                        f"srovnání dvou nabídek, {profile_id}",
                        value=cost,
                        unit="Kč/rok",
                    )
                )
        outcome = engine.price_pct_cheaper(claimed_pct=params.pct, basis=params.basis, costs=pairs)
        return facts, outcome

    def _price_guarantee(self, claim: Claim, rule: PriceGuaranteeRule) -> Gathered:
        terms = self._store.get_product_terms(claim.supplier_id, claim.product_id)
        if terms is None:
            return [], engine.price_guarantee(
                claimed_until=rule.params.until, fixed_until=None, exclusions_unclear=False
            )
        until = terms.fixed_until.isoformat() if terms.fixed_until else "neuvedeno"
        fact = self._fact(
            claim, 1, f"Fixace obchodní složky do {until}", terms, f"produkt {terms.product_id}"
        )
        outcome = engine.price_guarantee(
            claimed_until=rule.params.until,
            fixed_until=terms.fixed_until,
            exclusions_unclear=terms.exclusions_unclear,
        )
        return [fact], outcome

    # -- green ------------------------------------------------------------

    def _green_share(self, claim: Claim, rule: GreenShareRule) -> Gathered:
        mix = self._store.get_fuel_mix(claim.supplier_id, claim.product_id)
        if mix is None:
            return [], engine.green_share(
                claimed_pct=rule.params.share_pct,
                renewable_share=None,
                product_specific=False,
                guarantees_documented=False,
            )
        scope = f"produkt {mix.product_id}" if mix.product_specific else "obecný mix, ne konkrétní produkt"
        fact = self._fact(
            claim,
            1,
            "Podíl obnovitelných zdrojů v deklarovaném mixu",
            mix,
            scope,
            value=mix.renewable_share,
            unit="%",
            limitations=() if mix.product_specific else ("Údaj se nevztahuje ke konkrétnímu produktu.",),
        )
        outcome = engine.green_share(
            claimed_pct=rule.params.share_pct,
            renewable_share=mix.renewable_share,
            product_specific=mix.product_specific,
            guarantees_documented=mix.guarantees_documented,
        )
        return [fact], outcome

    # -- customer ---------------------------------------------------------

    def _customer_count(self, claim: Claim, rule: CustomerCountRule) -> Gathered:
        counts = self._store.get_ote_opm(claim.supplier_id)
        if not counts:
            return [], engine.customer_count(
                claimed_count=rule.params.count,
                claim_metric=rule.params.metric,
                observed=None,
                observed_metric=None,
            )
        latest = counts[-1]
        fact = self._fact(
            claim,
            1,
            f"Počet odběrných míst (OPM) za období {latest.period}",
            latest,
            "odběrná místa elektřiny, jedna právnická osoba",
            value=Decimal(latest.opm),
            unit="OPM",
            limitations=("OPM nejsou unikátní zákazníci.",),
        )
        outcome = engine.customer_count(
            claimed_count=rule.params.count,
            claim_metric=rule.params.metric,
            observed=latest.opm,
            observed_metric="opm",
        )
        return [fact], outcome

    def _customer_growth(self, claim: Claim, rule: CustomerGrowthRule) -> Gathered:
        counts = self._store.get_ote_opm(claim.supplier_id)
        facts = [
            self._fact(
                claim,
                index,
                f"Počet odběrných míst (OPM) za období {count.period}",
                count,
                "odběrná místa elektřiny",
                value=Decimal(count.opm),
                unit="OPM",
            )
            for index, count in enumerate(counts[-2:], start=1)
        ]
        outcome = engine.customer_growth(
            claim_metric=rule.params.metric,
            superlative=rule.params.superlative,
            previous=counts[-2].opm if len(counts) >= 2 else None,
            latest=counts[-1].opm if len(counts) >= 2 else None,
        )
        return facts, outcome
