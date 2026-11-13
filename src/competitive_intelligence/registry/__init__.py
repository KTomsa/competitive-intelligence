"""Registry of approved suppliers and the exact pages the Web Scout may fetch."""

import json
from dataclasses import dataclass
from importlib import resources
from typing import Literal

from pydantic import Field, field_validator

from ..schemas import StrictModel, SupplierInfo
from ..schemas.claim import require_https


class RegisteredPage(StrictModel):
    source_id: str
    url: str
    page_kind: Literal["homepage", "product", "green", "pricing"]

    _https = field_validator("url")(require_https)


class RegisteredSupplier(StrictModel):
    supplier_id: str
    name: str
    pages: list[RegisteredPage] = Field(min_length=1, max_length=4)


@dataclass(frozen=True)
class SupplierRegistry:
    suppliers: list[RegisteredSupplier]

    @property
    def supplier_ids(self) -> list[str]:
        return [s.supplier_id for s in self.suppliers]

    @property
    def allowed_urls(self) -> set[str]:
        return {page.url for supplier in self.suppliers for page in supplier.pages}

    def get(self, supplier_id: str) -> RegisteredSupplier:
        return next(s for s in self.suppliers if s.supplier_id == supplier_id)

    def infos(self, supplier_ids: list[str]) -> list[SupplierInfo]:
        wanted = set(supplier_ids)
        return [
            SupplierInfo(supplier_id=s.supplier_id, name=s.name)
            for s in self.suppliers
            if s.supplier_id in wanted
        ]


def load_registry() -> SupplierRegistry:
    raw = json.loads(
        resources.files("competitive_intelligence.registry")
        .joinpath("suppliers.json")
        .read_text(encoding="utf-8")
    )
    return SupplierRegistry([RegisteredSupplier.model_validate(s) for s in raw["suppliers"]])
