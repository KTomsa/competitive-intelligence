"""Read-only, manually reviewed reference imports with explicit scope and artifact hashes."""

import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, model_validator

from ..schemas import StrictModel
from ..tools.reference import FuelMix, PriceOffer, ProductTerms, ReferenceRecord, ReferenceStore


class GuaranteesRecord(ReferenceRecord):
    supplier_id: str
    product_id: str


class ReviewedReferenceBundle(StrictModel):
    version: int = Field(ge=1, le=1)
    reviewed_by: str = Field(min_length=1, max_length=150)
    reviewed_at: AwareDatetime
    # All price offers share the same D02d distribution region and include VAT.
    distribution_region: str = Field(min_length=1, max_length=100)
    tariff: str = Field(pattern=r"^D02d$")
    vat_included: bool
    offers: list[PriceOffer] = Field(default_factory=list, max_length=500)
    terms: list[ProductTerms] = Field(default_factory=list, max_length=500)
    fuel_mix: list[FuelMix] = Field(default_factory=list, max_length=500)
    guarantees: list[GuaranteesRecord] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_provenance(self):
        if not self.reviewed_by.strip() or not self.vat_included:
            raise ValueError("Reviewed reference prices must include VAT")
        for row in [*self.offers, *self.terms, *self.fuel_mix, *self.guarantees]:
            url = urlsplit(row.source_url)
            if (not row.source_url.startswith("https://") or not row.source_ref.strip()
                    or not url.hostname or url.username is not None or url.password is not None
                    or re.fullmatch(r"[0-9a-f]{64}", row.source_content_hash) is None
                    or row.valid_from is None or row.valid_to is None
                    or row.valid_to < row.valid_from or row.valid_as_of < row.valid_from
                    or row.valid_as_of > row.valid_to or row.captured_at > self.reviewed_at):
                raise ValueError("Reference provenance/validity is incomplete")
        if len({row.offer_id for row in self.offers}) != len(self.offers):
            raise ValueError("Offer IDs must be unique")
        if any(row.price_mwh < 0 or row.monthly_fee < 0 for row in self.offers):
            raise ValueError("Negative price components are not supported")
        if any(not 0 <= row.renewable_share <= 100 for row in self.fuel_mix):
            raise ValueError("Renewable share must be a percentage")
        return self

    def at(self, as_of: date, profiles) -> tuple[ReferenceStore, list[GuaranteesRecord]]:
        def valid(row):
            return (row.valid_from <= as_of <= row.valid_to
                    and row.captured_at.date() <= as_of and self.reviewed_at.date() <= as_of)

        # A Boolean in a mix row alone never proves product-linked guarantees.
        mixes = [row.model_copy(update={"guarantees_documented": False}) for row in self.fuel_mix if valid(row)]
        return ReferenceStore.from_records(profiles, offers=[r for r in self.offers if valid(r)],
            terms=[r for r in self.terms if valid(r)], fuel_mix=mixes), [r for r in self.guarantees if valid(r)]


def load_reference_bundle(path: str) -> ReviewedReferenceBundle:
    try:
        file = Path(path)
        if file.stat().st_size > 2_000_000:
            raise ValueError("Bundle too large")
        return ReviewedReferenceBundle.model_validate_json(file.read_text(encoding="utf-8"))
    except Exception:
        raise ValueError("Reviewed reference bundle is missing or invalid") from None
