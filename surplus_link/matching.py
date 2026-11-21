"""Explainable matching for SurplusLink (M2).

Ranks receiver organisations and volunteers for a posted listing and explains
every score and every ineligibility reason. Nothing is cached: each call
recalculates eligibility from the current database rows.

Travel times are planning estimates (straight-line distance x road factor at
an assumed average speed), not measured routes. Coordinates are synthetic.
This module does not claim listings and does not change listing status.
"""

import math
import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, render_template

from .db import get_db
from .timeutils import now_utc, parse_utc, to_utc_iso

ROAD_FACTOR = 1.3
AVERAGE_SPEED_KMH = 20.0
PICKUP_BUFFER_MIN = 15
MAX_DISTANCE_KM = 15.0
TIMING_CAP_MIN = 120
CAPACITY_WINDOW_DAYS = 7

RECEIVER_WEIGHTS = {"distance": 35, "quantity": 25, "timing": 20, "need": 20}
VOLUNTEER_WEIGHTS = {"distance": 40, "capacity": 30, "timing": 30}

ASSUMPTIONS = [
    "Travel time: straight-line distance x 1.3 road factor at an assumed "
    "average of 20 km/h. Planning estimate, not measured.",
    "Pickup buffer: 15 minutes are reserved at the end of travel before the "
    "pickup-by deadline.",
    "Volunteer travel period must fit the same 15-minute pickup buffer as receivers.",
    "Distance score: full points at 0 km, zero points at 15 km or more.",
    "Timing slack: slack is capped at 120 minutes when scoring.",
    "Weekly intake: all active claims count as outstanding commitments "
    "regardless of age; completed claims count only if claimed in the last "
    "7 days; released claims never count; each claim counts once.",
    "Coordinates are synthetic.",
    "Need level 3 is the highest need.",
    "A volunteer without a recorded availability window is treated as "
    "unavailable.",
]

IST = ZoneInfo("Asia/Kolkata")

matching_bp = Blueprint("matching", __name__)


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in km on a sphere of radius 6371 km."""
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(min(1.0, math.sqrt(a)))


def road_km(lat1, lng1, lat2, lng2):
    """Approximate road distance: straight-line distance x ROAD_FACTOR."""
    return haversine_km(lat1, lng1, lat2, lng2) * ROAD_FACTOR


def travel_minutes(km):
    """Estimated travel time in minutes at AVERAGE_SPEED_KMH."""
    return km / AVERAGE_SPEED_KMH * 60.0


def quantity_fit(quantity, need):
    """Ratio of smaller to larger value, in [0, 1]. 0.0 if either is <= 0."""
    if quantity <= 0 or need <= 0:
        return 0.0
    return min(quantity, need) / max(quantity, need)


def split_csv(text):
    """Split a comma list into stripped, non-empty items. None gives []."""
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def parse_hhmm(text):
    """Return minutes since midnight for 'HH:MM', or None if not parseable."""
    if not text:
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text)
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def reserved_kg(db, receiver_org_id, now):
    """Return (active_kg, completed_window_kg) for a receiver.

    active_kg: total quantity of the receiver's active claims, regardless of
    how old they are, so outstanding commitments never drop out of capacity.
    completed_window_kg: total quantity of completed claims claimed within the
    last CAPACITY_WINDOW_DAYS. Released claims never count. Each claim is
    counted once.
    """
    active_row = db.execute(
        "SELECT COALESCE(SUM(quantity_kg), 0) AS total FROM claim "
        "WHERE receiver_org_id = ? AND status = 'active'",
        (receiver_org_id,),
    ).fetchone()
    active_kg = float(active_row["total"] or 0.0)

    window_start = now - timedelta(days=CAPACITY_WINDOW_DAYS)
    rows = db.execute(
        "SELECT quantity_kg, claimed_at_utc FROM claim "
        "WHERE receiver_org_id = ? AND status = 'completed'",
        (receiver_org_id,),
    ).fetchall()
    completed_window_kg = 0.0
    for row in rows:
        if parse_utc(row["claimed_at_utc"]) >= window_start:
            completed_window_kg += float(row["quantity_kg"] or 0.0)
    return active_kg, completed_window_kg


def _breakdown_item(label, value, raw_points, weight):
    return {
        "factor": label,
        "value": value,
        "points": round(raw_points, 1),
        "max_points": weight,
    }


def _rank(entries, id_key):
    """Eligible first (score desc, distance asc, id asc); then ineligible by name."""
    eligible = [e for e in entries if e["eligible"]]
    ineligible = [e for e in entries if not e["eligible"]]
    eligible.sort(key=lambda e: (-e["score"], e["distance_km"], e[id_key]))
    ineligible.sort(key=lambda e: e["name"] or "")
    return eligible + ineligible


def _evaluate_receiver(db, listing, donor, org, now, minutes_left):
    quantity = float(listing["quantity_kg"] or 0.0)
    storage_type = listing["storage_type"]
    listing_allergens = split_csv(listing["allergens"])

    active_kg, completed_window_kg = reserved_kg(db, org["id"], now)
    outstanding = active_kg + completed_window_kg
    capacity = float(org["weekly_intake_capacity_kg"] or 0.0)
    need = float(org["current_need_kg"] or 0.0)
    remaining_capacity = max(0.0, capacity - outstanding)
    remaining_need = max(0.0, need - active_kg)

    distance = road_km(
        donor["lat"], donor["lng"], org["lat"], org["lng"]
    )
    travel = travel_minutes(distance)
    slack = minutes_left - travel - PICKUP_BUFFER_MIN

    reasons = []
    if storage_type not in split_csv(org["accepted_storage"]):
        reasons.append(f"Does not accept {storage_type} storage.")
    excluded = sorted(
        set(listing_allergens) & set(split_csv(org["allergen_exclusions"]))
    )
    if excluded:
        reasons.append(
            "Listing contains allergen(s) this organisation excludes: "
            + ", ".join(excluded)
            + "."
        )
    if quantity > remaining_capacity:
        reasons.append(
            f"Quantity {quantity:g} kg exceeds remaining weekly intake "
            f"capacity {remaining_capacity:g} kg."
        )
    if remaining_need <= 0:
        reasons.append("No current food need remaining.")
    if slack < 0:
        reasons.append(
            f"Cannot arrive before pickup-by: about {travel:.0f} min travel "
            f"plus {PICKUP_BUFFER_MIN} min buffer exceeds the "
            f"{minutes_left:.0f} min left."
        )

    entry = {
        "org_id": org["id"],
        "name": org["name"],
        "eligible": not reasons,
        "score": None,
        "distance_km": round(distance, 1),
        "travel_min": round(travel, 1),
        "breakdown": [],
        "reasons": reasons,
        "remaining_capacity_kg": round(remaining_capacity, 1),
        "remaining_need_kg": round(remaining_need, 1),
    }
    if reasons:
        return entry

    w = RECEIVER_WEIGHTS
    need_level = int(org["need_level"] or 0)
    raw_distance = w["distance"] * max(0.0, 1 - distance / MAX_DISTANCE_KM)
    raw_quantity = w["quantity"] * quantity_fit(quantity, remaining_need)
    raw_timing = w["timing"] * min(slack, TIMING_CAP_MIN) / TIMING_CAP_MIN
    raw_need = w["need"] * need_level / 3

    breakdown = [
        _breakdown_item(
            "Distance",
            f"{distance:.1f} km (approx. road)",
            raw_distance,
            w["distance"],
        ),
        _breakdown_item(
            "Quantity fit",
            f"{quantity:g} kg vs {remaining_need:g} kg remaining need",
            raw_quantity,
            w["quantity"],
        ),
        _breakdown_item(
            "Timing slack",
            f"{slack:.0f} min slack after travel and buffer",
            raw_timing,
            w["timing"],
        ),
        _breakdown_item(
            "Recipient need",
            f"Need level {need_level} of 3",
            raw_need,
            w["need"],
        ),
    ]
    entry["breakdown"] = breakdown
    entry["score"] = round(
        raw_distance + raw_quantity + raw_timing + raw_need, 1
    )
    return entry


def _evaluate_volunteer(volunteer, listing, donor, now, pickup, minutes_left):
    quantity = float(listing["quantity_kg"] or 0.0)
    capacity = float(volunteer["carrying_capacity_kg"] or 0.0)

    distance = road_km(
        donor["lat"], donor["lng"], volunteer["lat"], volunteer["lng"]
    )
    travel = travel_minutes(distance)
    collection = now + timedelta(minutes=travel)
    collection_end = collection + timedelta(minutes=PICKUP_BUFFER_MIN)
    collection_hhmm = collection.astimezone(IST).strftime("%H:%M")
    now_ist = now.astimezone(IST)
    start_hhmm = now_ist.strftime("%H:%M")
    start_minutes = now_ist.hour * 60 + now_ist.minute
    end_ist = collection_end.astimezone(IST)
    end_hhmm = end_ist.strftime("%H:%M")
    end_minutes = end_ist.hour * 60 + end_ist.minute
    slack = (pickup - collection_end).total_seconds() / 60.0

    reasons = []
    if quantity > capacity:
        reasons.append(
            f"Carrying capacity {capacity:g} kg is below {quantity:g} kg."
        )

    from_text = volunteer["available_from_ist"]
    until_text = volunteer["available_until_ist"]
    from_min = parse_hhmm(from_text)
    until_min = parse_hhmm(until_text)
    if from_min is None or until_min is None:
        reasons.append("Availability window not recorded.")
    elif not (from_min <= start_minutes and end_minutes <= until_min):
        reasons.append(
            "Not available for the planned travel and collection period "
            f"{start_hhmm}-{end_hhmm} IST "
            f"(window {from_text.strip()}-{until_text.strip()} IST)."
        )

    if slack < 0:
        reasons.append(
            f"Cannot reach the donor before pickup-by (about {travel:.0f} min "
            "travel)."
        )

    entry = {
        "volunteer_id": volunteer["id"],
        "name": volunteer["name"],
        "eligible": not reasons,
        "score": None,
        "distance_km": round(distance, 1),
        "travel_min": round(travel, 1),
        "collection_time_ist": collection_hhmm,
        "breakdown": [],
        "reasons": reasons,
    }
    if reasons:
        return entry

    w = VOLUNTEER_WEIGHTS
    capacity_ratio = min(1.0, quantity / capacity) if capacity > 0 else 0.0
    raw_distance = w["distance"] * max(0.0, 1 - distance / MAX_DISTANCE_KM)
    raw_capacity = w["capacity"] * capacity_ratio
    raw_timing = w["timing"] * min(slack, TIMING_CAP_MIN) / TIMING_CAP_MIN

    entry["breakdown"] = [
        _breakdown_item(
            "Distance",
            f"{distance:.1f} km to donor (approx. road)",
            raw_distance,
            w["distance"],
        ),
        _breakdown_item(
            "Capacity use",
            f"{quantity:g} of {capacity:g} kg carrying capacity",
            raw_capacity,
            w["capacity"],
        ),
        _breakdown_item(
            "Timing slack",
            f"{slack:.0f} min before pickup-by at collection",
            raw_timing,
            w["timing"],
        ),
    ]
    entry["score"] = round(raw_distance + raw_capacity + raw_timing, 1)
    return entry


def evaluate_listing(db, listing_id, now):
    """Evaluate every NGO and volunteer against one listing.

    Returns None if the listing does not exist. Otherwise returns a dict with
    listing_id, listing_open, listing_reason, receivers, volunteers and
    assumptions. Receivers and volunteers are empty when the listing is not
    open for matching.
    """
    listing = db.execute(
        "SELECT * FROM listing WHERE id = ?", (listing_id,)
    ).fetchone()
    if listing is None:
        return None

    pickup = parse_utc(listing["pickup_by_utc"])
    minutes_left = (pickup - now).total_seconds() / 60.0

    listing_open = True
    listing_reason = None
    if listing["status"] != "posted":
        listing_open = False
        listing_reason = (
            f"Listing is {listing['status']}, not open for matching."
        )
    elif pickup <= now:
        listing_open = False
        listing_reason = "Pickup-by deadline has passed."

    result = {
        "listing_id": listing_id,
        "listing_open": listing_open,
        "listing_reason": listing_reason,
        "receivers": [],
        "volunteers": [],
        "assumptions": list(ASSUMPTIONS),
    }
    if not listing_open:
        return result

    donor = db.execute(
        "SELECT * FROM organisation WHERE id = ?",
        (listing["donor_org_id"],),
    ).fetchone()

    receivers = []
    for org in db.execute(
        "SELECT * FROM organisation WHERE org_type = 'ngo' ORDER BY id"
    ).fetchall():
        receivers.append(
            _evaluate_receiver(db, listing, donor, org, now, minutes_left)
        )

    volunteers = []
    for volunteer in db.execute(
        "SELECT * FROM volunteer ORDER BY id"
    ).fetchall():
        volunteers.append(
            _evaluate_volunteer(
                volunteer, listing, donor, now, pickup, minutes_left
            )
        )

    result["receivers"] = _rank(receivers, "org_id")
    result["volunteers"] = _rank(volunteers, "volunteer_id")
    return result


def check_receiver_eligibility(db, listing_id, org_id, now):
    """Re-evaluate one receiver against one listing, for the claim check.

    Returns None if the listing or the NGO organisation does not exist.
    Otherwise returns the same entry dict that evaluate_listing produces.
    """
    listing = db.execute(
        "SELECT * FROM listing WHERE id = ?", (listing_id,)
    ).fetchone()
    if listing is None:
        return None
    org = db.execute(
        "SELECT * FROM organisation WHERE id = ? AND org_type = 'ngo'",
        (org_id,),
    ).fetchone()
    if org is None:
        return None
    donor = db.execute(
        "SELECT * FROM organisation WHERE id = ?",
        (listing["donor_org_id"],),
    ).fetchone()
    minutes_left = (parse_utc(listing["pickup_by_utc"]) - now).total_seconds() / 60.0
    return _evaluate_receiver(db, listing, donor, org, now, minutes_left)


@matching_bp.route("/listings/<int:listing_id>/matches")
def listing_matches(listing_id):
    db = get_db()
    result = evaluate_listing(db, listing_id, now_utc())
    if result is None:
        abort(404)
    listing = db.execute(
        "SELECT l.*, o.name AS donor_name FROM listing l "
        "JOIN organisation o ON o.id = l.donor_org_id WHERE l.id = ?",
        (listing_id,),
    ).fetchone()
    return render_template("matches.html", result=result, listing=listing)
