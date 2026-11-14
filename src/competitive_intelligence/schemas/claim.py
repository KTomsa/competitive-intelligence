"""Source and Claim contracts plus the closed rule catalogue (docs/RULE_CATALOG.md)."""

import re
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, TypeAdapter, field_validator, model_validator

from .common import ClaimType, Origin, StrictModel

SHA256_PATTERN = r"^[0-9a-f]{64}$"


def require_https(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value):
            raise ValueError
    except ValueError:
        raise ValueError("URL must be an HTTPS URL without credentials or a nonstandard port") from None
    return value


class Source(StrictModel):
    source_id: str
    supplier_id: str
    source_url: str
    origin: Origin
    fetch_was_live: bool
    captured_at: AwareDatetime
    checked_at: AwareDatetime
    valid_as_of: date
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    canonical_hash: str = Field(pattern=SHA256_PATTERN)
    parser_version: str
    fallback_reason: str | None = None
    baseline_approved_at: AwareDatetime | None = None

    _https = field_validator("source_url")(require_https)


# --- Rule parameters -------------------------------------------------------
# One params model per rule ID. The discriminated union below is the only
# place where a rule ID becomes valid; anything else is coerced to `other`.


class EmptyParams(StrictModel):
    pass


class PriceRankParams(StrictModel):
    scope: Literal["cz_market", "sample"] = "cz_market"
    rank: int = Field(default=1, ge=1)


class PricePctCheaperParams(StrictModel):
    pct: Decimal = Field(gt=0, le=100)
    baseline_supplier_id: str | None = None
    # What the percentage is a share of. Without it the saving cannot be checked.
    basis: Literal["unit_price", "annual_trade_cost", "total_bill"] | None = None
    profile_id: str | None = None


class PriceGuaranteeParams(StrictModel):
    until: date | None = None


class GreenShareParams(StrictModel):
    share_pct: Decimal = Field(gt=0, le=100)


class CarbonNeutralParams(StrictModel):
    uses_offsets: bool | None = None


class CustomerCountParams(StrictModel):
    count: int = Field(gt=0)
    metric: Literal["customers", "opm"] = "customers"


class CustomerGrowthParams(StrictModel):
    metric: Literal["customers", "opm"] = "customers"
    superlative: bool = False


class RatingParams(StrictModel):
    value: Decimal = Field(ge=0)
    platform: str | None = None


class PriceRankRule(StrictModel):
    id: Literal["price_rank"]
    params: PriceRankParams = PriceRankParams()


class PricePctCheaperRule(StrictModel):
    id: Literal["price_pct_cheaper"]
    params: PricePctCheaperParams


class PriceGuaranteeRule(StrictModel):
    id: Literal["price_guarantee"]
    params: PriceGuaranteeParams = PriceGuaranteeParams()


class GreenShareRule(StrictModel):
    id: Literal["green_share"]
    params: GreenShareParams


class GreenGenericRule(StrictModel):
    id: Literal["green_generic"]
    params: EmptyParams = EmptyParams()


class CarbonNeutralRule(StrictModel):
    id: Literal["carbon_neutral"]
    params: CarbonNeutralParams = CarbonNeutralParams()


class CustomerCountRule(StrictModel):
    id: Literal["customer_count"]
    params: CustomerCountParams


class CustomerGrowthRule(StrictModel):
    id: Literal["customer_growth"]
    params: CustomerGrowthParams = CustomerGrowthParams()


class RatingRule(StrictModel):
    id: Literal["rating"]
    params: RatingParams


class SoftTrustRule(StrictModel):
    id: Literal["soft_trust"]
    params: EmptyParams = EmptyParams()


class OtherRule(StrictModel):
    id: Literal["other"]
    params: EmptyParams = EmptyParams()


Rule = Annotated[
    PriceRankRule
    | PricePctCheaperRule
    | PriceGuaranteeRule
    | GreenShareRule
    | GreenGenericRule
    | CarbonNeutralRule
    | CustomerCountRule
    | CustomerGrowthRule
    | RatingRule
    | SoftTrustRule
    | OtherRule,
    Field(discriminator="id"),
]

RULE_IDS: frozenset[str] = frozenset(
    {
        "price_rank",
        "price_pct_cheaper",
        "price_guarantee",
        "green_share",
        "green_generic",
        "carbon_neutral",
        "customer_count",
        "customer_growth",
        "rating",
        "soft_trust",
        "other",
    }
)

_rule_adapter: TypeAdapter[Rule] = TypeAdapter(Rule)


def coerce_rule(raw: Any) -> Rule:
    """Validate an extractor-proposed rule against the closed catalogue.

    An unknown rule ID becomes `other`. Invalid params for a known ID raise a
    ValidationError so the caller can retry or drop the claim.
    """
    if not isinstance(raw, dict) or raw.get("id") not in RULE_IDS:
        return OtherRule(id="other")
    return _rule_adapter.validate_python(raw)


class Claim(StrictModel):
    id: str
    supplier_id: str
    quote: str = Field(min_length=1)
    source_id: str
    source_url: str
    source_ref: str
    captured_at: AwareDatetime
    origin: Origin
    human_reviewed: bool = False
    product_id: str | None = None
    product_binding_ref: str | None = None
    product_binding_reviewed_at: AwareDatetime | None = None
    claim_type: ClaimType
    rule: Rule

    _https = field_validator("source_url")(require_https)

    @model_validator(mode="after")
    def reviewed_product_binding(self):
        if (self.product_binding_ref is None) != (self.product_binding_reviewed_at is None):
            raise ValueError("Product binding requires both reference and review timestamp")
        if self.product_binding_ref is not None and self.product_id is None:
            raise ValueError("Product binding requires a product")
        return self


_WHITESPACE = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def quote_in_text(quote: str, canonical_text: str) -> bool:
    """A quote counts only if it appears literally in the captured page text.

    Whitespace normalisation is the single allowed transformation.
    """
    needle = normalize_whitespace(quote)
    return bool(needle) and needle in normalize_whitespace(canonical_text)
