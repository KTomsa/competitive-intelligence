"""Only exact reviewed source/quote bindings may attach products to new claims."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from competitive_intelligence.services.reference_bundle import ReviewedReferenceBundle

from .test_live_agents import NOW, claim, reference_data


def binding_data():
    return {"supplier_id": "cez", "source_url": "https://www.cez.cz/", "canonical_hash": "a" * 64,
            "quote": "Nejlevnější elektřina v ČR", "product_id": "product_cez",
            "valid_from": "2026-11-01", "valid_to": "2026-11-30"}


def source():
    from competitive_intelligence.schemas import Source

    return Source(source_id="cez_home", supplier_id="cez", source_url="https://www.cez.cz/", origin="live",
                  fetch_was_live=True, captured_at=NOW, checked_at=NOW, valid_as_of=NOW.date(),
                  content_sha256="b" * 64, canonical_hash="a" * 64, parser_version="html-v1")


def test_reviewed_binding_adds_product_with_traceable_review_without_relabeling_claim():
    data = reference_data()
    data["product_bindings"] = [binding_data()]
    bundle = ReviewedReferenceBundle.model_validate(data)
    bound = bundle.bind_claim(claim(product_id=None), source())
    assert bound.product_id == "product_cez" and bound.human_reviewed is False
    assert bound.product_binding_ref.startswith("reviewed-product-binding:")
    assert bound.product_binding_reviewed_at == bundle.reviewed_at
    assert "test-reviewer" not in bound.model_dump_json()


@pytest.mark.parametrize("mutation", ["hash", "quote", "url", "supplier", "expired", "future_review"])
def test_binding_does_not_transfer_across_sources_dates_or_quotes(mutation):
    data, page = reference_data(), source()
    data["product_bindings"] = [binding_data()]
    current = claim(product_id=None)
    if mutation == "hash":
        page.canonical_hash = "c" * 64
    elif mutation == "quote":
        current.quote = "Jiné tvrzení"
    elif mutation == "url":
        page.source_url = "https://www.cez.cz/changed"
    elif mutation == "supplier":
        current.supplier_id = "eon"
    elif mutation == "expired":
        data["product_bindings"][0]["valid_to"] = "2026-11-12"
    else:
        data["reviewed_at"] = NOW + timedelta(days=1)
    assert ReviewedReferenceBundle.model_validate(data).bind_claim(current, page).product_id is None


@pytest.mark.parametrize("mutation", ["duplicate", "unknown_product", "reversed_dates"])
def test_ambiguous_or_invalid_product_bindings_are_rejected(mutation):
    data = reference_data()
    data["product_bindings"] = [binding_data()]
    if mutation == "duplicate":
        data["product_bindings"].append(binding_data())
    elif mutation == "unknown_product":
        data["product_bindings"][0]["product_id"] = "invented"
    else:
        data["product_bindings"][0]["valid_to"] = "2020-01-01"
    with pytest.raises(ValidationError):
        ReviewedReferenceBundle.model_validate(data)


def test_binding_never_overwrites_approved_baseline_product():
    data = reference_data()
    data["product_bindings"] = [binding_data()]
    current = claim(product_id="baseline-product")
    assert ReviewedReferenceBundle.model_validate(data).bind_claim(current, source()).product_id == "baseline-product"
