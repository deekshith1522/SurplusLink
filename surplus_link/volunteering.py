"""M4 volunteer assignment, collection and delivery for SurplusLink.

Volunteers accept a claimed pickup, then advance it claimed -> collected ->
delivered. Each change is one transaction using BEGIN IMMEDIATE, and every
rule (capacity, availability, deadline, assignment) is rechecked inside that
transaction. Expiry is enforced on the server: overdue, uncollected listings
are expired and their active claim reservations are released.
"""

from flask import Blueprint, abort, redirect, render_template, url_for

from .db import get_db
from .matching import check_volunteer_eligibility
from .timeutils import now_utc, parse_utc, to_utc_iso
from .views import current_persona

volunteering_bp = Blueprint("volunteering", __name__)

MESSAGES = {
    "not_found": "Listing not found.",
    "not_volunteer": "Only a volunteer can accept or update a pickup.",
    "taken": "Another volunteer has already accepted this pickup.",
    "not_assigned": "Only the volunteer assigned to this pickup can update it.",
    "no_claim": "This listing has no active claim to collect.",
    "closed": "This pickup is no longer open: its deadline has passed or the listing is no longer claimed.",
    "ineligible": "You are not suitable for this pickup right now. The matches page explains why.",
    "bad_transition": "That status change is not allowed from the current status.",
    "after_deadline": "Collection is closed: the pickup-by deadline has passed.",
    "expired": "This listing expired before collection and its reservation was released.",
}


def _insert_status_event(db, listing_id, from_status, to_status, actor_persona_id, at_iso):
    db.execute(
        "INSERT INTO status_event (listing_id, from_status, to_status, actor_persona_id, at_utc) "
        "VALUES (?, ?, ?, ?, ?)",
        (listing_id, from_status, to_status, actor_persona_id, at_iso),
    )


def expire_overdue(db, now):
    """Expire posted or claimed listings whose pickup-by has passed.

    Collected listings are never expired. Returns the number of listings expired.
    """
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")
    try:
        candidates = db.execute(
            "SELECT id, status, pickup_by_utc FROM listing WHERE status IN ('posted', 'claimed')"
        ).fetchall()
        stamp = to_utc_iso(now)
        count = 0
        for row in candidates:
            if parse_utc(row["pickup_by_utc"]) > now:
                continue
            old = row["status"]
            cur = db.execute(
                "UPDATE listing SET status = 'expired', updated_at_utc = ? WHERE id = ? AND status = ?",
                (stamp, row["id"], old),
            )
            if cur.rowcount != 1:
                continue
            if old == "claimed":
                db.execute(
                    "UPDATE claim SET status = 'released' WHERE listing_id = ? AND status = 'active'",
                    (row["id"],),
                )
            _insert_status_event(db, row["id"], old, "expired", None, stamp)
            count += 1
        db.commit()
        return count
    except Exception:
        db.rollback()
        raise


def accept_pickup(db, listing_id, persona_id, volunteer_id, now):
    """Assign a volunteer to a claimed, unassigned pickup. Returns ok, code, claim_id."""
    now_iso = to_utc_iso(now)
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")
    try:
        listing = db.execute("SELECT * FROM listing WHERE id = ?", (listing_id,)).fetchone()
        if listing is None:
            db.rollback()
            return {"ok": False, "code": "not_found", "claim_id": None}

        claim = db.execute(
            "SELECT * FROM claim WHERE listing_id = ? AND status = 'active'", (listing_id,)
        ).fetchone()
        if listing["status"] != "claimed" or claim is None:
            db.rollback()
            return {"ok": False, "code": "closed", "claim_id": None}

        if claim["volunteer_id"] is not None:
            db.rollback()
            return {"ok": False, "code": "taken", "claim_id": None}

        if parse_utc(listing["pickup_by_utc"]) <= now:
            db.rollback()
            return {"ok": False, "code": "closed", "claim_id": None}

        entry = check_volunteer_eligibility(db, listing_id, volunteer_id, now)
        if entry is None or not entry["eligible"]:
            db.rollback()
            return {"ok": False, "code": "ineligible", "claim_id": None}

        cur = db.execute(
            "UPDATE claim SET volunteer_id = ?, assigned_at_utc = ? "
            "WHERE id = ? AND volunteer_id IS NULL AND status = 'active'",
            (volunteer_id, now_iso, claim["id"]),
        )
        if cur.rowcount != 1:
            db.rollback()
            return {"ok": False, "code": "taken", "claim_id": None}

        _insert_status_event(db, listing_id, "claimed", "claimed", persona_id, now_iso)
        db.commit()
        return {"ok": True, "code": None, "claim_id": claim["id"]}
    except Exception:
        db.rollback()
        raise


def advance(db, listing_id, persona_id, volunteer_id, target, now):
    """Move an assigned pickup to 'collected' or 'delivered'. Returns ok, code."""
    now_iso = to_utc_iso(now)
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")
    try:
        listing = db.execute("SELECT * FROM listing WHERE id = ?", (listing_id,)).fetchone()
        if listing is None:
            db.rollback()
            return {"ok": False, "code": "not_found"}

        claim = db.execute(
            "SELECT * FROM claim WHERE listing_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
            (listing_id,),
        ).fetchone()
        if claim is None:
            db.rollback()
            return {"ok": False, "code": "no_claim"}

        if claim["volunteer_id"] != volunteer_id:
            db.rollback()
            return {"ok": False, "code": "not_assigned"}

        old = listing["status"]
        if target == "collected":
            if old == "expired":
                db.rollback()
                return {"ok": False, "code": "expired"}
            if old != "claimed":
                db.rollback()
                return {"ok": False, "code": "bad_transition"}
            if parse_utc(listing["pickup_by_utc"]) <= now:
                db.rollback()
                return {"ok": False, "code": "after_deadline"}
            cur = db.execute(
                "UPDATE listing SET status = 'collected', updated_at_utc = ? "
                "WHERE id = ? AND status = 'claimed'",
                (now_iso, listing_id),
            )
            if cur.rowcount != 1:
                db.rollback()
                return {"ok": False, "code": "bad_transition"}
        elif target == "delivered":
            if old != "collected":
                db.rollback()
                return {"ok": False, "code": "bad_transition"}
            cur = db.execute(
                "UPDATE listing SET status = 'delivered', updated_at_utc = ? "
                "WHERE id = ? AND status = 'collected'",
                (now_iso, listing_id),
            )
            if cur.rowcount != 1:
                db.rollback()
                return {"ok": False, "code": "bad_transition"}
            cur = db.execute(
                "UPDATE claim SET status = 'completed' WHERE id = ? AND status = 'active'",
                (claim["id"],),
            )
            if cur.rowcount != 1:
                db.rollback()
                return {"ok": False, "code": "bad_transition"}
        else:
            db.rollback()
            return {"ok": False, "code": "bad_transition"}

        _insert_status_event(db, listing_id, old, target, persona_id, now_iso)
        db.commit()
        return {"ok": True, "code": None}
    except Exception:
        db.rollback()
        raise


def listing_timeline(db, listing_id):
    """Status events for a listing in insertion order, with the actor's display name."""
    return db.execute(
        "SELECT e.from_status, e.to_status, e.at_utc, p.display_name AS actor "
        "FROM status_event e LEFT JOIN persona p ON p.id = e.actor_persona_id "
        "WHERE e.listing_id = ? ORDER BY e.id",
        (listing_id,),
    ).fetchall()


def volunteer_board(db, volunteer_id, now):
    """Open pickups for this volunteer to accept, and pickups already assigned to them."""
    open_items = []
    rows = db.execute(
        "SELECT l.id AS listing_id, l.description, l.quantity_kg, l.pickup_by_utc, "
        "d.name AS donor_name, r.name AS receiver_name "
        "FROM listing l "
        "JOIN claim c ON c.listing_id = l.id AND c.status = 'active' "
        "JOIN organisation d ON d.id = l.donor_org_id "
        "JOIN organisation r ON r.id = c.receiver_org_id "
        "WHERE l.status = 'claimed' AND c.volunteer_id IS NULL"
    ).fetchall()
    for row in rows:
        if parse_utc(row["pickup_by_utc"]) <= now:
            continue
        if volunteer_id is None:
            eligible, reasons = False, ["No volunteer record is linked to this persona."]
        else:
            entry = check_volunteer_eligibility(db, row["listing_id"], volunteer_id, now)
            if entry is None:
                eligible, reasons = False, ["Pickup details are unavailable."]
            else:
                eligible, reasons = entry["eligible"], entry["reasons"]
        item = dict(row)
        item["eligible"] = bool(eligible)
        item["reasons"] = list(reasons)
        open_items.append(item)
    open_items.sort(key=lambda item: parse_utc(item["pickup_by_utc"]))

    mine = []
    if volunteer_id is not None:
        mine_rows = db.execute(
            "SELECT l.id AS listing_id, l.description, l.quantity_kg, l.pickup_by_utc, l.status, "
            "d.name AS donor_name, r.name AS receiver_name "
            "FROM listing l "
            "JOIN claim c ON c.listing_id = l.id AND c.volunteer_id = ? "
            "AND c.status IN ('active', 'completed') "
            "JOIN organisation d ON d.id = l.donor_org_id "
            "JOIN organisation r ON r.id = c.receiver_org_id "
            "WHERE l.status IN ('claimed', 'collected', 'delivered')",
            (volunteer_id,),
        ).fetchall()
        mine = [dict(row) for row in mine_rows]
        mine.sort(key=lambda item: parse_utc(item["pickup_by_utc"]))

    return {"open": open_items, "mine": mine}


def _volunteer_id_for(db, persona_id):
    row = db.execute("SELECT volunteer_id FROM persona WHERE id = ?", (persona_id,)).fetchone()
    return row["volunteer_id"] if row else None


def _volunteer_persona():
    """Return (persona, None) for a volunteer; otherwise (None, response) or abort(403)."""
    persona = current_persona()
    if persona is None:
        return None, redirect(url_for("main.home"))
    if persona["role"] != "volunteer":
        abort(403)
    return persona, None


@volunteering_bp.route("/volunteer/board")
def board():
    persona = current_persona()
    if persona is None:
        return redirect(url_for("main.home"))
    if persona["role"] != "volunteer":
        abort(403)
    db = get_db()
    vid = _volunteer_id_for(db, persona["id"])
    now = now_utc()
    expire_overdue(db, now)
    return render_template(
        "board.html",
        board=volunteer_board(db, vid, now),
        last_updated_utc=to_utc_iso(now),
    )


@volunteering_bp.route("/listings/<int:listing_id>/accept", methods=["POST"])
def accept_pickup_view(listing_id):
    persona, response = _volunteer_persona()
    if response is not None:
        return response
    db = get_db()
    now = now_utc()
    expire_overdue(db, now)
    vid = _volunteer_id_for(db, persona["id"])
    result = accept_pickup(db, listing_id, persona["id"], vid, now)
    if result["code"] == "not_found":
        abort(404)
    code = "accepted" if result["ok"] else result["code"]
    return redirect(
        url_for("donations.listing_detail", listing_id=listing_id, vol=code), code=303
    )


def _advance_view(listing_id, target, ok_code):
    persona, response = _volunteer_persona()
    if response is not None:
        return response
    db = get_db()
    now = now_utc()
    expire_overdue(db, now)
    vid = _volunteer_id_for(db, persona["id"])
    result = advance(db, listing_id, persona["id"], vid, target, now)
    if result["code"] == "not_found":
        abort(404)
    code = ok_code if result["ok"] else result["code"]
    return redirect(
        url_for("donations.listing_detail", listing_id=listing_id, vol=code), code=303
    )


@volunteering_bp.route("/listings/<int:listing_id>/collect", methods=["POST"])
def collect_view(listing_id):
    return _advance_view(listing_id, "collected", "collected")


@volunteering_bp.route("/listings/<int:listing_id>/deliver", methods=["POST"])
def deliver_view(listing_id):
    return _advance_view(listing_id, "delivered", "delivered")
