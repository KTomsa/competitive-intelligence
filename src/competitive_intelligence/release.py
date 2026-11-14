"""Bounded, non-mutating HTTP smoke checks for a deployed Flask preview."""

import argparse
import json
import sys
from urllib.parse import urlsplit

import httpx

from .schemas import AuditResult, AuditStatusView
from .schemas.claim import require_https


def check_preview(base_url: str, *, transport=None, require_live: bool = True) -> dict:
    require_https(base_url)
    parts = urlsplit(base_url)
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("Preview URL must be an HTTPS origin")
    checks = []
    base_url = base_url.rstrip("/")
    with httpx.Client(timeout=15, follow_redirects=False, transport=transport) as client:
        def get(path):
            with client.stream("GET", base_url + path) as response:
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise ValueError("Preview response exceeds size bound")
                return response.status_code, response.headers, bytes(body)

        def record(name, passed):
            checks.append({"check": name, "passed": bool(passed)})

        status, headers, _ = get("/")
        record("homepage", status == 200)
        record("security_headers", all(name in headers for name in (
            "content-security-policy", "x-content-type-options", "x-request-id")))
        status, _, body = get("/health/live")
        record("liveness", status == 200 and json.loads(body).get("status") == "ok")
        status, _, body = get("/health/ready")
        ready = json.loads(body) if status == 200 else {}
        record("durable_readiness", status == 200 and ready.get("storage") == "durable")
        status, headers, body = get("/api/v1/audits/latest")
        record("latest_published", status == 200)
        if status == 200:
            result = AuditResult.model_validate_json(body)
            record("publication_timestamp", result.published_at is not None)
            record("latest_cache_policy", headers.get("cache-control") == "no-store")
            if require_live:
                record("live_completed_audit", result.mode == "live" and bool(result.claims))
                record("three_supplier_price_green_scope", len(result.suppliers) >= 3
                       and {"price", "green"} <= {claim.claim_type.value for claim in result.claims})
            # Follow only locally constructed routes; never a URL from a response.
            from urllib.parse import quote
            status, _, body = get("/api/v1/audits/" + quote(result.audit_id, safe=""))
            view = AuditStatusView.model_validate_json(body) if status == 200 else None
            record("persisted_status", view is not None and view.status == "completed")
        status, _, _ = get("/api/v1/audits/invalid-smoke-id/results")
        record("unknown_audit", status == 404)
        # No access code or request body: this cannot enqueue a job. Redirects
        # remain disabled so credentials would never follow another origin.
        with client.stream("POST", base_url + "/api/v1/audits") as response:
            record("launch_authentication", response.status_code == 401)
    return {"passed": all(item["passed"] for item in checks), "checks": checks,
            "limitations": ["Read-only smoke check; worker restart, TLS driver and human evaluation require separate acceptance.",
                             "No audit was launched, approved or published by this command."]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--allow-fixture", action="store_true", help="Demonstration check; never production acceptance")
    args = parser.parse_args(argv)
    try:
        report = check_preview(args.url, require_live=not args.allow_fixture)
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1
    except (httpx.HTTPError, ValueError):
        print("Preview check failed: network, response or URL validation error.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
