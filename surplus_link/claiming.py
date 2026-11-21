"""Receiver claiming (M3): atomic claim, eligibility rechecked inside the transaction.

BEGIN IMMEDIATE serialises writers, so two receivers cannot both win the same
listing. Eligibility is rechecked inside the transaction, not only at page load.
"""

import sqlite3

from flask import Blueprint, abort, redirect, url_for

from .db import get_db
from .matching import check_receiver_eligibility
from .timeutils import now_utc, parse_utc, to_utc_iso
from .views import current_persona

claiming_bp = Blueprint("claiming", __name__)

MESSAGES = {
    "taken": "This listing has already been claimed by another organisation.",
    "duplicate": "Your organisation has already claimed this listing.",
    "closed": "This listing is no longer open for claiming: it is not posted, or its pickup-by deadline has passed.",
    "ineligible": "Your organisation cannot claim this listing right now. The ranked matches page explains why.",
    "not_found": "Listing not found.",
}


def _insert_claim(db, listing_id, org_id, quantity_kg, now_iso):
    cursor = db.execute(
        "INSERT INTO claim (listing_id, receiver_org_id, volunteer_id, quantity_kg, status, claimed_at_utc) "
        "VALUES (?, ?, NULL, ?, 'active', ?)",
        (listing_id, org_id, quantity_kg, now_iso),
    )
    return cursor.lastrowid


def _insert_status_event(db, listing_id, from_status, to_status, actor_persona_id, at_iso):
    db.execute(
        "INSERT INTO status_event (listing_id, from_status, to_status, actor_persona_id, at_utc) "
        "VALUES (?, ?, ?, ?, ?)",
        (listing_id, from_status, to_status, actor_persona_id, at_iso),
    )


def attempt_claim(db, listing_id, persona_id, org_id, now):
    """Try to claim a listing for a receiver organisation in one transaction.

    Returns a dict with ok (bool), code (None when ok, else a MESSAGES key)
    and claim_id (int when ok, else None).
    """
    now_iso = to_utc_iso(now)
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")
    try:
        listing = db.execute(
            "SELECT * FROM listing WHERE id = ?", (listing_id,)
        ).fetchone()
        if listing is None:
            db.rollback()
            return {"ok": False, "code": "not_found", "claim_id": None}

        existing = db.execute(
            "SELECT receiver_org_id FROM claim WHERE listing_id = ?", (listing_id,)
        ).fetchone()
        if listing["status"] != "posted" or parse_utc(listing["pickup_by_utc"]) <= now:
            db.rollback()
            if existing is not None and existing["receiver_org_id"] == org_id:
                code = "duplicate"
            elif existing is not None:
                code = "taken"
            else:
                code = "closed"
            return {"ok": False, "code": code, "claim_id": None}

        entry = check_receiver_eligibility(db, listing_id, org_id, now)
        if entry is None or not entry["eligible"]:
            db.rollback()
            return {"ok": False, "code": "ineligible", "claim_id": None}

        cur = db.execute(
            "UPDATE listing SET status = 'claimed', updated_at_utc = ? "
            "WHERE id = ? AND status = 'posted'",
            (now_iso, listing_id),
        )
        if cur.rowcount != 1:
            db.rollback()
            return {"ok": False, "code": "taken", "claim_id": None}

        try:
            claim_id = _insert_claim(
                db, listing_id, org_id, float(listing["quantity_kg"]), now_iso
            )
        except sqlite3.IntegrityError:
            db.rollback()
            return {"ok": False, "code": "taken", "claim_id": None}

        _insert_status_event(
            db, listing_id, "posted", "claimed", persona_id, now_iso
        )
        db.commit()
        return {"ok": True, "code": None, "claim_id": claim_id}
    except Exception:
        db.rollback()
        raise


def get_claimant(db, listing_id):
    """Return the name of the organisation holding the listing's claim, or None."""
    row = db.execute(
        "SELECT o.name FROM claim c JOIN organisation o ON o.id = c.receiver_org_id "
        "WHERE c.listing_id = ? AND c.status IN ('active', 'completed')",
        (listing_id,),
    ).fetchone()
    return row["name"] if row is not None else None


@claiming_bp.route("/listings/<int:listing_id>/claim", methods=["POST"])
def claim_view(listing_id):
    persona = current_persona()
    if persona is None:
        return redirect(url_for("main.home"))
    if persona["role"] != "receiver":
        abort(403)

    db = get_db()
    row = db.execute(
        "SELECT organisation_id FROM persona WHERE id = ?", (persona["id"],)
    ).fetchone()
    org_id = row["organisation_id"]

    result = attempt_claim(db, listing_id, persona["id"], org_id, now_utc())
    if result["code"] == "not_found":
        abort(404)
    if result["ok"]:
        return redirect(
            url_for("donations.listing_detail", listing_id=listing_id, claimed=1),
            code=303,
        )
    return redirect(
        url_for("donations.listing_detail", listing_id=listing_id, claim=result["code"]),
        code=303,
    )
