import pytest

from competitive_intelligence.app import create_app

ACCESS_CODE = "test-only-access-code"


@pytest.fixture
def app():
    return create_app({"audit_access_code": ACCESS_CODE, "rate_limit_per_minute": 100})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth():
    return {"X-Audit-Access-Code": ACCESS_CODE}


@pytest.fixture
def payload():
    return {
        "supplier_ids": ["supplier_demo_a", "supplier_demo_b", "supplier_demo_c"],
        "profile_ids": ["d02d_2mwh", "d02d_4mwh"],
        "commodity": "electricity",
        "mode": "fixture",
    }
