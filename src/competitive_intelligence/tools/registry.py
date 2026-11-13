"""Closed Pydantic-validated tools; no URL fetching, SQL, shell, or eval entry point."""

from datetime import date
from typing import Literal

from pydantic import Field

from ..llm.runtime import AgentStep, digest
from ..schemas import Claim, StrictModel
from .reference import ReferenceStore, calculate_annual_cost


class OffersArgs(StrictModel):
    supplier_ids: list[str] = Field(min_length=1, max_length=5)
    profile_id: str
    as_of_date: date


class CostArgs(StrictModel):
    offer_id: str
    profile_id: str
    component: Literal["annual_trade_cost"]


class ProductArgs(StrictModel):
    supplier_id: str
    product_id: str | None
    as_of_date: date


def strict_schema(schema):
    """Strict Responses schemas require every object key, including nullable keys."""
    if isinstance(schema, list):
        return [strict_schema(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    value = {key: strict_schema(item) for key, item in schema.items() if key != "default"}
    if value.get("type") == "object":
        value["additionalProperties"] = False
        value["required"] = list(value.get("properties", {}))
    return value


class ToolRegistry:
    def __init__(self, claim: Claim, store: ReferenceStore, guarantees, supplier_ids, profile_ids, record):
        self.claim, self.store, self.guarantees = claim, store, guarantees
        self.suppliers, self.profiles, self.record = set(supplier_ids), set(profile_ids), record
        self.role = "price_agent" if claim.claim_type == "price" else "green_agent"
        self.schemas = ({"get_price_offers": OffersArgs, "calculate_annual_cost": CostArgs,
                         "get_product_terms": ProductArgs} if self.role == "price_agent" else
                        {"get_fuel_mix": ProductArgs, "get_guarantees_of_origin": ProductArgs})
        self.available_offers = {}
        self.selected_offers, self.selected_terms, self.selected_mix, self.selected_guarantees = {}, {}, {}, {}
        self.remaining = 4

    def definitions(self):
        return [{"type": "function", "name": name, "strict": True,
                 "description": f"Read approved evidence using {name}; returns unavailable when no evidence exists.",
                 "parameters": strict_schema(model.model_json_schema())} for name, model in self.schemas.items()]

    def execute(self, call):
        step = AgentStep(role=self.role, kind="tool", name=call.name if call.name in self.schemas else "unknown_tool",
                         status="started", input_hash=digest(call.arguments))
        self.record(step)  # Lease fence before executing an approved function.
        if self.remaining <= 0:
            self.record(step.model_copy(update={"status": "rejected"}))
            return {"error": "TOOL_BUDGET_EXHAUSTED"}
        self.remaining -= 1
        try:
            if call.name not in self.schemas:
                raise ValueError("Unapproved tool")
            args = self.schemas[call.name].model_validate_json(call.arguments)
            if hasattr(args, "profile_id") and args.profile_id not in self.profiles:
                raise ValueError("Profile outside audit scope")
            if hasattr(args, "as_of_date") and args.as_of_date != self.claim.captured_at.date():
                raise ValueError("Wrong evidence date")
            if isinstance(args, OffersArgs) and not set(args.supplier_ids) <= self.suppliers:
                raise ValueError("Supplier outside audit scope")
            if isinstance(args, ProductArgs) and (
                args.supplier_id != self.claim.supplier_id or args.product_id != self.claim.product_id
            ):
                raise ValueError("Product outside claim scope")
            result = self._execute(call.name, args)
        except (ValueError, KeyError):
            self.record(step.model_copy(update={"status": "rejected"}))
            return {"error": "TOOL_INPUT_REJECTED"}
        self.record(step.model_copy(update={"status": "completed", "output_hash": digest(result)}))
        return result

    def _execute(self, name, args):
        if name == "get_price_offers":
            rows = [row for row in self.store.get_price_offers(args.supplier_ids)
                    if row.supplier_id != self.claim.supplier_id or (
                        self.claim.product_id is not None and row.product_id == self.claim.product_id)][:50]
            self.available_offers.update({row.offer_id: row for row in rows})
        elif name == "calculate_annual_cost":
            offer = self.available_offers[args.offer_id]
            # Multiple products for one supplier require a separate comparison scope.
            prior = self.selected_offers.get(offer.supplier_id)
            if prior is not None and prior.offer_id != offer.offer_id:
                raise ValueError("Ambiguous comparable product")
            self.selected_offers[offer.supplier_id] = offer
            return {"offer_id": offer.offer_id, "profile_id": args.profile_id, "component": args.component,
                    "value": str(calculate_annual_cost(offer, self.store.profiles[args.profile_id])),
                    "breakdown": {"monthly_supplier_fee": str(offer.monthly_fee),
                                  "annual_consumption_mwh": str(self.store.profiles[args.profile_id]),
                                  "supplier_energy_price_mwh": str(offer.price_mwh)},
                    "unit": "CZK/year", "source_content_hash": offer.source_content_hash}
        elif name == "get_product_terms":
            row = self.store.get_product_terms(args.supplier_id, args.product_id)
            rows = [row] if row is not None else []
            self.selected_terms.update({r.product_id: r for r in rows})
        elif name == "get_fuel_mix":
            row = self.store.get_fuel_mix(args.supplier_id, args.product_id)
            rows = [row] if row is not None else []
            self.selected_mix.update({(r.supplier_id, r.product_id): r for r in rows})
        else:
            rows = [r for r in self.guarantees if r.supplier_id == args.supplier_id
                    and args.product_id is not None and r.product_id == args.product_id][:50]
            self.selected_guarantees.update({(r.supplier_id, r.product_id): r for r in rows})
        return {"records": [r.model_dump(mode="json") for r in rows], "unavailable": not rows}

    def selected_store(self):
        mixes = []
        for row in self.selected_mix.values():
            certificate = self.selected_guarantees.get((row.supplier_id, row.product_id))
            documented = (certificate is not None and certificate.valid_from == row.valid_from
                          and certificate.valid_to == row.valid_to)
            mixes.append(row.model_copy(update={"guarantees_documented": documented}))
        return ReferenceStore.from_records(self.store.profiles, offers=self.selected_offers.values(),
                                           terms=self.selected_terms.values(), fuel_mix=mixes)
