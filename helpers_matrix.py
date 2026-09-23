"""SwapCheck helpers: matrix CRUD and CSV."""
from __future__ import annotations

import csv
import io

from helpers import (
    get_db,
    list_matrix,
    utc_now_iso,
)


def create_matrix_row(
    *,
    from_sku: str | None = None,
    from_name: str | None = None,
    to_sku: str | None = None,
    to_name: str | None = None,
    notes: str | None = None,
    active: bool = True,
) -> int:
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO matrix (from_sku, from_name, to_sku, to_name, notes, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            from_sku or None,
            from_name or None,
            to_sku or None,
            to_name or None,
            notes or None,
            1 if active else 0,
            utc_now_iso(),
        ),
    )
    db.commit()
    return int(cur.lastrowid)


def update_matrix_row(
    row_id: int,
    *,
    from_sku: str | None = None,
    from_name: str | None = None,
    to_sku: str | None = None,
    to_name: str | None = None,
    notes: str | None = None,
    active: bool = True,
) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE matrix SET from_sku = ?, from_name = ?, to_sku = ?, to_name = ?,
            notes = ?, active = ? WHERE id = ?
        """,
        (
            from_sku or None,
            from_name or None,
            to_sku or None,
            to_name or None,
            notes or None,
            1 if active else 0,
            row_id,
        ),
    )
    db.commit()


def delete_matrix_row(row_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM matrix WHERE id = ?", (row_id,))
    db.commit()


def _norm(val: str | None) -> str:
    return (val or "").strip().lower()


def matrix_lookup(
    from_sku: str | None,
    from_name: str | None,
    to_sku: str | None,
    to_name: str | None,
) -> bool:
    """True if an active matrix row matches from→to (SKU preferred, else name)."""
    rows = list_matrix(active_only=True)
    fs, fn = _norm(from_sku), _norm(from_name)
    ts, tn = _norm(to_sku), _norm(to_name)
    for row in rows:
        rfs, rfn = _norm(row["from_sku"]), _norm(row["from_name"])
        rts, rtn = _norm(row["to_sku"]), _norm(row["to_name"])
        from_ok = False
        if fs and rfs and fs == rfs:
            from_ok = True
        elif fn and rfn and fn == rfn:
            from_ok = True
        elif fs and rfn and fs == rfn:
            from_ok = True
        elif fn and rfs and fn == rfs:
            from_ok = True
        to_ok = False
        if ts and rts and ts == rts:
            to_ok = True
        elif tn and rtn and tn == rtn:
            to_ok = True
        elif ts and rtn and ts == rtn:
            to_ok = True
        elif tn and rts and tn == rts:
            to_ok = True
        if from_ok and to_ok:
            return True
    return False


def import_matrix_csv(text: str) -> int:
    """Import matrix rows from CSV. Returns count inserted."""
    reader = csv.DictReader(io.StringIO(text))
    count = 0
    for row in reader:
        if not row:
            continue
        # normalize keys
        keyed = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        from_sku = keyed.get("from_sku") or None
        from_name = keyed.get("from_name") or None
        to_sku = keyed.get("to_sku") or None
        to_name = keyed.get("to_name") or None
        notes = keyed.get("notes") or None
        active_raw = (keyed.get("active") or "1").lower()
        active = active_raw not in {"0", "false", "no", "n", "inactive"}
        if not any([from_sku, from_name, to_sku, to_name]):
            continue
        create_matrix_row(
            from_sku=from_sku,
            from_name=from_name,
            to_sku=to_sku,
            to_name=to_name,
            notes=notes,
            active=active,
        )
        count += 1
    return count


def matrix_csv_bytes() -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(
        ["from_sku", "from_name", "to_sku", "to_name", "notes", "active"]
    )
    for row in list_matrix():
        writer.writerow(
            [
                row["from_sku"] or "",
                row["from_name"] or "",
                row["to_sku"] or "",
                row["to_name"] or "",
                row["notes"] or "",
                "1" if row["active"] else "0",
            ]
        )
    return out.getvalue().encode("utf-8")
