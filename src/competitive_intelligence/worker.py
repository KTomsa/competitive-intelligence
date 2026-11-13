"""Separately started durable worker and explicit operator commands."""

import argparse
import json
import logging
import sys
import threading
import time
from contextlib import contextmanager

from .config import Settings
from .db import check_schema
from .schemas import AuditCreateRequest, AuditMode
from .services import AuditRepository, FixtureBundle, InMemoryAuditRepository, load_demo_fixture
from .services.factory import build_repository
from .services.orchestrator import run_fixture_audit
from .services.repository import LeaseLost

log = logging.getLogger(__name__)


@contextmanager
def keep_lease(repo, audit_id):
    """Only the separate durable worker starts a heartbeat thread."""
    stop = threading.Event()

    def beat():
        while not stop.wait(max(1, repo.lease_seconds / 3)):
            try:
                repo.heartbeat(audit_id)
            except Exception:
                # The pipeline's next write still fences ownership in SQL.
                # A database outage is recovered through lease expiration.
                return

    thread = None
    if repo.durable:
        thread = threading.Thread(target=beat, name="audit-heartbeat", daemon=True)
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if thread is not None:
            thread.join(timeout=12)


def process_next(repo: AuditRepository, bundle: FixtureBundle) -> str | None:
    """Claim and process one job. Returns its audit ID, or None if the queue is empty."""
    audit_id = repo.claim_next_job()
    if audit_id is None:
        return None
    try:
        with keep_lease(repo, audit_id):
            if repo.get_request(audit_id).mode is not AuditMode.FIXTURE:
                repo.fail(audit_id, "LIVE_NOT_IMPLEMENTED", "Živý audit zatím není implementován.")
                return audit_id
            run_fixture_audit(repo, audit_id, bundle)
    except LeaseLost:
        log.warning("Audit %s lost its lease; delivery stopped", audit_id)
    except Exception as error:
        # A failed stage is an operational failure, never a verdict.
        # Exception text/tracebacks may contain driver credentials or prompts.
        log.warning("Audit %s interrupted (%s)", audit_id, type(error).__name__)
        try:
            repo.retry(audit_id, "PIPELINE_ERROR", "Audit selhal při zpracování; výsledky nejsou k dispozici.")
        except Exception as retry_error:
            log.warning("Audit %s recovery deferred (%s)", audit_id, type(retry_error).__name__)
    return audit_id


def fixture_main() -> None:
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fact or Watt durable audit worker")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Consume queued Azure SQL jobs")
    run.add_argument("--once", action="store_true", help="Process at most one available job")
    run.add_argument("--poll-seconds", type=float, default=2)
    commands.add_parser("check-db", help="Check connectivity and existing schema (read-only)")
    publish = commands.add_parser("publish", help="Approve and publish a completed audit")
    publish.add_argument("audit_id")
    publish.add_argument("--approved-by", required=True)
    commands.add_parser("fixture", help="Run one offline in-memory fixture audit")
    args = parser.parse_args(argv)
    if args.command == "fixture":
        fixture_main()
        return 0
    if args.command == "run" and args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    engine = None
    try:
        settings = Settings.from_env()
        if not settings.database_url:
            parser.error("DATABASE_URL is required for durable worker commands")
        repo = build_repository(settings)
        engine = repo.sessions.kw["bind"]
        check_schema(engine)
        if args.command == "check-db":
            with repo.sessions() as session:
                repo._now(session)  # exercise driver/UTC conversion with a read-only query
            print("Azure SQL connectivity and Phase 2 schema: OK")
            return 0
        if args.command == "publish":
            result = repo.publish(args.audit_id, approved_by=args.approved_by)
            print(json.dumps({"audit_id": result.audit_id, "published_at": result.published_at.isoformat()}))
            return 0
        bundle = load_demo_fixture()
        while True:
            audit_id = process_next(repo, bundle)
            if audit_id:
                print(json.dumps({"audit_id": audit_id, "status": repo.get_status(audit_id).status}), flush=True)
            if args.once:
                return 0
            if audit_id is None:
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"Worker command failed ({type(error).__name__}); check configuration, connectivity and schema.", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
