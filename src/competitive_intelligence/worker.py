"""Separately started durable worker and explicit operator commands."""

import argparse
import json
import logging
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .connectors import SupplierWebClient
from .db import check_schema
from .registry import load_registry
from .llm.provider import OpenAIResponsesProvider
from .llm.gemini import GeminiGenerateContentProvider
from .schemas import AuditCreateRequest, AuditMode
from .services import AuditRepository, FixtureBundle, InMemoryAuditRepository, load_demo_fixture
from .services.factory import build_repository
from .services.orchestrator import PipelineContext, run_fixture_audit, run_live_audit
from .services.repository import LeaseLost
from .services.reference_bundle import load_reference_bundle
from .services.stores import FileBaselineStore, FileSnapshotStore
from .services.web_scout import WebScout

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"


def build_context(data_dir: Path = DATA_DIR, *, settings: Settings | None = None) -> PipelineContext:
    """Wire source intake without making network requests."""
    registry = load_registry()
    scout = WebScout(
        SupplierWebClient(registry.allowed_urls),
        FileSnapshotStore(data_dir / "snapshots"),
        FileBaselineStore(data_dir / "baseline"),
    )
    ctx = PipelineContext(bundle=load_demo_fixture(), registry=registry, scout=scout)
    if settings is not None:
        if settings.llm_provider in ("openai", "gemini"):
            assert settings.llm_api_key is not None and settings.llm_model is not None
            provider = GeminiGenerateContentProvider if settings.llm_provider == "gemini" else OpenAIResponsesProvider
            ctx.model_provider = provider(settings.llm_api_key, settings.llm_model,
                                          timeout=settings.llm_timeout_seconds)
        if settings.reference_bundle_path:
            ctx.references = load_reference_bundle(settings.reference_bundle_path)
        ctx.llm_max_requests = settings.llm_max_requests
        ctx.llm_max_input_chars = settings.llm_max_input_chars
        ctx.llm_max_output_tokens = settings.llm_max_output_tokens
        ctx.llm_max_claims = settings.llm_max_claims
    return ctx


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


def process_next(repo: AuditRepository, context: PipelineContext | FixtureBundle) -> str | None:
    """Claim and process one job. Returns its audit ID, or None if the queue is empty."""
    audit_id = repo.claim_next_job()
    if audit_id is None:
        return None
    try:
        with keep_lease(repo, audit_id):
            if repo.get_request(audit_id).mode is AuditMode.LIVE:
                ctx = context if isinstance(context, PipelineContext) else build_context()
                storage = repo.source_storage(audit_id, ctx.registry)
                if storage is not None:
                    ctx = replace(ctx, scout=ctx.scout.with_storage(storage))
                run_live_audit(repo, audit_id, ctx)
            else:
                bundle = context.bundle if isinstance(context, PipelineContext) else context
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
    """Offline smoke run through the whole pipeline, no network, no keys."""
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
                "events": [f"{e.stage}: {e.message}" for e in status.events],
                "sources": {s.source_id: s.origin for s in result.sources} if result else None,
                "verdicts": {v.claim_id: v.verdict for v in result.verdicts} if result else None,
                "error": status.error.message if status.error else None,
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
    commands.add_parser("check-llm-config", help="Show non-secret model configuration without contacting a provider")
    publish = commands.add_parser("publish", help="Approve and publish a completed audit")
    publish.add_argument("audit_id")
    publish.add_argument("--approved-by", required=True)
    commands.add_parser("fixture", help="Run one offline in-memory fixture audit")
    args = parser.parse_args(argv)
    if args.command == "fixture":
        fixture_main()
        return 0
    if args.command == "check-llm-config":
        try:
            settings = Settings.from_env()
            print(json.dumps({"enabled": settings.llm_provider is not None,
                "provider": settings.llm_provider, "model": settings.llm_model,
                "key_configured": bool(settings.llm_api_key), "max_requests": settings.llm_max_requests,
                "max_output_tokens_per_request": settings.llm_max_output_tokens,
                "max_input_chars": settings.llm_max_input_chars, "max_claims": settings.llm_max_claims}))
            return 0
        except ValueError:
            print("Invalid LLM configuration; check provider, model and limits.", file=sys.stderr)
            return 1
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
            print("Azure SQL connectivity and audit/source schema: OK")
            return 0
        if args.command == "publish":
            result = repo.publish(args.audit_id, approved_by=args.approved_by)
            print(json.dumps({"audit_id": result.audit_id, "published_at": result.published_at.isoformat()}))
            return 0
        ctx = build_context(settings=settings)
        while True:
            audit_id = process_next(repo, ctx)
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
