"""Flask application factory."""

# VS Code's Run Python File executes this path without its package context.
# Keep relative imports working while retaining the canonical src package.
if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "competitive_intelligence"

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from flask import Flask, Response, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from .config import Settings
from .schemas import AuditCreateRequest, AuditMode
from .services import AuditRepository, FixtureBundle
from .services.factory import build_repository
from .services.orchestrator import PipelineContext
from .services.repository import StoredAuditError
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
    config: dict[str, Any] | None = None,
    *,
    repository: AuditRepository | None = None,
    context: PipelineContext | None = None,
) -> Flask:
    """Build the app. `config` overrides Settings fields (used by tests).

    The factory reads settings, but opens no database connection at startup.
    """
    app = Flask(__name__)
    settings = Settings.from_env().with_overrides(config)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_content_length
    app.config["TRUSTED_HOSTS"] = list(settings.trusted_hosts) or None
    ctx = context if context is not None else build_context(settings=settings)
    repo = repository if repository is not None else build_repository(settings)
    if settings.environment == "production" and not repo.durable:
        raise ValueError("Production requires a durable audit repository")
    if settings.seed_demo_audit and not repo.durable:
        _seed_demo_audit(repo, ctx)
    app.extensions["fact_or_watt"] = Services(
        settings=settings,
        repo=repo,
        ctx=ctx,
        limiter=RateLimiter(settings.rate_limit_per_minute),
    )

    app.register_blueprint(pages)
    app.register_blueprint(api)

    @app.before_request
    def _request_context() -> None:
        g.request_id = uuid4().hex

    @app.get("/health/live")
    def liveness():
        return jsonify({"status": "ok"})

    @app.get("/health/ready")
    def readiness():
        repo.check_ready()
        return jsonify({"status": "ready", "storage": "durable" if repo.durable else "demonstration"})

    @app.errorhandler(StoredAuditError)
    def _stored_audit_invalid(_error: StoredAuditError):
        return error_response(503, "STORED_AUDIT_INCOMPATIBLE", "Uložený audit nelze načíst. Kontaktujte správce.")

    @app.errorhandler(SQLAlchemyError)
    def _database_unavailable(_error: SQLAlchemyError):
        # Driver exception text can contain a server name, SQL or credentials.
        if request.path.startswith(("/api/", "/health/")):
            return error_response(503, "DATABASE_UNAVAILABLE", "Databáze není dostupná. Zkuste to znovu později.")
        return "Databáze není dostupná.", 503

    @app.errorhandler(HTTPException)
    def _http_error(error: HTTPException):
        if request.path.startswith("/api/"):
            return error_response(error.code or 500, (error.name or "ERROR").upper().replace(" ", "_"), error.description or "")
        return error

    @app.errorhandler(Exception)
    def _unexpected_error(error: Exception):
        # Do not log exception messages/tracebacks: driver/provider errors may
        # contain credentials, SQL, prompts or raw source content.
        app.logger.error("request_failed request_id=%s error_type=%s", getattr(g, "request_id", "unknown"), type(error).__name__)
        if request.path.startswith("/api/"):
            return error_response(500, "INTERNAL_ERROR", "Požadavek se nepodařilo dokončit.")
        return "Služba není dostupná.", 500

    @app.after_request
    def _security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers["X-Request-ID"] = getattr(g, "request_id", uuid4().hex)
        if request.path.startswith(("/api/", "/health/")) or response.mimetype == "text/html":
            response.headers["Cache-Control"] = "no-store"
        if response.status_code in (429, 503):
            response.headers.setdefault("Retry-After", "60")
        if settings.environment == "production" and request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        return response

    return app


if __name__ == "__main__":
    create_app().run(debug=False)
