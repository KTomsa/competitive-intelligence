"""Audit job processing.

`process_next` claims one queued job and runs it. For now the web app calls
it inline, because the in-memory repository cannot be shared with another
process. Once a durable repository exists, the same function runs in a
separately deployed worker loop and the inline call is removed.
"""

import json
import logging
import sys
from pathlib import Path

from .connectors import SupplierWebClient
from .registry import load_registry
from .schemas import AuditCreateRequest, AuditMode
from .services import AuditRepository, InMemoryAuditRepository, load_demo_fixture
from .services.orchestrator import PipelineContext, run_fixture_audit, run_live_audit
from .services.stores import FileBaselineStore, FileSnapshotStore
from .services.web_scout import WebScout

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"


def build_context(data_dir: Path = DATA_DIR) -> PipelineContext:
    """Wire the real collaborators. Opens no connection until a live audit runs."""
    registry = load_registry()
    scout = WebScout(
        SupplierWebClient(registry.allowed_urls),
        FileSnapshotStore(data_dir / "snapshots"),
        FileBaselineStore(data_dir / "baseline"),
    )
    return PipelineContext(bundle=load_demo_fixture(), registry=registry, scout=scout)


def process_next(repo: AuditRepository, ctx: PipelineContext) -> str | None:
    """Claim and process one job. Returns its audit ID, or None if the queue is empty."""
    audit_id = repo.claim_next_job()
    if audit_id is None:
        return None
    try:
        if repo.get_request(audit_id).mode is AuditMode.LIVE:
            run_live_audit(repo, audit_id, ctx)
        else:
            run_fixture_audit(repo, audit_id, ctx.bundle)
    except Exception:
        # A failed stage is an operational failure, never a verdict.
        log.exception("Audit %s failed", audit_id)
        repo.fail(audit_id, "PIPELINE_ERROR", "Audit selhal při zpracování; výsledky nejsou k dispozici.")
    return audit_id


def main() -> None:
    """Run one audit from the command line and print a summary.

    Default is the offline fixture audit. `--live` fetches the registered
    supplier pages and refreshes their snapshots.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Czech text on a non-UTF-8 console
    live = "--live" in sys.argv[1:]
    ctx = build_context()
    repo = InMemoryAuditRepository()
    audit_id = repo.create_audit(
        AuditCreateRequest(
            supplier_ids=ctx.registry.supplier_ids if live else ctx.bundle.supplier_ids,
            profile_ids=list(ctx.bundle.profiles),
            mode=AuditMode.LIVE if live else AuditMode.FIXTURE,
        )
    )
    process_next(repo, ctx)
    status = repo.get_status(audit_id)
    result = repo.get_result(audit_id)
    print(
        json.dumps(
            {
                "audit_id": audit_id,
                "status": status.status,
                "events": [f"{e.stage}: {e.message}" for e in status.events],
                "sources": {s.source_id: s.origin for s in result.sources} if result else None,
                "verdicts": {v.claim_id: v.verdict for v in result.verdicts} if result else None,
                "error": status.error.message if status.error else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
