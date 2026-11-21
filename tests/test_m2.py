"""M2 tests: explainable matching, ranking, volunteer suitability, expiry and recalculation.

All data is synthetic and inserted directly so expected values are controlled.
Travel-time assumptions (road factor 1.3, 20 km/h average) are checked against
the ASSUMPTIONS list rather than re-derived here.
"""

import re
from datetime import datetime, timezone

import pytest

from surplus_link.db import get_db, seed_db
from surplus_link.matching import (
    ASSUMPTIONS,
    ROAD_FACTOR,
    evaluate_listing,
    haversine_km,
    quantity_fit,
    reserved_kg,
    road_km,
    split_csv,
    travel_minutes,
)
from surplus_link.timeutils import set_clock
from werkzeug.datastructures import MultiDict

NOW = datetime(2025, 1, 1, 6, 0, tzinfo=timezone.utc)  # 11:30 IST
NOW_ISO = "2025-01-01T06:00:00+00:00"
OLD_ISO = "2024-12-22T06:00:00+00:00"  # 10 days before NOW
OLD30_ISO = "2024-12-02T06:00:00+00:00"  # 30 days before NOW
PICKUP = "2025-01-01T12:00:00+00:00"  # 6.5 h after NOW
PAST_PICKUP = "2025-01-01T05:00:00+00:00"
DONOR_LAT, DONOR_LNG = 12.9000, 77.6000
FAR_LAT = DONOR_LAT + 0.1  # about 11.1 km straight line
VERY_FAR_LAT = DONOR_LAT + 1.0  # about 111 km straight line


# ---- fixtures and helpers ----


@pytest.fixture
def fixed_clock():
    set_clock(lambda: NOW)
    yield
    set_clock(None)


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


def insert_org(db, name, org_type, lat, lng, cap=None, need=None, level=None,
               storage="ambient,chilled,frozen", excl=""):
    cur = db.execute(
        "INSERT INTO organisation (name, org_type, zone, lat, lng, weekly_intake_capacity_kg,"
        " current_need_kg, need_level, accepted_storage, allergen_exclusions)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (name, org_type, "Zone A", lat, lng, cap, need, level, storage, excl),
    )
    db.commit()
    return cur.lastrowid


def insert_volunteer(db, name, lat, lng, cap, frm="08:00", until="20:00"):
    cur = db.execute(
        "INSERT INTO volunteer (name, zone, lat, lng, carrying_capacity_kg,"
        " available_from_ist, available_until_ist) VALUES (?,?,?,?,?,?,?)",
        (name, "Zone A", lat, lng, cap, frm, until),
    )
    db.commit()
    return cur.lastrowid


def insert_listing(db, donor_id, qty, pickup_utc_iso, storage="chilled", allergens="",
                   status="posted"):
    cur = db.execute(
        "INSERT INTO listing (donor_org_id, description, quantity_kg, pickup_by_utc,"
        " storage_type, handling_notes, allergens, status, created_at_utc, updated_at_utc)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (donor_id, "Test food", qty, pickup_utc_iso, storage, "", allergens, status,
         NOW_ISO, NOW_ISO),
    )
    db.commit()
    return cur.lastrowid


def insert_claim(db, listing_id, receiver_id, qty, status="active", claimed_at_utc=NOW_ISO):
    cur = db.execute(
        "INSERT INTO claim (listing_id, receiver_org_id, volunteer_id, quantity_kg, status,"
        " claimed_at_utc) VALUES (?,?,NULL,?,?,?)",
        (listing_id, receiver_id, qty, status, claimed_at_utc),
    )
    db.commit()
    return cur.lastrowid


def donor_and_ngos(db):
    """Insert a synthetic donor at the standard donor coordinates and return its id."""
    return insert_org(db, "Test Donor", "donor", DONOR_LAT, DONOR_LNG)


def good_ngo(db, name="NGO Test", lat=DONOR_LAT, lng=DONOR_LNG, cap=500, need=50, level=2,
             storage="ambient,chilled,frozen", excl=""):
    return insert_org(db, name, "ngo", lat, lng, cap=cap, need=need, level=level,
                      storage=storage, excl=excl)


def open_listing(db, donor_id, qty=10, storage="chilled", allergens="", pickup=PICKUP,
                 status="posted"):
    return insert_listing(db, donor_id, qty, pickup, storage, allergens, status)


def _find(items, key, value):
    return next(item for item in items if item[key] == value)


def _receiver(result, org_id):
    return _find(result["receivers"], "org_id", org_id)


def _volunteer(result, volunteer_id):
    return _find(result["volunteers"], "volunteer_id", volunteer_id)


def _factor(item, name):
    return next(b for b in item["breakdown"] if b["factor"] == name)


def _has(reasons, text):
    return any(text in r for r in reasons)


def _valid_form():
    return {
        "description": "Dal and rice",
        "quantity_kg": "12.5",
        "pickup_by": "2025-01-01T15:00",
        "storage_type": "chilled",
        "handling_notes": "Keep covered",
        "allergens": ["dairy"],
    }


# ---- pure helpers ----


def test_haversine_zero_for_same_point():
    assert haversine_km(12.9, 77.6, 12.9, 77.6) == 0


def test_road_km_applies_factor():
    straight = haversine_km(DONOR_LAT, DONOR_LNG, 12.95, 77.65)
    assert road_km(DONOR_LAT, DONOR_LNG, 12.95, 77.65) == pytest.approx(straight * ROAD_FACTOR)


def test_travel_minutes():
    assert travel_minutes(20) == pytest.approx(60.0)


@pytest.mark.parametrize(
    "qty, need, expected",
    [(10, 10, 1.0), (5, 10, 0.5), (0, 10, 0.0), (10, 0, 0.0)],
)
def test_quantity_fit(qty, need, expected):
    assert quantity_fit(qty, need) == expected


def test_split_csv():
    assert split_csv(" nuts, dairy ,,") == ["nuts", "dairy"]
    assert split_csv(None) == []


# ---- recalculation and capacity ----


def test_reserved_kg_counts_active_and_completed_in_window(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db)
    cases = [
        ("active", 10, NOW_ISO),
        ("completed", 5, NOW_ISO),
        ("released", 7, NOW_ISO),
        ("active", 4, OLD_ISO),
    ]
    for status, qty, claimed_at in cases:
        listing = open_listing(db, donor, qty=qty, status="claimed")
        insert_claim(db, listing, rec, qty, status=status, claimed_at_utc=claimed_at)
    # Policy: all active claims count regardless of age (10 + 4 = 14 kg); completed only within 7 days (5 kg); released never.
    active_kg, completed_window_kg = reserved_kg(db, rec, NOW)
    assert active_kg == 14.0
    assert completed_window_kg == 5.0


def test_old_active_claim_still_reserves_capacity(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, cap=20)
    old = open_listing(db, donor, qty=15, status="claimed")
    insert_claim(db, old, rec, 15, status="active", claimed_at_utc=OLD30_ISO)
    target = open_listing(db, donor, qty=10)
    item = _receiver(evaluate_listing(db, target, NOW), rec)
    assert not item["eligible"]
    assert _has(item["reasons"], "exceeds remaining weekly intake capacity 5 kg")


def test_completed_claim_outside_window_frees_capacity(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, cap=20)
    old = open_listing(db, donor, qty=15, status="claimed")
    insert_claim(db, old, rec, 15, status="completed", claimed_at_utc=OLD30_ISO)
    target = open_listing(db, donor, qty=10)
    assert _receiver(evaluate_listing(db, target, NOW), rec)["eligible"]


def test_each_claim_counted_once(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, cap=30)
    first = open_listing(db, donor, qty=10, status="claimed")
    insert_claim(db, first, rec, 10, status="active", claimed_at_utc=NOW_ISO)
    second = open_listing(db, donor, qty=10, status="claimed")
    insert_claim(db, second, rec, 10, status="completed", claimed_at_utc=NOW_ISO)
    assert reserved_kg(db, rec, NOW) == (10.0, 10.0)
    target = open_listing(db, donor, qty=11)
    item = _receiver(evaluate_listing(db, target, NOW), rec)
    assert not item["eligible"]
    assert _has(item["reasons"], "exceeds remaining weekly intake capacity 10 kg.")


def test_released_claim_frees_capacity(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, cap=20)
    other = open_listing(db, donor, qty=15, status="claimed")
    claim = insert_claim(db, other, rec, 15)
    target = open_listing(db, donor, qty=10)

    before = _receiver(evaluate_listing(db, target, NOW), rec)
    assert not before["eligible"]
    assert _has(before["reasons"], "exceeds remaining weekly intake capacity")

    db.execute("UPDATE claim SET status='released' WHERE id=?", (claim,))
    db.commit()
    after = _receiver(evaluate_listing(db, target, NOW), rec)
    assert after["eligible"]


def test_result_reflects_current_need_not_snapshot(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, need=50)
    listing = open_listing(db, donor)
    assert _receiver(evaluate_listing(db, listing, NOW), rec)["eligible"]

    db.execute("UPDATE organisation SET current_need_kg=0 WHERE id=?", (rec,))
    db.commit()
    result = _receiver(evaluate_listing(db, listing, NOW), rec)
    assert _has(result["reasons"], "No current food need remaining.")


# ---- hard filters (receivers) ----


def test_storage_filter(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, storage="ambient")
    listing = open_listing(db, donor, storage="chilled")
    item = _receiver(evaluate_listing(db, listing, NOW), rec)
    assert not item["eligible"]
    assert "Does not accept chilled storage." in item["reasons"]


def test_allergen_filter(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, excl="nuts")
    listing = open_listing(db, donor, allergens="dairy,nuts")
    item = _receiver(evaluate_listing(db, listing, NOW), rec)
    assert not item["eligible"]
    assert _has(item["reasons"], "nuts")


def test_capacity_filter(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, cap=5)
    listing = open_listing(db, donor, qty=10)
    item = _receiver(evaluate_listing(db, listing, NOW), rec)
    assert not item["eligible"]
    assert _has(item["reasons"], "exceeds remaining weekly intake capacity 5 kg")


def test_multiple_reasons_all_collected(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, storage="ambient", excl="nuts", need=0)
    listing = open_listing(db, donor, storage="chilled", allergens="nuts")
    item = _receiver(evaluate_listing(db, listing, NOW), rec)
    assert len(item["reasons"]) == 3


def test_timing_filter(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, lat=VERY_FAR_LAT)
    listing = open_listing(db, donor, pickup="2025-01-01T06:30:00+00:00")
    item = _receiver(evaluate_listing(db, listing, NOW), rec)
    assert not item["eligible"]
    assert any(r.startswith("Cannot arrive before pickup-by") for r in item["reasons"])


# ---- receiver scoring and ranking ----


def test_score_breakdown_sums_to_score(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db)
    listing = open_listing(db, donor)
    item = _receiver(evaluate_listing(db, listing, NOW), rec)
    assert item["eligible"]
    assert sum(b["points"] for b in item["breakdown"]) == pytest.approx(item["score"], abs=0.11)
    assert [b["factor"] for b in item["breakdown"]] == [
        "Distance", "Quantity fit", "Timing slack", "Recipient need",
    ]
    assert all(b["points"] <= b["max_points"] for b in item["breakdown"])
    assert sum(b["max_points"] for b in item["breakdown"]) == pytest.approx(100)


def test_closer_receiver_scores_higher_distance_points(db):
    donor = donor_and_ngos(db)
    close = good_ngo(db, name="Close")
    far = good_ngo(db, name="Far", lat=DONOR_LAT + 0.05)
    listing = open_listing(db, donor)
    result = evaluate_listing(db, listing, NOW)
    assert _factor(_receiver(result, close), "Distance")["points"] > \
        _factor(_receiver(result, far), "Distance")["points"]
    assert result["receivers"][0]["org_id"] == close


def test_ranking_eligible_before_ineligible(db):
    donor = donor_and_ngos(db)
    ok = good_ngo(db, name="Ok")
    bad = good_ngo(db, name="Bad", storage="ambient")
    listing = open_listing(db, donor, storage="chilled")
    result = evaluate_listing(db, listing, NOW)
    assert result["receivers"][0]["org_id"] == ok
    ineligible = _receiver(result, bad)
    assert ineligible["score"] is None
    assert ineligible["breakdown"] == []


def test_higher_need_level_scores_higher(db):
    donor = donor_and_ngos(db)
    low = good_ngo(db, name="Low", level=1)
    high = good_ngo(db, name="High", level=3)
    listing = open_listing(db, donor)
    result = evaluate_listing(db, listing, NOW)
    gap = _receiver(result, high)["score"] - _receiver(result, low)["score"]
    assert gap == pytest.approx(20 * (2 / 3), abs=0.2)


def test_quantity_fit_scores_exact_match_higher(db):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, need=10)
    exact = open_listing(db, donor, qty=10)
    partial = open_listing(db, donor, qty=3)
    points_exact = _factor(_receiver(evaluate_listing(db, exact, NOW), rec), "Quantity fit")["points"]
    points_partial = _factor(_receiver(evaluate_listing(db, partial, NOW), rec), "Quantity fit")["points"]
    assert points_exact > points_partial


# ---- volunteers ----


def test_volunteer_capacity_filter(db):
    donor = donor_and_ngos(db)
    vol = insert_volunteer(db, "Small", DONOR_LAT, DONOR_LNG, 5)
    listing = open_listing(db, donor, qty=10)
    item = _volunteer(evaluate_listing(db, listing, NOW), vol)
    assert not item["eligible"]
    assert "Carrying capacity 5 kg is below 10 kg." in item["reasons"]


def test_volunteer_availability_filter(db):
    donor = donor_and_ngos(db)
    vol = insert_volunteer(db, "Early", DONOR_LAT, DONOR_LNG, 50, frm="08:00", until="09:00")
    listing = open_listing(db, donor)
    item = _volunteer(evaluate_listing(db, listing, NOW), vol)
    assert not item["eligible"]
    assert _has(item["reasons"], "Not available for the planned travel and collection period")


def test_volunteer_eligible_and_ranked_by_distance(db):
    donor = donor_and_ngos(db)
    near = insert_volunteer(db, "Near", DONOR_LAT, DONOR_LNG, 50)
    far = insert_volunteer(db, "Far", FAR_LAT, DONOR_LNG, 50)
    listing = open_listing(db, donor)
    result = evaluate_listing(db, listing, NOW)
    assert result["volunteers"][0]["volunteer_id"] == near
    for vid in (near, far):
        item = _volunteer(result, vid)
        assert item["eligible"]
        assert re.search(r"\d{2}:\d{2}", item["collection_time_ist"])


def test_volunteer_breakdown_factors(db):
    donor = donor_and_ngos(db)
    vol = insert_volunteer(db, "Vol", DONOR_LAT, DONOR_LNG, 50)
    listing = open_listing(db, donor)
    item = _volunteer(evaluate_listing(db, listing, NOW), vol)
    assert [b["factor"] for b in item["breakdown"]] == ["Distance", "Capacity use", "Timing slack"]
    assert sum(b["points"] for b in item["breakdown"]) == pytest.approx(item["score"], abs=0.11)


def test_volunteer_buffer_applied(db):
    donor = donor_and_ngos(db)
    vol = insert_volunteer(db, "Buffer", DONOR_LAT, DONOR_LNG, 50)
    # Zero travel: slack without buffer is 10 min (>= 0), with the 15-min buffer it is -5 min.
    listing = open_listing(db, donor, pickup="2025-01-01T06:10:00+00:00")
    item = _volunteer(evaluate_listing(db, listing, NOW), vol)
    assert not item["eligible"]
    assert _has(item["reasons"], "Cannot reach the donor before pickup-by")


def test_volunteer_period_must_be_in_window(db):
    donor = donor_and_ngos(db)
    # About 4.3 km road (about 13 min travel): period 11:30-11:58 IST runs past the 11:45 window end.
    vol = insert_volunteer(db, "Late", DONOR_LAT + 0.03, DONOR_LNG, 50, frm="08:00", until="11:45")
    listing = open_listing(db, donor)
    item = _volunteer(evaluate_listing(db, listing, NOW), vol)
    assert not item["eligible"]
    assert _has(item["reasons"], "Not available for the planned travel and collection period")


# ---- expiry and listing state ----


def test_expired_deadline_returns_closed(db):
    donor = donor_and_ngos(db)
    good_ngo(db)
    insert_volunteer(db, "Vol", DONOR_LAT, DONOR_LNG, 50)
    listing = open_listing(db, donor, pickup=PAST_PICKUP)
    result = evaluate_listing(db, listing, NOW)
    assert result["listing_open"] is False
    assert result["listing_reason"] == "Pickup-by deadline has passed."
    assert result["receivers"] == []
    assert result["volunteers"] == []


def test_pickup_exactly_now_is_closed(db):
    donor = donor_and_ngos(db)
    listing = open_listing(db, donor, pickup=NOW_ISO)
    assert evaluate_listing(db, listing, NOW)["listing_open"] is False


def test_non_posted_listing_closed(db):
    donor = donor_and_ngos(db)
    listing = open_listing(db, donor, status="claimed")
    result = evaluate_listing(db, listing, NOW)
    assert result["listing_open"] is False
    assert "Listing is claimed" in result["listing_reason"]


def test_unknown_listing_returns_none(db):
    assert evaluate_listing(db, 9999, NOW) is None


# ---- no suitable matches ----


def test_no_suitable_receivers_all_ineligible(db):
    donor = donor_and_ngos(db)
    good_ngo(db, name="A", storage="ambient")
    good_ngo(db, name="B", storage="ambient")
    listing = open_listing(db, donor, storage="chilled")
    result = evaluate_listing(db, listing, NOW)
    assert result["listing_open"] is True
    assert len(result["receivers"]) == 2
    assert not any(r["eligible"] for r in result["receivers"])


# ---- routes ----


def test_matches_page_renders_ranked_and_assumptions(fixed_clock, db, client):
    seed_db()
    donor_id = db.execute(
        "SELECT id FROM persona WHERE role='donor' ORDER BY id LIMIT 1"
    ).fetchone()[0]
    client.get(f"/role/{donor_id}")
    response = client.post("/donate", data=_valid_form())
    assert response.status_code == 303
    listing_id = db.execute("SELECT MAX(id) FROM listing").fetchone()[0]
    page = client.get(f"/listings/{listing_id}/matches")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "Ranked matches" in body
    assert "Assumptions" in body
    assert "approx. road" in body


def test_matches_page_404_unknown(fixed_clock, client):
    assert client.get("/listings/9999/matches").status_code == 404


def test_matches_page_shows_expired_message(fixed_clock, db, client):
    donor = donor_and_ngos(db)
    listing = open_listing(db, donor, pickup=PAST_PICKUP)
    page = client.get(f"/listings/{listing}/matches")
    assert page.status_code == 200
    assert "Pickup-by deadline has passed." in page.get_data(as_text=True)


def test_matches_page_shows_reasons(fixed_clock, db, client):
    donor = donor_and_ngos(db)
    good_ngo(db, storage="ambient")
    listing = open_listing(db, donor, storage="chilled")
    page = client.get(f"/listings/{listing}/matches")
    assert page.status_code == 200
    assert "Does not accept" in page.get_data(as_text=True)


def test_assumptions_constants_documented():
    assert isinstance(ASSUMPTIONS, list)
    assert ASSUMPTIONS
    assert all(isinstance(s, str) and s.strip() for s in ASSUMPTIONS)
    assert any("1.3" in s for s in ASSUMPTIONS)
    assert any("20 km/h" in s for s in ASSUMPTIONS)


def test_recalculation_on_each_request(fixed_clock, db, client):
    donor = donor_and_ngos(db)
    rec = good_ngo(db, need=50)
    listing = open_listing(db, donor)
    first = client.get(f"/listings/{listing}/matches")
    assert first.status_code == 200

    db.execute("UPDATE organisation SET current_need_kg=0 WHERE id=?", (rec,))
    db.commit()
    second = client.get(f"/listings/{listing}/matches")
    assert "No current food need remaining." in second.get_data(as_text=True)
