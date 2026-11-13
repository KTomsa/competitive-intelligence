"""Flask application factory."""

from dataclasses import dataclass
from typing import Any

from flask import Flask, request
from werkzeug.exceptions import HTTPException

from .config import Settings
from .schemas import AuditCreateRequest, AuditMode
from .services import AuditRepository, FixtureBundle, InMemoryAuditRepository
from .services.orchestrator import PipelineContext
from .web import api, error_response, pages
from .web.security import RateLimiter
from .worker import build_context, process_next


@dataclass
class Services:
    settings: Settings
    repo: AuditRepository
    ctx: PipelineContext
    limiter: RateLimiter

    @property
    def bundle(self) -> FixtureBundle:
        return self.ctx.bundle


def _seed_demo_audit(repo: AuditRepository, ctx: PipelineContext) -> None:
    bundle = ctx.bundle
    audit_id = repo.create_audit(
        AuditCreateRequest(
            supplier_ids=bundle.supplier_ids,
            profile_ids=list(bundle.profiles),
            mode=AuditMode.FIXTURE,
        )
    )
    process_next(repo, ctx)
    if repo.get_result(audit_id) is not None:
        repo.publish(audit_id)


def create_app(
    config: dict[str, Any] | None = None, context: PipelineContext | None = None
) -> Flask:
    """Build the app. `config` overrides Settings fields and `context` replaces the
    pipeline collaborators (both used by tests).

    Nothing here opens a database connection or reads a secret at import time.
    """
    app = Flask(__name__)
    settings = Settings.from_env().with_overrides(config)
    ctx = context or build_context()
    repo: AuditRepository = InMemoryAuditRepository()
    if settings.seed_demo_audit:
        _seed_demo_audit(repo, ctx)
    app.extensions["fact_or_watt"] = Services(
        settings=settings,
        repo=repo,
        ctx=ctx,
        limiter=RateLimiter(settings.rate_limit_per_minute),
    )

    app.register_blueprint(pages)
    app.register_blueprint(api)

    @app.errorhandler(HTTPException)
    def _http_error(error: HTTPException):
        if request.path.startswith("/api/"):
            return error_response(error.code or 500, (error.name or "ERROR").upper().replace(" ", "_"), error.description or "")
        return error

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        return response

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
