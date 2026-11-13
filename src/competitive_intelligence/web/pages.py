"""Server-rendered pages. Data is loaded by the browser from the JSON API."""

from flask import Blueprint, current_app, render_template

pages = Blueprint("pages", __name__)


def _form_context() -> dict:
    services = current_app.extensions["fact_or_watt"]
    bundle = services.bundle
    return {
        "suppliers": bundle.suppliers,
        "profiles": [
            {"profile_id": pid, "label": bundle.profile_labels[pid]} for pid in bundle.profiles
        ],
        "launch_enabled": bool(services.settings.audit_access_code),
    }


@pages.get("/")
def index():
    return render_template("index.html", **_form_context())


@pages.get("/audits/<audit_id>")
def audit(audit_id: str):
    # The ID is only echoed into a data attribute (autoescaped); the API decides if it exists.
    return render_template("audit.html", audit_id=audit_id)
