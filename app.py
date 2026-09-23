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
