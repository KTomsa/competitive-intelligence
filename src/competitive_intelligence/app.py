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

from flask import Flask, request
from werkzeug.exceptions import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings
from .schemas import AuditCreateRequest, AuditMode
from .services import AuditRepository, FixtureBundle, load_demo_fixture
from .services.factory import build_repository
from .services.repository import StoredAuditError
from .web import api, error_response, pages
from .web.security import RateLimiter
from .worker import process_next


@dataclass
class Services:
    settings: Settings
    repo: AuditRepository
    bundle: FixtureBundle
    limiter: RateLimiter


def _seed_demo_audit(repo: AuditRepository, bundle: FixtureBundle) -> None:
    audit_id = repo.create_audit(
        AuditCreateRequest(
            supplier_ids=bundle.supplier_ids,
            profile_ids=list(bundle.profiles),
            mode=AuditMode.FIXTURE,
        )
    )
    process_next(repo, bundle)
    if repo.get_result(audit_id) is not None:
        repo.publish(audit_id)


def create_app(config: dict[str, Any] | None = None, *, repository: AuditRepository | None = None) -> Flask:
    """Build the app. `config` overrides Settings fields (used by tests).

    The factory reads settings, but opens no database connection at startup.
    """
    app = Flask(__name__)
    settings = Settings.from_env().with_overrides(config)
    bundle = load_demo_fixture()
    repo = repository if repository is not None else build_repository(settings)
    if settings.seed_demo_audit and not repo.durable:
        _seed_demo_audit(repo, bundle)
    app.extensions["fact_or_watt"] = Services(
        settings=settings,
        repo=repo,
        bundle=bundle,
        limiter=RateLimiter(settings.rate_limit_per_minute),
    )

    app.register_blueprint(pages)
    app.register_blueprint(api)

    @app.errorhandler(StoredAuditError)
    def _stored_audit_invalid(_error):
        return error_response(503, "STORED_AUDIT_INCOMPATIBLE", "Uložený audit nelze načíst. Kontaktujte správce.")

    @app.errorhandler(SQLAlchemyError)
    def _database_unavailable(_error):
        # Driver exception text can contain a server name, SQL or credentials.
        if request.path.startswith("/api/"):
            return error_response(503, "DATABASE_UNAVAILABLE", "Databáze není dostupná. Zkuste to znovu později.")
        return "Databáze není dostupná.", 503

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
