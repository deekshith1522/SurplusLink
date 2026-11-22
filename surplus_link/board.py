"""Public donation board: read-only browse of open, unexpired donations.

Anyone can browse. Claiming stays in claiming.py and is enforced there.
This module never expires listings and never writes to the database.
"""

from flask import Blueprint, render_template, request

from .db import get_db
from .donations import STORAGE_LABELS
from .timeutils import format_ist, now_utc, parse_utc
from .views import current_persona

board_bp = Blueprint("board", __name__)

STORAGE_VALUES = ("ambient", "chilled", "frozen")
STORAGE_OPTIONS = [
    ("", "All storage"),
    ("ambient", "Ambient"),
    ("chilled", "Chilled"),
    ("frozen", "Frozen"),
]
MAX_QUERY_LEN = 100

_OPEN_SQL = """
    SELECT l.id, l.description, l.quantity_kg, l.storage_type, l.allergens,
           l.pickup_by_utc, o.name AS donor_name
    FROM listing l
    JOIN organisation o ON o.id = l.donor_org_id
    WHERE l.status = 'posted'
    ORDER BY l.pickup_by_utc ASC, l.id ASC
"""


def available_listings(db, now, q="", storage="", sort="deadline"):
    """Return open, unexpired listings as display dicts, soonest pickup deadline first.

    Only "deadline" sorting is supported; any other sort value is treated as "deadline"
    (the SQL already orders by deadline).
    """
    needle = (q or "").strip().lower()
    results = []
    for row in db.execute(_OPEN_SQL).fetchall():
        if parse_utc(row["pickup_by_utc"]) <= now:
            continue
        if storage in STORAGE_VALUES and row["storage_type"] != storage:
            continue
        if needle:
            haystack = (
                row["description"].lower(),
                row["donor_name"].lower(),
                (row["allergens"] or "").lower(),
            )
            if not any(needle in text for text in haystack):
                continue
        storage_type = row["storage_type"]
        results.append(
            {
                "id": row["id"],
                "description": row["description"],
                "quantity_kg": row["quantity_kg"],
                "storage_type": storage_type,
                "storage_label": STORAGE_LABELS.get(storage_type, (storage_type or "").capitalize()),
                "allergens": [a.strip() for a in (row["allergens"] or "").split(",") if a.strip()],
                "donor_name": row["donor_name"],
                "pickup_by_ist": format_ist(row["pickup_by_utc"]),
                "pickup_by_utc": row["pickup_by_utc"],
            }
        )
    return results


@board_bp.route("/board")
def board_view():
    q = request.args.get("q", "").strip()[:MAX_QUERY_LEN]
    storage = request.args.get("storage", "")
    if storage not in STORAGE_VALUES:
        storage = ""

    db = get_db()
    now = now_utc()
    items = available_listings(db, now, q, storage)
    total_open = len(available_listings(db, now, "", ""))

    persona = current_persona()
    viewer_role = persona["role"] if persona is not None else None

    return render_template(
        "donation_board.html",
        items=items,
        q=q,
        storage=storage,
        storage_options=STORAGE_OPTIONS,
        total_open=total_open,
        is_filtered=bool(q or storage),
        viewer_role=viewer_role,
    )
