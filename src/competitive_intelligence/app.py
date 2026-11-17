"""Flask aplikace: jedna stránka a jeden koncový bod, který spustí ohraničený audit.

Ochrana veřejného dema bez registrací: sdílený přístupový kód (na veřejném nasazení povinný pro
živý audit), malý limit velikosti požadavku, nejvýše několik souběžných auditů a omezení počtu
živých auditů z jedné adresy. Limity v paměti platí pro jednu instanci serveru; na serverless
hostingu je potřeba doplnit ochranu na úrovni hostingu (viz README, část Nasazení).
"""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
from collections import deque
from typing import Literal

from flask import Flask, jsonify, render_template, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import agents, workflow

MAX_BODY_BYTES = 8 * 1024
MAX_CONCURRENT_AUDITS = int(os.environ.get("MAX_CONCURRENT_AUDITS", "2"))
LIVE_AUDITS_PER_WINDOW = int(os.environ.get("LIVE_AUDITS_PER_WINDOW", "6"))
RATE_WINDOW_S = 600


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_ids: list[str] = Field(min_length=1, max_length=30)
    mode: Literal["live", "snapshot"] = "live"
    perspective: str | None = Field(default=None, max_length=40)
    code: str | None = Field(default=None, max_length=200)


class RateLimiter:
    """Klouzavé okno v paměti jedné instance (pro serverless nejde o globální kvótu)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window_s = limit, window_s
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            hits = self.hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def is_public_deployment() -> bool:
    return bool(os.environ.get("VERCEL")) or os.environ.get("PUBLIC_DEMO") == "1"


def build_id() -> str | None:
    """Commit, ze kterého běží nasazení (nastaví se při nasazení), aby šlo ověřit, co běží."""
    value = os.environ.get("APP_BUILD") or os.environ.get("VERCEL_GIT_COMMIT_SHA")
    return value[:12] if value else None


def _code_matches(given: str | None, expected: str) -> bool:
    return hmac.compare_digest((given or "").encode("utf-8"), expected.encode("utf-8"))


def create_app() -> Flask:
    try:  # lokální vývoj: načti .env, pokud existuje
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    slots = threading.BoundedSemaphore(MAX_CONCURRENT_AUDITS)
    limiter = RateLimiter(LIVE_AUDITS_PER_WINDOW, RATE_WINDOW_S)

    @app.errorhandler(413)
    def too_large(_exc):
        return jsonify(error="Požadavek je příliš velký."), 413

    @app.get("/")
    def index():
        config = workflow.load_config()
        return render_template(
            "index.html",
            suppliers=config["suppliers"],
            profile=config["profile"],
            llm=agents.llm_available(),
            needs_code=bool(os.environ.get("DEMO_CODE")),
            build=build_id(),
        )

    @app.get("/api/v1/version")
    def version():
        return jsonify(build=build_id())

    @app.post("/api/v1/analyze")
    def analyze():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="Tělo požadavku musí být JSON objekt."), 400
        try:
            req = AnalyzeRequest.model_validate(body)
        except ValidationError as exc:
            fields = sorted({".".join(str(p) for p in err["loc"]) or "tělo" for err in exc.errors()})
            return jsonify(error="Neplatný požadavek: " + ", ".join(fields) + "."), 400
        known = {s["id"] for s in workflow.load_config()["suppliers"]}
        if not all(i in known for i in req.supplier_ids):
            return jsonify(error="supplier_ids musí obsahovat jen známé identifikátory dodavatelů."), 400
        if req.perspective is not None and req.perspective not in known:
            return jsonify(error="perspective musí být identifikátor známého dodavatele."), 400

        demo_code = os.environ.get("DEMO_CODE")
        live = req.mode == "live"
        if live and is_public_deployment() and not demo_code:
            return jsonify(error="Na veřejném nasazení musí být nastaven DEMO_CODE; živý audit je vypnutý."), 403
        if demo_code and not _code_matches(req.code, demo_code):
            return jsonify(error="Neplatný přístupový kód."), 403
        if live and not limiter.allow(request.remote_addr or "neznámá adresa"):
            return jsonify(error="Příliš mnoho živých auditů za krátkou dobu. Zkuste to později "
                                 "nebo použijte uložená data."), 429
        if not slots.acquire(blocking=False):
            return jsonify(error="Právě běží jiné audity. Zkuste to za chvíli."), 429
        try:
            report = workflow.run(req.supplier_ids, live=live, perspective=req.perspective)
        except workflow.AuditError as exc:
            return jsonify(error="Došlo k chybě při provádění auditu."), 502
        finally:
            slots.release()
        report["build"] = build_id()
        return jsonify(report)

    @app.get("/api/v1/example")
    def example():
        if not workflow.EXAMPLE_REPORT.exists():
            return jsonify(error="Uložená ukázka není k dispozici."), 404
        return jsonify(json.loads(workflow.EXAMPLE_REPORT.read_text(encoding="utf-8")))

    return app


if __name__ == "__main__":
    create_app().run()
