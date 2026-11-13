"""Read-only reference tools (docs/API_CONTRACTS.md, section 4).

These are the only functions evidence agents may call. They read curated,
dated reference records and do arithmetic in `Decimal`. They never fetch a
URL, run SQL or accept free-form input.
"""

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime

from ..schemas import StrictModel


class ReferenceRecord(StrictModel):
    """Provenance every reference record must carry to be usable as evidence."""

    source_url: str
    source_ref: str
    captured_at: AwareDatetime
    valid_as_of: date
    source_content_hash: str = ""
    valid_from: date | None = None
    valid_to: date | None = None


class PriceOffer(ReferenceRecord):
    offer_id: str
    supplier_id: str
    product_id: str
    monthly_fee: Decimal
    price_mwh: Decimal
    comparable: bool = True


class ProductTerms(ReferenceRecord):
    supplier_id: str
    product_id: str
    fixed_until: date | None = None
    exclusions_unclear: bool = False


class FuelMix(ReferenceRecord):
    supplier_id: str
    product_id: str | None = None
    renewable_share: Decimal
    product_specific: bool
    guarantees_documented: bool = False


class OpmCount(ReferenceRecord):
    supplier_id: str
    period: str
    opm: int


def _content_hash(raw: dict[str, Any]) -> str:
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load[T: ReferenceRecord](model: type[T], rows: list[dict[str, Any]]) -> list[T]:
    return [model.model_validate({**row, "source_content_hash": _content_hash(row)}) for row in rows]


def calculate_annual_cost(offer: PriceOffer, annual_mwh: Decimal) -> Decimal:
    """Annual trade-component cost incl. VAT: 12 x monthly fee + consumption x price."""
    return (12 * offer.monthly_fee + annual_mwh * offer.price_mwh).quantize(Decimal("0.01"))


class ReferenceStore:
    def __init__(self, data: dict[str, Any], profiles: dict[str, Decimal]) -> None:
        self.profiles = profiles
        self._offers = _load(PriceOffer, data.get("offers", []))
        self._terms = _load(ProductTerms, data.get("terms", []))
        self._mix = _load(FuelMix, data.get("fuel_mix", []))
        self._opm = _load(OpmCount, data.get("ote_opm", []))

    @classmethod
    def from_records(cls, profiles, *, offers=(), terms=(), fuel_mix=(), opm=()):
        """Use validated records without replacing their original artifact hashes."""
        store = cls({}, dict(profiles))
        store._offers = list(offers)
        store._terms = list(terms)
        store._mix = list(fuel_mix)
        store._opm = list(opm)
        return store

    def get_price_offers(self, supplier_ids: list[str]) -> list[PriceOffer]:
        wanted = set(supplier_ids)
        return [o for o in self._offers if o.supplier_id in wanted and o.comparable]

    def get_product_terms(self, supplier_id: str, product_id: str | None) -> ProductTerms | None:
        for terms in self._terms:
            if terms.supplier_id == supplier_id and terms.product_id == product_id:
                return terms
        return None

    def get_fuel_mix(self, supplier_id: str, product_id: str | None) -> FuelMix | None:
        """Product-specific record if one exists, else the broader record as context only."""
        candidates = [m for m in self._mix if m.supplier_id == supplier_id]
        for mix in candidates:
            if product_id is not None and mix.product_id == product_id:
                return mix
        for mix in candidates:
            if mix.product_id is None:
                return mix
        return None

    def get_ote_opm(self, supplier_id: str) -> list[OpmCount]:
        return sorted((o for o in self._opm if o.supplier_id == supplier_id), key=lambda o: o.period)
