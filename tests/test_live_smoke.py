"""Volitelná kontrola živých úředních zdrojů. Běží jen s RUN_LIVE_SMOKE=1, mimo běžné CI.

    RUN_LIVE_SMOKE=1 uv run pytest tests/test_live_smoke.py -q
"""
import os

import pytest

from competitive_intelligence.connectors import eru, ote

pytestmark = pytest.mark.skipif(os.environ.get("RUN_LIVE_SMOKE") != "1", reason="živé zdroje jen na vyžádání")


def test_eru_bands_are_live():
    data = eru.load(live=True)
    assert data["source"]["origin"] == "live" and len(data["rows"]) > 100


@pytest.mark.parametrize("loader", [ote.load, ote.load_gas])
def test_ote_tables_are_live(loader):
    data = loader(live=True)
    assert data["source"]["origin"] == "live" and len(data["ranking"]) > 20
