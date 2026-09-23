"""SwapCheck helpers: env, db, matrix, uploads, notify."""
from __future__ import annotations

import base64
import csv
import io
import os
import re
import secrets
import smtplib
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from flask import g, session

APP_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = APP_ROOT / "swapcheck.db"
DEFAULT_UPLOAD = APP_ROOT / "data" / "uploads"

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
ALLOWED_IMAGE_MIME = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

OPEN_ENDPOINTS = {
    "health",
    "login",
    "logout",
    "tech_login",
    "tech_logout",
    "decide",
    "decide_photo",
    "static",
}

TECH_OR_OWNER_ENDPOINTS = {
    "new_request",
    "request_detail",
    "request_photo",
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def database_path() -> str:
    raw = _env("DATABASE_PATH")
    return raw if raw else str(DEFAULT_DB)


def upload_dir() -> Path:
    raw = _env("UPLOAD_DIR")
    path = Path(raw) if raw else DEFAULT_UPLOAD
    path.mkdir(parents=True, exist_ok=True)
    return path


def max_upload_bytes() -> int:
    try:
        mb = int(_env("MAX_UPLOAD_MB") or "5")
    except ValueError:
        mb = 5
    return max(1, mb) * 1024 * 1024


def owner_password() -> str:
    return os.environ.get("OWNER_PASSWORD", "").strip()


def tech_password() -> str:
    return os.environ.get("TECH_PASSWORD", "").strip()


def business_name() -> str:
    return _env("BUSINESS_NAME") or "SwapCheck"


def public_base_url() -> str:
    return _env("PUBLIC_BASE_URL").rstrip("/")


def smtp_configured() -> bool:
    return bool(_env("SMTP_HOST") and _env("FROM_EMAIL"))


def twilio_configured() -> bool:
    return bool(
        _env("TWILIO_ACCOUNT_SID")
        and _env("TWILIO_AUTH_TOKEN")
        and _env("TWILIO_FROM_NUMBER")
    )


def marketing_url() -> str:
    return _env("MARKETING_URL")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_now_iso() -> str:
    return to_iso(utc_now())


def decide_url(token: str) -> str:
    base = public_base_url() or "http://localhost:8080"
    return f"{base}/d/{token}"


def is_owner() -> bool:
    if not owner_password():
        return True
    return bool(session.get("owner"))


def is_tech() -> bool:
    return bool(session.get("tech"))


def connect_db(path: str | None = None) -> sqlite3.Connection:
    db_path = path or database_path()
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    db = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db


def get_db() -> sqlite3.Connection:
    db = getattr(g, "_db", None)
    if db is None:
        db = connect_db()
        g._db = db
    return db


def close_db(_exc: BaseException | None = None) -> None:
    db = getattr(g, "_db", None)
    if db is not None:
        db.close()
        g._db = None


def init_schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('tech', 'office')),
            phone TEXT,
            email TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS matrix (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_sku TEXT,
            from_name TEXT,
            to_sku TEXT,
            to_name TEXT,
            notes TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_ref TEXT NOT NULL,
            tech_person_id INTEGER NOT NULL REFERENCES people(id),
            from_sku TEXT,
            from_name TEXT,
            to_sku TEXT,
            to_name TEXT,
            reason TEXT,
            notes TEXT,
            photo_path TEXT,
            spec_text TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'approved', 'denied', 'cancelled')),
            matrix_hit INTEGER NOT NULL DEFAULT 0,
            decide_token TEXT NOT NULL UNIQUE,
            decided_at TEXT,
            decided_by TEXT,
            decision_note TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES requests(id),
            kind TEXT NOT NULL,
            actor TEXT,
            note TEXT,
            at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_people_role ON people(role);
        CREATE INDEX IF NOT EXISTS idx_matrix_active ON matrix(active);
        CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
        CREATE INDEX IF NOT EXISTS idx_requests_job_ref ON requests(job_ref);
        CREATE INDEX IF NOT EXISTS idx_requests_token ON requests(decide_token);
        CREATE INDEX IF NOT EXISTS idx_events_request_id ON events(request_id);
        """
    )
    db.commit()


def add_event(
    conn: sqlite3.Connection,
    request_id: int,
    kind: str,
    *,
    actor: str | None = None,
    note: str | None = None,
    at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO events (request_id, kind, actor, note, at) VALUES (?, ?, ?, ?, ?)",
        (request_id, kind, actor, note, at or utc_now_iso()),
    )


def list_people(role: str | None = None):
    db = get_db()
    if role:
        return db.execute(
            "SELECT * FROM people WHERE role = ? ORDER BY name COLLATE NOCASE",
            (role,),
        ).fetchall()
    return db.execute("SELECT * FROM people ORDER BY role, name COLLATE NOCASE").fetchall()


def get_person(person_id: int):
    return get_db().execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()


def create_person(
    name: str,
    role: str,
    *,
    phone: str | None = None,
    email: str | None = None,
    notes: str | None = None,
) -> int:
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO people (name, role, phone, email, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, role, phone, email, notes, utc_now_iso()),
    )
    db.commit()
    return int(cur.lastrowid)


def update_person(
    person_id: int,
    name: str,
    role: str,
    *,
    phone: str | None = None,
    email: str | None = None,
    notes: str | None = None,
) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE people SET name = ?, role = ?, phone = ?, email = ?, notes = ?
        WHERE id = ?
        """,
        (name, role, phone, email, notes, person_id),
    )
    db.commit()


def delete_person(person_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM people WHERE id = ?", (person_id,))
    db.commit()


def list_matrix(active_only: bool = False):
    db = get_db()
    if active_only:
        return db.execute(
            "SELECT * FROM matrix WHERE active = 1 ORDER BY id DESC"
        ).fetchall()
    return db.execute("SELECT * FROM matrix ORDER BY id DESC").fetchall()


def get_matrix_row(row_id: int):
    return get_db().execute("SELECT * FROM matrix WHERE id = ?", (row_id,)).fetchone()

from helpers_matrix import *  # noqa: E402,F401,F403
from helpers_data import *  # noqa: E402,F401,F403
from helpers_extra import *  # noqa: E402,F401,F403
