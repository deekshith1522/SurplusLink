"""M1 donation form: validation, persistence, donor-only posting, detail page."""

import math
import zoneinfo
from datetime import datetime, timezone

from flask import Blueprint, abort, redirect, render_template, request, url_for
from werkzeug.datastructures import MultiDict

from . import timeutils
from .claiming import MESSAGES as CLAIM_MESSAGES, get_claimant
from .db import get_db
from .timeutils import now_utc, parse_utc, to_utc_iso
from .views import current_persona
from .volunteering import listing_timeline, expire_overdue, MESSAGES as VOL_MESSAGES

donations_bp = Blueprint("donations", __name__)

STORAGE_TYPES = ("ambient", "chilled", "frozen")
STORAGE_LABELS = {
    "ambient": "Ambient (dry goods, room temperature)",
    "chilled": "Chilled (refrigerated)",
    "frozen": "Frozen",
}
ALLERGENS = ("gluten", "dairy", "eggs", "nuts", "soy", "fish", "shellfish", "sesame")
MAX_DESCRIPTION_LEN = 200
MAX_NOTES_LEN = 500
MIN_QUANTITY_KG = 0.1
MAX_QUANTITY_KG = 1000.0
DEADLINE_FORMAT = "%Y-%m-%dT%H:%M"
IST = zoneinfo.ZoneInfo("Asia/Kolkata")

QUANTITY_MESSAGE = f"Enter a quantity between {MIN_QUANTITY_KG:g} and {MAX_QUANTITY_KG:g} kg."


def validate_listing_form(form, now):
    """Validate submitted listing fields. Returns (cleaned, errors)."""
    cleaned = {}
    errors = {}

    description = form.get("description", "").strip()
    if not description:
        errors["description"] = "Enter a description of the food."
    elif len(description) > MAX_DESCRIPTION_LEN:
        errors["description"] = f"Description must be {MAX_DESCRIPTION_LEN} characters or fewer."
    cleaned["description"] = description

    raw_quantity = form.get("quantity_kg", "").strip()
    quantity = None
    if not raw_quantity:
        errors["quantity_kg"] = QUANTITY_MESSAGE
    else:
        try:
            quantity = float(raw_quantity)
        except ValueError:
            quantity = None
        if quantity is None or not math.isfinite(quantity) or not (
            MIN_QUANTITY_KG <= quantity <= MAX_QUANTITY_KG
        ):
            errors["quantity_kg"] = QUANTITY_MESSAGE
    cleaned["quantity_kg"] = quantity

    raw_deadline = form.get("pickup_by", "").strip()
    cleaned["pickup_by_utc"] = None
    if not raw_deadline:
        errors["pickup_by"] = "Enter the pickup-by deadline."
    else:
        try:
            local = datetime.strptime(raw_deadline, DEADLINE_FORMAT).replace(tzinfo=IST)
        except ValueError:
            errors["pickup_by"] = "Enter a valid pickup-by date and time."
        else:
            if local <= now:
                errors["pickup_by"] = "Pickup-by must be in the future."
            else:
                cleaned["pickup_by_utc"] = timeutils.to_utc_iso(local)

    storage_type = form.get("storage_type", "").strip()
    if storage_type not in STORAGE_TYPES:
        errors["storage_type"] = "Choose a storage type."
    cleaned["storage_type"] = storage_type

    handling_notes = form.get("handling_notes", "").strip()
    if len(handling_notes) > MAX_NOTES_LEN:
        errors["handling_notes"] = f"Handling information must be {MAX_NOTES_LEN} characters or fewer."
    cleaned["handling_notes"] = handling_notes

    submitted = form.getlist("allergens")
    if any(a not in ALLERGENS for a in submitted):
        errors["allergens"] = "Choose allergens from the list."
    cleaned["allergens"] = ",".join(a for a in ALLERGENS if a in submitted)

    return cleaned, errors


def create_listing(db, donor_org_id, persona_id, cleaned, now):
    """Insert a posted listing and its first status event in one transaction."""
    stamp = timeutils.to_utc_iso(now)
    try:
        cursor = db.execute(
            """
            INSERT INTO listing (donor_org_id, description, quantity_kg, pickup_by_utc,
                                 storage_type, handling_notes, allergens, status,
                                 created_at_utc, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'posted', ?, ?)
            """,
            (
                donor_org_id,
                cleaned["description"],
                cleaned["quantity_kg"],
                cleaned["pickup_by_utc"],
                cleaned["storage_type"],
                cleaned["handling_notes"],
                cleaned["allergens"],
                stamp,
                stamp,
            ),
        )
        listing_id = cursor.lastrowid
        db.execute(
            """
            INSERT INTO status_event (listing_id, from_status, to_status, actor_persona_id, at_utc)
            VALUES (?, NULL, 'posted', ?, ?)
            """,
            (listing_id, persona_id, stamp),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return listing_id


def get_listing(db, listing_id):
    """Return the listing row joined with its donor organisation name, or None."""
    return db.execute(
        """
        SELECT l.*, o.name AS donor_name
        FROM listing l
        JOIN organisation o ON o.id = l.donor_org_id
        WHERE l.id = ?
        """,
        (listing_id,),
    ).fetchone()


def _require_donor():
    """Return (persona, None) for a donor; otherwise (None, response) or abort(403)."""
    persona = current_persona()
    if persona is None:
        return None, redirect(url_for("main.home"))
    if persona["role"] != "donor":
        abort(403)
    return persona, None


def _render_form(values, errors, status):
    return (
        render_template(
            "donate.html",
            values=values,
            errors=errors,
            storage_options=[(key, STORAGE_LABELS[key]) for key in STORAGE_TYPES],
            allergen_options=ALLERGENS,
        ),
        status,
    )


@donations_bp.route("/donate", methods=["GET", "POST"])
def new_listing():
    persona, response = _require_donor()
    if response is not None:
        return response

    if request.method == "GET":
        return _render_form(MultiDict(), {}, 200)

    now = timeutils.now_utc()
    cleaned, errors = validate_listing_form(request.form, now)
    if errors:
        return _render_form(request.form, errors, 400)

    db = get_db()
    row = db.execute("SELECT organisation_id FROM persona WHERE id = ?", (persona["id"],)).fetchone()
    listing_id = create_listing(db, row["organisation_id"], persona["id"], cleaned, now)
    return redirect(url_for("donations.listing_detail", listing_id=listing_id, created=1), code=303)


@donations_bp.route("/listings/<int:listing_id>")
def listing_detail(listing_id):
    db = get_db()
    now = now_utc()
    listing = get_listing(db, listing_id)
    if listing is None:
        abort(404)
    expire_overdue(db, now)
    listing = get_listing(db, listing_id)
    allergen_list = [a for a in (listing["allergens"] or "").split(",") if a]
    claimant_name = get_claimant(db, listing_id)
    claim_code = request.args.get("claim")
    claim_message = CLAIM_MESSAGES.get(claim_code) if claim_code else None
    claimed_ok = request.args.get("claimed") == "1"

    timeline = listing_timeline(db, listing_id)
    vol_code = request.args.get("vol")
    vol_message = VOL_MESSAGES.get(vol_code) if vol_code in VOL_MESSAGES else None
    vol_ok_text = {
        "accepted": "Pickup accepted. You are now assigned to collect this listing.",
        "collected": "Collection recorded. Deliver the food to the receiver.",
        "delivered": "Delivery recorded. The listing is delivered and the claim is completed.",
    }.get(vol_code)
    volunteer_row = db.execute(
        "SELECT v.name FROM claim c JOIN volunteer v ON v.id = c.volunteer_id "
        "WHERE c.listing_id = ? AND c.volunteer_id IS NOT NULL ORDER BY c.id DESC LIMIT 1",
        (listing_id,),
    ).fetchone()
    volunteer_name = volunteer_row["name"] if volunteer_row else None

    persona = current_persona()
    can_accept = can_collect = can_deliver = False
    if persona is not None and persona["role"] == "volunteer":
        my_row = db.execute(
            "SELECT volunteer_id FROM persona WHERE id = ?", (persona["id"],)
        ).fetchone()
        my_vid = my_row["volunteer_id"] if my_row else None
        claim = db.execute(
            "SELECT * FROM claim WHERE listing_id = ? AND status = 'active'", (listing_id,)
        ).fetchone()
        pickup_future = parse_utc(listing["pickup_by_utc"]) > now
        if claim is not None:
            can_accept = (
                listing["status"] == "claimed"
                and claim["volunteer_id"] is None
                and pickup_future
            )
            can_collect = (
                listing["status"] == "claimed"
                and my_vid is not None
                and claim["volunteer_id"] == my_vid
                and pickup_future
            )
            can_deliver = (
                listing["status"] == "collected"
                and my_vid is not None
                and claim["volunteer_id"] == my_vid
            )

    return render_template(
        "listing.html",
        listing=listing,
        allergen_list=allergen_list,
        show_created=request.args.get("created") == "1",
        claimant_name=claimant_name,
        claim_message=claim_message,
        claimed_ok=claimed_ok,
        timeline=timeline,
        vol_message=vol_message,
        vol_ok_text=vol_ok_text,
        volunteer_name=volunteer_name,
        can_accept=can_accept,
        can_collect=can_collect,
        can_deliver=can_deliver,
        last_updated_utc=to_utc_iso(now_utc()),
    )
