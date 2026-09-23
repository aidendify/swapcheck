"""SwapCheck: live substitute-part decision log."""

from __future__ import annotations

import secrets
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

import helpers as H

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = H.max_upload_bytes()
app.secret_key = __import__("os").environ.get("SECRET_KEY", "swapcheck-self-hosted-change-me")

app.teardown_appcontext(H.close_db)


def init_db() -> None:
    with app.app_context():
        H.init_schema(H.get_db())
        H.upload_dir()


@app.context_processor
def inject_globals() -> dict:
    owner_ok = H.is_owner()
    tech_ok = H.is_tech()
    return {
        "marketing_url": H.marketing_url(),
        "show_marketing": bool(H.marketing_url()),
        "smtp_configured": H.smtp_configured(),
        "twilio_configured": H.twilio_configured(),
        "business_name": H.business_name(),
        "owner_locked": bool(H.owner_password()),
        "tech_locked": bool(H.tech_password()),
        "logged_in": owner_ok,
        "tech_logged_in": tech_ok,
        "is_owner": owner_ok,
        "is_tech": tech_ok,
    }


def _safe_next(val: str | None, fallback: str = "/") -> str:
    raw = (val or "").strip()
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    return fallback


@app.before_request
def protect_routes():
    endpoint = request.endpoint
    if endpoint in H.OPEN_ENDPOINTS or endpoint is None:
        return None

    owner_pw = H.owner_password()
    tech_pw = H.tech_password()

    if endpoint in H.TECH_OR_OWNER_ENDPOINTS:
        if H.is_owner() or H.is_tech():
            return None
        # Prefer tech login for create; owner login otherwise
        if endpoint == "new_request" and tech_pw:
            return redirect(url_for("tech_login", next=request.path))
        if owner_pw:
            return redirect(url_for("login", next=request.path))
        return None

    # All other routes require owner (when password set)
    if not owner_pw:
        return None
    if H.is_owner():
        return None
    nxt = request.path if request.method == "GET" else "/"
    return redirect(url_for("login", next=nxt))


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "smtp_configured": H.smtp_configured(),
            "twilio_configured": H.twilio_configured(),
        }
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = _safe_next(request.values.get("next"), "/")
    if not H.owner_password():
        session["owner"] = True
        return redirect(nxt)
    if session.get("owner"):
        return redirect(nxt)
    error = None
    if request.method == "POST":
        provided = (request.form.get("password") or "").encode("utf-8")
        expected = H.owner_password().encode("utf-8")
        ok = len(provided) == len(expected) and secrets.compare_digest(provided, expected)
        if ok:
            session["owner"] = True
            session.pop("tech", None)
            return redirect(nxt)
        error = "Incorrect password."
    return render_template("login.html", next=nxt, error=error, public=True, mode="owner")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/tech/login", methods=["GET", "POST"])
def tech_login():
    nxt = _safe_next(request.values.get("next"), url_for("new_request"))
    if not H.tech_password():
        # No tech password: fall back to owner session requirement
        if H.is_owner():
            return redirect(nxt)
        return redirect(url_for("login", next=nxt))
    if session.get("tech") or session.get("owner"):
        return redirect(nxt)
    error = None
    if request.method == "POST":
        provided = (request.form.get("password") or "").encode("utf-8")
        expected = H.tech_password().encode("utf-8")
        ok = len(provided) == len(expected) and secrets.compare_digest(provided, expected)
        if ok:
            session["tech"] = True
            return redirect(nxt)
        error = "Incorrect tech password."
    return render_template("login.html", next=nxt, error=error, public=True, mode="tech")


@app.get("/tech/logout")
def tech_logout():
    session.pop("tech", None)
    return redirect(url_for("tech_login"))


@app.get("/")
def index():
    pending = H.list_requests(status="pending", limit=50)
    recent = [
        r
        for r in H.list_requests(limit=30)
        if r["status"] in {"approved", "denied", "cancelled"}
    ][:20]
    return render_template("index.html", pending=pending, recent=recent)


@app.route("/people", methods=["GET", "POST"])
def people():
    if request.method == "POST":
        action = (request.form.get("action") or "create").strip()
        if action == "delete":
            pid = int(request.form.get("id") or "0")
            try:
                H.delete_person(pid)
                flash("Person removed.", "ok")
            except Exception as exc:  # noqa: BLE001
                flash(f"Could not delete: {exc}", "error")
            return redirect(url_for("people"))

        name = (request.form.get("name") or "").strip()
        role = (request.form.get("role") or "tech").strip()
        phone = (request.form.get("phone") or "").strip() or None
        email = (request.form.get("email") or "").strip() or None
        notes = (request.form.get("notes") or "").strip() or None
        if role not in {"tech", "office"}:
            flash("Role must be tech or office.", "error")
            return redirect(url_for("people"))
        if not name:
            flash("Name is required.", "error")
            return redirect(url_for("people"))

        pid_raw = (request.form.get("id") or "").strip()
        if action == "update" and pid_raw:
            H.update_person(int(pid_raw), name, role, phone=phone, email=email, notes=notes)
            flash("Person updated.", "ok")
        else:
            H.create_person(name, role, phone=phone, email=email, notes=notes)
            flash("Person added.", "ok")
        return redirect(url_for("people"))

    return render_template("people.html", people=H.list_people())


@app.route("/matrix", methods=["GET", "POST"])
def matrix():
    if request.method == "POST":
        action = (request.form.get("action") or "create").strip()
        if action == "delete":
            rid = int(request.form.get("id") or "0")
            H.delete_matrix_row(rid)
            flash("Matrix row removed.", "ok")
            return redirect(url_for("matrix"))

        if action == "import":
            f = request.files.get("file")
            if not f or not f.filename:
                flash("Choose a CSV file.", "error")
                return redirect(url_for("matrix"))
            text = f.read().decode("utf-8-sig", errors="replace")
            n = H.import_matrix_csv(text)
            flash(f"Imported {n} matrix row(s).", "ok")
            return redirect(url_for("matrix"))

        from_sku = (request.form.get("from_sku") or "").strip() or None
        from_name = (request.form.get("from_name") or "").strip() or None
        to_sku = (request.form.get("to_sku") or "").strip() or None
        to_name = (request.form.get("to_name") or "").strip() or None
        notes = (request.form.get("notes") or "").strip() or None
        active = request.form.get("active") == "1"
        if not any([from_sku, from_name, to_sku, to_name]):
            flash("Need at least one from/to field.", "error")
            return redirect(url_for("matrix"))

        rid_raw = (request.form.get("id") or "").strip()
        if action == "update" and rid_raw:
            H.update_matrix_row(
                int(rid_raw),
                from_sku=from_sku,
                from_name=from_name,
                to_sku=to_sku,
                to_name=to_name,
                notes=notes,
                active=active,
            )
            flash("Matrix row updated.", "ok")
        else:
            H.create_matrix_row(
                from_sku=from_sku,
                from_name=from_name,
                to_sku=to_sku,
                to_name=to_name,
                notes=notes,
                active=active,
            )
            flash("Matrix row added.", "ok")
        return redirect(url_for("matrix"))

    return render_template("matrix.html", rows=H.list_matrix())


@app.get("/matrix/export.csv")
def matrix_export():
    data = H.matrix_csv_bytes()
    return Response(
        data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=matrix.csv"},
    )

# Remaining routes live in routes_extra (imported for registration).
import routes_extra  # noqa: E402,F401

# Ensure schema on import for gunicorn / tests
try:
    init_db()
except Exception:
    pass


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(__import__("os").environ.get("PORT", "8080")))
