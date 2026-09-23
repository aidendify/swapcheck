"""SwapCheck remaining routes (imported by app for registration)."""
from __future__ import annotations

from flask import (
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

import helpers as H
from app import app


@app.route("/requests/new", methods=["GET", "POST"])
def new_request():
    techs = H.list_people(role="tech")
    if request.method == "GET":
        return render_template("request_new.html", techs=techs, form={})

    job_ref = (request.form.get("job_ref") or "").strip()
    tech_raw = (request.form.get("tech_person_id") or "").strip()
    from_sku = (request.form.get("from_sku") or "").strip() or None
    from_name = (request.form.get("from_name") or "").strip() or None
    to_sku = (request.form.get("to_sku") or "").strip() or None
    to_name = (request.form.get("to_name") or "").strip() or None
    reason = (request.form.get("reason") or "").strip() or None
    notes = (request.form.get("notes") or "").strip() or None
    spec_text = (request.form.get("spec_text") or "").strip() or None

    errors: list[str] = []
    if not job_ref:
        errors.append("Job ref is required.")
    tech_id: int | None = None
    try:
        tech_id = int(tech_raw)
        person = H.get_person(tech_id)
        if person is None or person["role"] != "tech":
            errors.append("Select a tech person.")
            tech_id = None
    except ValueError:
        errors.append("Select a tech person.")
    if not any([from_sku, from_name]):
        errors.append("From part SKU or name is required.")
    if not any([to_sku, to_name]):
        errors.append("To part SKU or name is required.")

    photo_path = None
    photo = request.files.get("photo")
    if photo and photo.filename:
        try:
            photo_path = H.save_upload(photo)
        except ValueError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Photo upload failed: {exc}")

    if errors:
        for e in errors:
            flash(e, "error")
        return render_template(
            "request_new.html", techs=techs, form=request.form
        ), 400

    actor = "owner" if H.is_owner() and not H.is_tech() else "tech"
    rid = H.create_request(
        job_ref=job_ref,
        tech_person_id=tech_id,  # type: ignore[arg-type]
        from_sku=from_sku,
        from_name=from_name,
        to_sku=to_sku,
        to_name=to_name,
        reason=reason,
        notes=notes,
        photo_path=photo_path,
        spec_text=spec_text,
        actor=actor,
    )
    row = H.get_request(rid)
    try:
        H.notify_office_decide(row)
    except Exception:  # noqa: BLE001
        pass

    hit = bool(row["matrix_hit"])
    flash(
        "Swap request created. Matrix: "
        + ("pre-approved" if hit else "needs office decision"),
        "ok",
    )
    if H.is_owner():
        return redirect(url_for("request_detail", request_id=rid))
    return redirect(url_for("request_detail", request_id=rid))


@app.get("/requests/<int:request_id>")
def request_detail(request_id: int):
    row = H.get_request(request_id)
    if row is None:
        abort(404)
    events = H.list_events(request_id)
    decide_link = H.decide_url(row["decide_token"])
    return render_template(
        "request_detail.html",
        req=row,
        events=events,
        decide_link=decide_link,
    )


@app.get("/requests/<int:request_id>/photo")
def request_photo(request_id: int):
    if not (H.is_owner() or H.is_tech()):
        abort(403)
    row = H.get_request(request_id)
    if row is None or not row["photo_path"]:
        abort(404)
    path = H.photo_abs_path(row["photo_path"])
    if path is None:
        abort(404)
    return send_file(path)


def _do_decide(request_id: int, action: str, actor: str, note: str | None):
    try:
        H.decide_request(request_id, action, actor=actor, decision_note=note)
        flash(f"Request {action}.", "ok")
    except RuntimeError:
        flash("Already decided — locked.", "error")
    except LookupError:
        abort(404)


@app.post("/requests/<int:request_id>/approve")
def approve_request(request_id: int):
    note = (request.form.get("decision_note") or "").strip() or None
    _do_decide(request_id, "approved", "owner", note)
    return redirect(url_for("request_detail", request_id=request_id))


@app.post("/requests/<int:request_id>/deny")
def deny_request(request_id: int):
    note = (request.form.get("decision_note") or "").strip() or None
    _do_decide(request_id, "denied", "owner", note)
    return redirect(url_for("request_detail", request_id=request_id))


@app.post("/requests/<int:request_id>/cancel")
def cancel_request(request_id: int):
    note = (request.form.get("decision_note") or "").strip() or None
    _do_decide(request_id, "cancelled", "owner", note)
    return redirect(url_for("request_detail", request_id=request_id))


@app.route("/d/<token>", methods=["GET", "POST"])
def decide(token: str):
    row = H.get_request_by_token(token)
    if row is None:
        abort(404)
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        note = (request.form.get("decision_note") or "").strip() or None
        if action not in {"approved", "denied"}:
            flash("Choose Approve or Deny.", "error")
        elif row["status"] != "pending":
            flash("Already decided — locked.", "error")
        else:
            try:
                H.decide_request(row["id"], action, actor="magic", decision_note=note)
                flash(f"Request {action}.", "ok")
            except RuntimeError:
                flash("Already decided — locked.", "error")
        row = H.get_request_by_token(token)
    events = H.list_events(row["id"])
    return render_template(
        "decide.html",
        req=row,
        events=events,
        public=True,
        token=token,
    )


@app.get("/d/<token>/photo")
def decide_photo(token: str):
    row = H.get_request_by_token(token)
    if row is None or not row["photo_path"]:
        abort(404)
    path = H.photo_abs_path(row["photo_path"])
    if path is None:
        abort(404)
    return send_file(path)


@app.get("/jobs/<path:job_ref>")
def job_trail(job_ref: str):
    reqs = H.list_job_requests(job_ref)
    trail = H.list_job_trail(job_ref)
    return render_template("job.html", job_ref=job_ref, requests=reqs, trail=trail)


@app.get("/export/requests.csv")
def export_requests():
    data = H.requests_csv_bytes()
    return Response(
        data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=requests.csv"},
    )


@app.get("/export/events.csv")
def export_events():
    data = H.events_csv_bytes()
    return Response(
        data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=events.csv"},
    )


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        flash(
            "Runtime contacts are set via .env (OFFICE_EMAIL, OFFICE_PHONE, SMTP_*, TWILIO_*). "
            "Restart Compose after editing .env.",
            "ok",
        )
        return redirect(url_for("settings"))
    return render_template(
        "settings.html",
        office_email=H._env("OFFICE_EMAIL"),
        office_phone=H._env("OFFICE_PHONE"),
        public_base_url=H.public_base_url(),
        max_upload_mb=H.max_upload_bytes() // (1024 * 1024),
        upload_dir=str(H.upload_dir()),
        database_path=H.database_path(),
    )
