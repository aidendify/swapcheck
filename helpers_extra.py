"""SwapCheck helpers: requests, decide, notify, CSV export."""
from __future__ import annotations

import base64
import csv
import io
import os
import secrets
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from helpers import (
    ALLOWED_IMAGE_EXT,
    ALLOWED_IMAGE_MIME,
    _env,
    add_event,
    business_name,
    decide_url,
    get_db,
    get_request,
    list_requests,
    matrix_lookup,
    photo_abs_path,
    safe_filename,
    smtp_configured,
    twilio_configured,
    upload_dir,
    utc_now_iso,
)


def create_request(
    *,
    job_ref: str,
    tech_person_id: int,
    from_sku: str | None,
    from_name: str | None,
    to_sku: str | None,
    to_name: str | None,
    reason: str | None = None,
    notes: str | None = None,
    photo_path: str | None = None,
    spec_text: str | None = None,
    actor: str = "tech",
) -> int:
    hit = matrix_lookup(from_sku, from_name, to_sku, to_name)
    token = secrets.token_urlsafe(32)
    now = utc_now_iso()
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO requests (
            job_ref, tech_person_id, from_sku, from_name, to_sku, to_name,
            reason, notes, photo_path, spec_text, status, matrix_hit,
            decide_token, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            job_ref,
            tech_person_id,
            from_sku or None,
            from_name or None,
            to_sku or None,
            to_name or None,
            reason or None,
            notes or None,
            photo_path or None,
            spec_text or None,
            1 if hit else 0,
            token,
            now,
        ),
    )
    rid = int(cur.lastrowid)
    add_event(db, rid, "created", actor=actor, note=f"job_ref={job_ref}", at=now)
    add_event(
        db,
        rid,
        "matrix_hit" if hit else "matrix_miss",
        actor="system",
        note="pre-approved" if hit else "needs office decision",
        at=now,
    )
    if photo_path:
        add_event(db, rid, "photo_added", actor=actor, note=photo_path, at=now)
    db.commit()
    return rid


def decide_request(
    request_id: int,
    action: str,
    *,
    actor: str,
    decision_note: str | None = None,
) -> None:
    """action: approved | denied | cancelled"""
    if action not in {"approved", "denied", "cancelled"}:
        raise ValueError("Invalid action")
    db = get_db()
    row = db.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        raise LookupError("Request not found")
    if row["status"] != "pending":
        raise RuntimeError("Already decided")
    now = utc_now_iso()
    db.execute(
        """
        UPDATE requests SET status = ?, decided_at = ?, decided_by = ?, decision_note = ?
        WHERE id = ?
        """,
        (action, now, actor, decision_note or None, request_id),
    )
    kind = {"approved": "approved", "denied": "denied", "cancelled": "cancelled"}[action]
    add_event(db, request_id, kind, actor=actor, note=decision_note, at=now)
    db.commit()


def send_smtp(to_email: str, subject: str, body: str) -> None:
    host = _env("SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP is not configured.")
    from_email = _env("FROM_EMAIL")
    if not from_email:
        raise RuntimeError("FROM_EMAIL is required to send mail.")
    port = int(_env("SMTP_PORT") or "587")
    user = _env("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD", "")
    tls_raw = _env("SMTP_TLS") or "true"
    use_tls = tls_raw.lower() in {"1", "true", "yes", "on"}
    from_name = _env("FROM_NAME")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_email)) if from_name else from_email
    msg["To"] = to_email
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if use_tls:
            smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)


def send_twilio_sms(to_phone: str, body: str) -> None:
    sid = _env("TWILIO_ACCOUNT_SID")
    token = _env("TWILIO_AUTH_TOKEN")
    from_number = _env("TWILIO_FROM_NUMBER")
    if not (sid and token and from_number):
        raise RuntimeError("Twilio is not configured.")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urllib.parse.urlencode(
        {"To": to_phone, "From": from_number, "Body": body}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    credentials = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Twilio HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Twilio HTTP {exc.code}") from exc


def notify_office_decide(req_row) -> bool:
    """Send decide link to OFFICE_EMAIL / OFFICE_PHONE if configured. Returns True if any sent."""
    link = decide_url(req_row["decide_token"])
    biz = business_name()
    subject = f"[{biz}] Swap decide needed — job {req_row['job_ref']}"
    body = (
        f"A substitute part request needs a decision.\n\n"
        f"Job: {req_row['job_ref']}\n"
        f"From: {req_row['from_sku'] or ''} {req_row['from_name'] or ''}\n"
        f"To: {req_row['to_sku'] or ''} {req_row['to_name'] or ''}\n"
        f"Matrix: {'pre-approved' if req_row['matrix_hit'] else 'needs office decision'}\n\n"
        f"Decide here:\n{link}\n"
    )
    sms = (
        f"{biz}: swap decide for job {req_row['job_ref']}. "
        f"{'Matrix hit.' if req_row['matrix_hit'] else 'Needs decision.'} {link}"
    )
    sent = False
    office_email = _env("OFFICE_EMAIL")
    office_phone = _env("OFFICE_PHONE")
    db = get_db()
    if office_email and smtp_configured():
        try:
            send_smtp(office_email, subject, body)
            add_event(db, req_row["id"], "notified", actor="system", note=f"email:{office_email}")
            sent = True
        except Exception as exc:  # noqa: BLE001
            add_event(db, req_row["id"], "notified", actor="system", note=f"email_fail:{exc}")
    if office_phone and twilio_configured():
        try:
            send_twilio_sms(office_phone, sms)
            add_event(db, req_row["id"], "notified", actor="system", note=f"sms:{office_phone}")
            sent = True
        except Exception as exc:  # noqa: BLE001
            add_event(db, req_row["id"], "notified", actor="system", note=f"sms_fail:{exc}")
    if sent:
        db.commit()
    return sent


def requests_csv_bytes() -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        [
            "id",
            "job_ref",
            "tech_name",
            "from_sku",
            "from_name",
            "to_sku",
            "to_name",
            "reason",
            "notes",
            "spec_text",
            "status",
            "matrix_hit",
            "decision_note",
            "decided_by",
            "decided_at",
            "created_at",
            "has_photo",
        ]
    )
    for row in list_requests(limit=10000):
        writer.writerow(
            [
                row["id"],
                row["job_ref"],
                row["tech_name"],
                row["from_sku"] or "",
                row["from_name"] or "",
                row["to_sku"] or "",
                row["to_name"] or "",
                row["reason"] or "",
                row["notes"] or "",
                row["spec_text"] or "",
                row["status"],
                "1" if row["matrix_hit"] else "0",
                row["decision_note"] or "",
                row["decided_by"] or "",
                row["decided_at"] or "",
                row["created_at"],
                "1" if row["photo_path"] else "0",
            ]
        )
    return out.getvalue().encode("utf-8")


def events_csv_bytes() -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "request_id", "job_ref", "kind", "actor", "note", "at"])
    rows = get_db().execute(
        """
        SELECT e.*, r.job_ref
        FROM events e
        JOIN requests r ON r.id = e.request_id
        ORDER BY e.id ASC
        """
    ).fetchall()
    for row in rows:
        writer.writerow(
            [
                row["id"],
                row["request_id"],
                row["job_ref"],
                row["kind"],
                row["actor"] or "",
                row["note"] or "",
                row["at"],
            ]
        )
    return out.getvalue().encode("utf-8")
