"""JSON API (docs/API_CONTRACTS.md). Handlers stay short; the pipeline runs in the worker."""

from typing import Any

from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, ValidationError

from ..schemas import AuditCreated, AuditCreateRequest, AuditMode, AuditStatus
from ..services import AuditNotFound
from ..worker import process_next
from .security import access_code_matches

api = Blueprint("api", __name__, url_prefix="/api/v1")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def error_response(status: int, code: str, message: str, details: dict[str, Any] | None = None):
    return jsonify({"error": {"code": code, "message": message, "details": details or {}}}), status


@api.errorhandler(ApiError)
def _handle_api_error(error: ApiError):
    return error_response(error.status, error.code, error.message, error.details)


@api.errorhandler(AuditNotFound)
def _handle_not_found(_error: AuditNotFound):
    return error_response(404, "AUDIT_NOT_FOUND", "Audit s tímto ID neexistuje.")


def _json(model: BaseModel, status: int = 200):
    return current_app.response_class(
        model.model_dump_json(), status=status, mimetype="application/json"
    )


def _services():
    return current_app.extensions["fact_or_watt"]


@api.post("/audits")
def create_audit():
    services = _services()
    if not services.limiter.allow(request.remote_addr or "unknown"):
        raise ApiError(429, "RATE_LIMITED", "Příliš mnoho pokusů, zkuste to za chvíli.")
    expected = services.settings.audit_access_code
    if not expected:
        # Fail closed: without a configured code nobody can launch an audit.
        raise ApiError(503, "AUDIT_LAUNCH_DISABLED", "Spouštění auditů není na serveru nastaveno.")
    if not access_code_matches(request.headers.get("X-Audit-Access-Code"), expected):
        raise ApiError(401, "UNAUTHORIZED", "Chybí nebo nesouhlasí přístupový kód.")

    try:
        payload = AuditCreateRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        fields = [{"field": ".".join(map(str, e["loc"])), "problem": e["msg"]} for e in exc.errors()]
        raise ApiError(422, "VALIDATION_ERROR", "Neplatný požadavek.", {"fields": fields}) from None

    bundle = services.bundle
    unknown_suppliers = sorted(set(payload.supplier_ids) - set(bundle.supplier_ids))
    unknown_profiles = sorted(set(payload.profile_ids) - set(bundle.profiles))
    if unknown_suppliers or unknown_profiles:
        raise ApiError(
            422,
            "VALIDATION_ERROR",
            "Neznámý dodavatel nebo profil.",
            {"supplier_ids": unknown_suppliers, "profile_ids": unknown_profiles},
        )
    if payload.mode is AuditMode.LIVE:
        raise ApiError(
            501, "LIVE_NOT_IMPLEMENTED", "Živý audit zatím není implementován; použijte mode=fixture."
        )

    audit_id = services.repo.create_audit(payload)
    if not getattr(services.repo, "durable", False):
        # Phase 1 only: the in-memory store cannot be shared with a separate
        # worker process, and an offline fixture run takes milliseconds.
        process_next(services.repo, bundle)
    created = AuditCreated(
        audit_id=audit_id,
        status=AuditStatus.QUEUED,
        status_url=f"/api/v1/audits/{audit_id}",
        results_url=f"/api/v1/audits/{audit_id}/results",
    )
    return _json(created, 202)


@api.get("/audits/latest")
def latest_audit():
    result = _services().repo.get_latest_published()
    if result is None:
        raise ApiError(404, "NOT_PUBLISHED", "Zatím nebyl publikován žádný audit.")
    return _json(result)


@api.get("/audits/<audit_id>")
def audit_status(audit_id: str):
    return _json(_services().repo.get_status(audit_id))


@api.get("/audits/<audit_id>/results")
def audit_results(audit_id: str):
    repo = _services().repo
    status = repo.get_status(audit_id)
    result = repo.get_result(audit_id)
    if result is None:
        if status.status is AuditStatus.FAILED:
            raise ApiError(409, "AUDIT_FAILED", "Audit selhal, výsledky nejsou k dispozici.")
        raise ApiError(409, "AUDIT_NOT_READY", "Audit ještě není dokončen.")
    return _json(result)
