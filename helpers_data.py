"""SwapCheck helpers: request queries and uploads."""
from __future__ import annotations

import re
import secrets
from pathlib import Path

from helpers import (
    ALLOWED_IMAGE_EXT,
    ALLOWED_IMAGE_MIME,
    get_db,
    upload_dir,
)


def get_request(request_id: int):
    return get_db().execute(
        """
        SELECT r.*, p.name AS tech_name, p.phone AS tech_phone, p.email AS tech_email
        FROM requests r
        JOIN people p ON p.id = r.tech_person_id
        WHERE r.id = ?
        """,
        (request_id,),
    ).fetchone()


def get_request_by_token(token: str):
    return get_db().execute(
        """
        SELECT r.*, p.name AS tech_name, p.phone AS tech_phone, p.email AS tech_email
        FROM requests r
        JOIN people p ON p.id = r.tech_person_id
        WHERE r.decide_token = ?
        """,
        (token,),
    ).fetchone()


def list_requests(status: str | None = None, limit: int = 100):
    db = get_db()
    if status:
        return db.execute(
            """
            SELECT r.*, p.name AS tech_name
            FROM requests r
            JOIN people p ON p.id = r.tech_person_id
            WHERE r.status = ?
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (status, limit),
        ).fetchall()
    return db.execute(
        """
        SELECT r.*, p.name AS tech_name
        FROM requests r
        JOIN people p ON p.id = r.tech_person_id
        ORDER BY r.created_at DESC, r.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def list_events(request_id: int):
    return get_db().execute(
        "SELECT * FROM events WHERE request_id = ? ORDER BY id ASC",
        (request_id,),
    ).fetchall()


def list_job_trail(job_ref: str):
    return get_db().execute(
        """
        SELECT e.*, r.job_ref, r.from_sku, r.from_name, r.to_sku, r.to_name, r.status
        FROM events e
        JOIN requests r ON r.id = e.request_id
        WHERE r.job_ref = ?
        ORDER BY e.at ASC, e.id ASC
        """,
        (job_ref,),
    ).fetchall()


def list_job_requests(job_ref: str):
    return get_db().execute(
        """
        SELECT r.*, p.name AS tech_name
        FROM requests r
        JOIN people p ON p.id = r.tech_person_id
        WHERE r.job_ref = ?
        ORDER BY r.created_at ASC, r.id ASC
        """,
        (job_ref,),
    ).fetchall()


def safe_filename(original: str) -> str:
    base = Path(original).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(base).stem)[:80] or "photo"
    ext = Path(base).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXT:
        ext = ".jpg"
    return f"{stem}_{secrets.token_hex(6)}{ext}"


def save_upload(file_storage) -> str | None:
    """Save uploaded image; return relative filename stored under UPLOAD_DIR."""
    if file_storage is None or not getattr(file_storage, "filename", None):
        return None
    filename = file_storage.filename
    ext = Path(filename).suffix.lower()
    mime = (getattr(file_storage, "mimetype", None) or "").lower()
    if ext not in ALLOWED_IMAGE_EXT and mime not in ALLOWED_IMAGE_MIME:
        raise ValueError("Only image uploads are allowed (jpg, png, gif, webp).")
    if ext not in ALLOWED_IMAGE_EXT:
        # map mime → ext
        mime_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }
        ext = mime_map.get(mime, ".jpg")
        filename = Path(filename).stem + ext
    dest_name = safe_filename(filename)
    dest = upload_dir() / dest_name
    file_storage.save(dest)
    return dest_name


def photo_abs_path(photo_path: str | None) -> Path | None:
    if not photo_path:
        return None
    name = Path(photo_path).name  # prevent path traversal
    full = upload_dir() / name
    if full.is_file():
        return full
    return None
