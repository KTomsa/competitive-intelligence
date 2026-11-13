"""Flask aplikace: jedna stránka a jeden koncový bod, který spustí ohraničený audit."""
from __future__ import annotations

import hmac
import json
import os

from flask import Flask, jsonify, render_template, request

from . import agents, workflow


def create_app() -> Flask:
    try:  # lokální vývoj: načti .env, pokud existuje
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    app = Flask(__name__)
    app.json.ensure_ascii = False

    @app.get("/")
    def index():
        config = workflow.load_config()
        return render_template(
            "index.html",
            suppliers=config["suppliers"],
            profile=config["profile"],
            llm=agents.llm_available(),
            needs_code=bool(os.environ.get("DEMO_CODE")),
        )

    @app.post("/api/v1/analyze")
    def analyze():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="Tělo požadavku musí být JSON objekt."), 400
        demo_code = os.environ.get("DEMO_CODE")
        if demo_code and not hmac.compare_digest(str(body.get("code", "")), demo_code):
            return jsonify(error="Neplatný přístupový kód."), 403
        known = {s["id"] for s in workflow.load_config()["suppliers"]}
        supplier_ids = body.get("supplier_ids")
        if (
            not isinstance(supplier_ids, list)
            or not supplier_ids
            or not all(isinstance(i, str) and i in known for i in supplier_ids)
        ):
            return jsonify(error="supplier_ids musí být neprázdný seznam známých identifikátorů."), 400
        live = body.get("mode", "live") != "snapshot"
        try:
            report = workflow.run(supplier_ids, live=live)
        except workflow.AuditError as exc:
            return jsonify(error=str(exc)), 502
        return jsonify(report)

    @app.get("/api/v1/example")
    def example():
        if not workflow.EXAMPLE_REPORT.exists():
            return jsonify(error="Uložená ukázka není k dispozici."), 404
        return jsonify(json.loads(workflow.EXAMPLE_REPORT.read_text(encoding="utf-8")))

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
