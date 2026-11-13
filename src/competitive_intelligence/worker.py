"""Audit job processing.

`process_next` claims one queued job and runs it. In Phase 1 the web app
calls it inline for fixture audits, because the in-memory repository cannot
be shared with another process. Once the Azure-backed repository exists, the
same function runs in a separately deployed worker loop and the inline call
is removed.
"""

import json
import logging
import sys

from .schemas import AuditCreateRequest, AuditMode
from .services import AuditRepository, FixtureBundle, InMemoryAuditRepository, load_demo_fixture
from .services.orchestrator import run_fixture_audit

log = logging.getLogger(__name__)


def process_next(repo: AuditRepository, bundle: FixtureBundle) -> str | None:
    """Claim and process one job. Returns its audit ID, or None if the queue is empty."""
    audit_id = repo.claim_next_job()
    if audit_id is None:
        return None
    try:
        if repo.get_request(audit_id).mode is not AuditMode.FIXTURE:
            repo.fail(audit_id, "LIVE_NOT_IMPLEMENTED", "Živý audit zatím není implementován.")
            return audit_id
        run_fixture_audit(repo, audit_id, bundle)
    except Exception:
        # A failed stage is an operational failure, never a verdict.
        log.exception("Audit %s failed", audit_id)
        repo.fail(audit_id, "PIPELINE_ERROR", "Audit selhal při zpracování; výsledky nejsou k dispozici.")
    return audit_id


def main() -> None:
    """Offline smoke run: one fixture audit through the whole pipeline, no network, no keys."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Czech text on a non-UTF-8 console
    bundle = load_demo_fixture()
    repo = InMemoryAuditRepository()
    audit_id = repo.create_audit(
        AuditCreateRequest(
            supplier_ids=bundle.supplier_ids,
            profile_ids=list(bundle.profiles),
            mode=AuditMode.FIXTURE,
        )
    )
    process_next(repo, bundle)
    status = repo.get_status(audit_id)
    result = repo.get_result(audit_id)
    print(
        json.dumps(
            {
                "audit_id": audit_id,
                "status": status.status,
                "events": len(status.events),
                "warnings": status.warnings,
                "verdicts": {v.claim_id: v.verdict for v in result.verdicts} if result else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
