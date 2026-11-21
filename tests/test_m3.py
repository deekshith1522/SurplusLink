"""M3 tests: receiver claiming, concurrency, capacity, rollback and routes."""

import html
import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from surplus_link import claiming
from surplus_link.claiming import attempt_claim, get_claimant, MESSAGES
from surplus_link.matching import check_receiver_eligibility, reserved_kg
from surplus_link.timeutils import set_clock
from surplus_link.db import get_db


NOW = datetime(2025, 1, 1, 6, 0, tzinfo=timezone.utc)
NOW_ISO = "2025-01-01T06:00:00+00:00"
PICKUP = "2025-01-01T12:00:00+00:00"
DONOR_LAT, DONOR_LNG = 12.9000, 77.6000


@pytest.fixture
def fixed_clock():
    set_clock(lambda: NOW)
    yield NOW
    set_clock(None)


def open_conn(app):
    conn = sqlite3.connect(app.config["DATABASE"], timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------- helpers

def insert_org(db, name, org_type, lat, lng, cap=None, need=None, level=None,
               storage="ambient,chilled,frozen", excl=""):
    if org_type == "ngo":
        cur = db.execute(
            "INSERT INTO organisation (name, org_type, zone, lat, lng, weekly_intake_capacity_kg, "
            "current_need_kg, need_level, accepted_storage, allergen_exclusions) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, org_type, "Zone A", lat, lng, cap, need, level, storage, excl),
        )
    else:
        cur = db.execute(
            "INSERT INTO organisation (name, org_type, zone, lat, lng) VALUES (?,?,?,?,?)",
            (name, org_type, "Zone A", lat, lng),
        )
    db.commit()
    return cur.lastrowid


def insert_volunteer(db, name, lat, lng):
    cur = db.execute(
        "INSERT INTO volunteer (name, zone, lat, lng, carrying_capacity_kg, "
        "available_from_ist, available_until_ist) VALUES (?,?,?,?,?,?,?)",
        (name, "Zone A", lat, lng, 50, "09:00", "18:00"),
    )
    db.commit()
    return cur.lastrowid


def insert_persona(db, display_name, role, organisation_id=None, volunteer_id=None):
    cur = db.execute(
        "INSERT INTO persona (display_name, role, organisation_id, volunteer_id) "
        "VALUES (?,?,?,?)",
        (display_name, role, organisation_id, volunteer_id),
    )
    db.commit()
    return cur.lastrowid


def insert_listing(db, donor_id, qty, pickup_utc_iso, storage="chilled", allergens="",
                   status="posted"):
    cur = db.execute(
        "INSERT INTO listing (donor_org_id, description, quantity_kg, pickup_by_utc, storage_type, "
        "allergens, status, created_at_utc, updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?)",
        (donor_id, "Synthetic test listing", qty, pickup_utc_iso, storage, allergens, status,
         NOW_ISO, NOW_ISO),
    )
    db.commit()
    return cur.lastrowid


def insert_claim(db, listing_id, receiver_id, qty, status="active",
                 claimed_at_utc="2025-01-01T06:00:00+00:00"):
    cur = db.execute(
        "INSERT INTO claim (listing_id, receiver_org_id, quantity_kg, status, claimed_at_utc) "
        "VALUES (?,?,?,?,?)",
        (listing_id, receiver_id, qty, status, claimed_at_utc),
    )
    db.commit()
    return cur.lastrowid


def make_donor(db):
    return insert_org(db, "Donor Kitchen", "donor", DONOR_LAT, DONOR_LNG)


def make_receiver(db, name, cap=100):
    org = insert_org(db, name, "ngo", DONOR_LAT, DONOR_LNG, cap, 100, 2)
    persona = insert_persona(db, name + " persona", "receiver", organisation_id=org)
    return org, persona


def listing_status(conn, listing_id):
    return conn.execute("SELECT status FROM listing WHERE id=?", (listing_id,)).fetchone()["status"]


def claims_for(conn, listing_id):
    return conn.execute("SELECT * FROM claim WHERE listing_id=?", (listing_id,)).fetchall()


def events_for(conn, listing_id):
    return conn.execute("SELECT * FROM status_event WHERE listing_id=?", (listing_id,)).fetchall()


def run_concurrent(app, jobs):
    """jobs: list of (listing_id, persona_id, org_id). Returns (results, errors)."""
    n = len(jobs)
    barrier = threading.Barrier(n)
    results = [None] * n
    errors = []

    def worker(i, listing_id, persona_id, org_id):
        conn = None
        try:
            conn = open_conn(app)
            barrier.wait(timeout=10)
            results[i] = attempt_claim(conn, listing_id, persona_id, org_id, NOW)
        except Exception as exc:  # store, do not swallow silently
            errors.append(repr(exc))
        finally:
            if conn is not None:
                conn.close()

    threads = [
        threading.Thread(target=worker, args=(i,) + tuple(job))
        for i, job in enumerate(jobs)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results, errors


def seed_routes(app):
    with app.app_context():
        db = get_db()
        donor = make_donor(db)
        rec_org, rec_p = make_receiver(db, "Receiver NGO", 100)
        other_org, other_p = make_receiver(db, "Other NGO", 100)
        donor_p = insert_persona(db, "Donor P", "donor", organisation_id=donor)
        vol_row = insert_volunteer(db, "Volunteer NGO Driver", DONOR_LAT, DONOR_LNG)
        vol_p = insert_persona(db, "Volunteer P", "volunteer", volunteer_id=vol_row)
        listing = insert_listing(db, donor, 15, PICKUP)
    return {
        "donor_org": donor, "rec_org": rec_org, "rec_p": rec_p,
        "other_org": other_org, "other_p": other_p,
        "donor_p": donor_p, "vol_p": vol_p, "listing": listing,
    }


# ------------------------------------------------- direct claim behaviour

@pytest.mark.usefixtures("fixed_clock")
def test_successful_claim_updates_listing_and_creates_claim(app):
    db = open_conn(app)
    donor = make_donor(db)
    org, persona = make_receiver(db, "Recv A")
    lid = insert_listing(db, donor, 15, PICKUP)

    result = attempt_claim(db, lid, persona, org, NOW)

    assert result["ok"] is True
    assert result["code"] is None
    assert result["claim_id"] is not None
    assert listing_status(db, lid) == "claimed"
    claims = claims_for(db, lid)
    assert len(claims) == 1
    assert claims[0]["status"] == "active"
    assert claims[0]["quantity_kg"] == 15
    assert claims[0]["receiver_org_id"] == org
    assert claims[0]["claimed_at_utc"] == NOW_ISO
    db.close()


@pytest.mark.usefixtures("fixed_clock")
def test_successful_claim_records_status_event(app):
    db = open_conn(app)
    donor = make_donor(db)
    org, persona = make_receiver(db, "Recv A")
    lid = insert_listing(db, donor, 15, PICKUP)

    attempt_claim(db, lid, persona, org, NOW)

    events = events_for(db, lid)
    assert len(events) == 1
    assert events[0]["from_status"] == "posted"
    assert events[0]["to_status"] == "claimed"
    assert events[0]["actor_persona_id"] == persona
    db.close()


@pytest.mark.usefixtures("fixed_clock")
def test_second_claim_by_other_receiver_is_taken(app):
    db = open_conn(app)
    donor = make_donor(db)
    org_a, p_a = make_receiver(db, "Recv A")
    org_b, p_b = make_receiver(db, "Recv B")
    lid = insert_listing(db, donor, 15, PICKUP)

    first = attempt_claim(db, lid, p_a, org_a, NOW)
    second = attempt_claim(db, lid, p_b, org_b, NOW)

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["code"] == "taken"
    assert "taken" in MESSAGES and MESSAGES["taken"]
    assert len(claims_for(db, lid)) == 1
    db.close()


@pytest.mark.usefixtures("fixed_clock")
def test_same_receiver_second_claim_is_duplicate(app):
    db = open_conn(app)
    donor = make_donor(db)
    org, persona = make_receiver(db, "Recv A")
    lid = insert_listing(db, donor, 15, PICKUP)

    first = attempt_claim(db, lid, persona, org, NOW)
    again = attempt_claim(db, lid, persona, org, NOW)

    assert first["ok"] is True
    assert again["ok"] is False
    assert again["code"] == "duplicate"
    assert len(claims_for(db, lid)) == 1
    assert listing_status(db, lid) == "claimed"
    db.close()


@pytest.mark.usefixtures("fixed_clock")
def test_concurrent_claims_on_one_listing(app):
    setup = open_conn(app)
    donor = make_donor(setup)
    lid = insert_listing(setup, donor, 15, PICKUP)
    receivers = [make_receiver(setup, f"Recv {i}") for i in range(6)]
    jobs = [(lid, persona, org) for org, persona in receivers]

    results, errors = run_concurrent(app, jobs)

    assert errors == []
    assert all(r is not None for r in results)
    oks = [r for r in results if r["ok"]]
    assert len(oks) == 1
    for r in results:
        if not r["ok"]:
            assert r["code"] == "taken"
    assert len(claims_for(setup, lid)) == 1
    assert listing_status(setup, lid) == "claimed"
    events = events_for(setup, lid)
    assert len(events) == 1
    assert events[0]["from_status"] == "posted"
    assert events[0]["to_status"] == "claimed"
    setup.close()


@pytest.mark.usefixtures("fixed_clock")
def test_claim_closed_after_deadline(app):
    db = open_conn(app)
    donor = make_donor(db)
    org, persona = make_receiver(db, "Recv A")
    lid = insert_listing(db, donor, 15, "2025-01-01T05:00:00+00:00")

    result = attempt_claim(db, lid, persona, org, NOW)

    assert result["ok"] is False
    assert result["code"] == "closed"
    assert claims_for(db, lid) == []
    assert listing_status(db, lid) == "posted"
    db.close()


@pytest.mark.usefixtures("fixed_clock")
def test_claim_not_posted_listing_is_closed(app):
    db = open_conn(app)
    donor = make_donor(db)
    org, persona = make_receiver(db, "Recv A")
    lid = insert_listing(db, donor, 15, PICKUP, status="expired")

    result = attempt_claim(db, lid, persona, org, NOW)

    assert result["ok"] is False
    assert result["code"] == "closed"
    assert claims_for(db, lid) == []
    db.close()


@pytest.mark.usefixtures("fixed_clock")
def test_ineligible_receiver_cannot_claim(app):
    db = open_conn(app)
    donor = make_donor(db)
    org = insert_org(db, "Ambient Only", "ngo", DONOR_LAT, DONOR_LNG, 100, 100, 2,
                     storage="ambient")
    persona = insert_persona(db, "Ambient P", "receiver", organisation_id=org)
    lid = insert_listing(db, donor, 15, PICKUP, storage="chilled")

    result = attempt_claim(db, lid, persona, org, NOW)

    assert result["ok"] is False
    assert result["code"] == "ineligible"
    assert claims_for(db, lid) == []
    assert listing_status(db, lid) == "posted"
    db.close()


@pytest.mark.usefixtures("fixed_clock")
def test_unknown_listing_not_found(app):
    db = open_conn(app)
    org, persona = make_receiver(db, "Recv A")

    result = attempt_claim(db, 9999, persona, org, NOW)

    assert result["ok"] is False
    assert result["code"] == "not_found"
    db.close()


@pytest.mark.usefixtures("fixed_clock")
def test_capacity_competition_only_one_listing_fits(app):
    db = open_conn(app)
    donor = make_donor(db)
    org, persona = make_receiver(db, "Small NGO", cap=20)
    l1 = insert_listing(db, donor, 15, PICKUP)
    l2 = insert_listing(db, donor, 15, PICKUP)

    first = attempt_claim(db, l1, persona, org, NOW)
    second = attempt_claim(db, l2, persona, org, NOW)

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["code"] == "ineligible"
    active = db.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(quantity_kg),0) AS kg FROM claim "
        "WHERE receiver_org_id=? AND status='active'", (org,)
    ).fetchone()
    assert active["n"] == 1
    assert active["kg"] == 15
    active_kg, _ = reserved_kg(db, org, NOW)
    assert active_kg == 15
    db.close()


@pytest.mark.usefixtures("fixed_clock")
def test_concurrent_listings_competing_for_capacity(app):
    setup = open_conn(app)
    donor = make_donor(setup)
    org = insert_org(setup, "Small NGO", "ngo", DONOR_LAT, DONOR_LNG, 20, 100, 2)
    p1 = insert_persona(setup, "Small P1", "receiver", organisation_id=org)
    p2 = insert_persona(setup, "Small P2", "receiver", organisation_id=org)
    l1 = insert_listing(setup, donor, 15, PICKUP)
    l2 = insert_listing(setup, donor, 15, PICKUP)

    results, errors = run_concurrent(app, [(l1, p1, org), (l2, p2, org)])

    assert errors == []
    oks = [r for r in results if r["ok"]]
    fails = [r for r in results if not r["ok"]]
    assert len(oks) == 1
    assert len(fails) == 1
    assert fails[0]["code"] == "ineligible"
    total = setup.execute(
        "SELECT COALESCE(SUM(quantity_kg),0) AS kg FROM claim "
        "WHERE receiver_org_id=? AND status='active'", (org,)
    ).fetchone()["kg"]
    assert total == 15
    statuses = {listing_status(setup, l1), listing_status(setup, l2)}
    assert statuses == {"claimed", "posted"}
    setup.close()


@pytest.mark.usefixtures("fixed_clock")
def test_released_claim_frees_capacity(app):
    db = open_conn(app)
    donor = make_donor(db)
    org, persona = make_receiver(db, "Small NGO", cap=20)
    other_listing = insert_listing(db, donor, 15, PICKUP, status="claimed")
    released_claim = insert_claim(db, other_listing, org, 15, status="active")
    lid = insert_listing(db, donor, 10, PICKUP)

    blocked = attempt_claim(db, lid, persona, org, NOW)
    assert blocked["ok"] is False
    assert blocked["code"] == "ineligible"

    db.execute("UPDATE claim SET status='released' WHERE id=?", (released_claim,))
    db.commit()

    entry = check_receiver_eligibility(db, lid, org, NOW)
    assert entry is not None and entry["eligible"] is True

    result = attempt_claim(db, lid, persona, org, NOW)
    assert result["ok"] is True
    assert listing_status(db, lid) == "claimed"
    db.close()


@pytest.mark.usefixtures("fixed_clock")
def test_active_claim_from_old_date_still_reserves(app):
    db = open_conn(app)
    donor = make_donor(db)
    org, persona = make_receiver(db, "Small NGO", cap=20)
    other_listing = insert_listing(db, donor, 15, PICKUP, status="claimed")
    insert_claim(db, other_listing, org, 15, status="active",
                 claimed_at_utc="2024-12-02T06:00:00+00:00")
    lid = insert_listing(db, donor, 10, PICKUP)

    result = attempt_claim(db, lid, persona, org, NOW)

    assert result["ok"] is False
    assert result["code"] == "ineligible"
    assert claims_for(db, lid) == []
    db.close()


@pytest.mark.usefixtures("fixed_clock")
def test_rollback_on_status_event_failure(app, monkeypatch):
    db = open_conn(app)
    donor = make_donor(db)
    org, persona = make_receiver(db, "Recv A")
    lid = insert_listing(db, donor, 15, PICKUP)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(claiming, "_insert_status_event", boom)
    with pytest.raises(sqlite3.OperationalError):
        attempt_claim(db, lid, persona, org, NOW)
    db.close()

    check = open_conn(app)
    assert listing_status(check, lid) == "posted"
    assert claims_for(check, lid) == []
    assert events_for(check, lid) == []
    check.close()


@pytest.mark.usefixtures("fixed_clock")
def test_rollback_on_claim_insert_failure(app, monkeypatch):
    db = open_conn(app)
    donor = make_donor(db)
    org, persona = make_receiver(db, "Recv A")
    lid = insert_listing(db, donor, 15, PICKUP)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(claiming, "_insert_claim", boom)
    with pytest.raises(sqlite3.OperationalError):
        attempt_claim(db, lid, persona, org, NOW)
    db.close()

    check = open_conn(app)
    assert listing_status(check, lid) == "posted"
    assert events_for(check, lid) == []
    check.close()


@pytest.mark.usefixtures("fixed_clock")
def test_claim_persists_after_reconnect(app):
    db = open_conn(app)
    donor = make_donor(db)
    org = insert_org(db, "Persist NGO", "ngo", DONOR_LAT, DONOR_LNG, 100, 100, 2)
    persona = insert_persona(db, "Persist P", "receiver", organisation_id=org)
    lid = insert_listing(db, donor, 15, PICKUP)
    assert attempt_claim(db, lid, persona, org, NOW)["ok"] is True
    db.close()

    fresh = open_conn(app)
    assert listing_status(fresh, lid) == "claimed"
    assert len(claims_for(fresh, lid)) == 1
    assert get_claimant(fresh, lid) == "Persist NGO"
    fresh.close()


@pytest.mark.usefixtures("fixed_clock")
def test_get_claimant_none_when_unclaimed(app):
    db = open_conn(app)
    donor = make_donor(db)
    lid = insert_listing(db, donor, 15, PICKUP)

    assert get_claimant(db, lid) is None
    db.close()


# ------------------------------------------------------------- routes

@pytest.mark.usefixtures("fixed_clock")
def test_receiver_can_claim_via_route(app, client):
    ids = seed_routes(app)
    client.get(f"/role/{ids['rec_p']}")

    resp = client.post(f"/listings/{ids['listing']}/claim")

    assert resp.status_code == 303
    assert "claimed=1" in resp.headers["Location"]
    page = client.get(f"/listings/{ids['listing']}")
    body = page.get_data(as_text=True)
    assert "Claimed by:" in body
    assert "Receiver NGO" in body


@pytest.mark.usefixtures("fixed_clock")
def test_donor_cannot_claim_403(app, client):
    ids = seed_routes(app)
    client.get(f"/role/{ids['donor_p']}")

    resp = client.post(f"/listings/{ids['listing']}/claim")

    assert resp.status_code == 403
    check = open_conn(app)
    assert listing_status(check, ids["listing"]) == "posted"
    check.close()


@pytest.mark.usefixtures("fixed_clock")
def test_volunteer_cannot_claim_403(app, client):
    ids = seed_routes(app)
    client.get(f"/role/{ids['vol_p']}")

    resp = client.post(f"/listings/{ids['listing']}/claim")

    assert resp.status_code == 403


@pytest.mark.usefixtures("fixed_clock")
def test_anonymous_claim_redirected(app, client):
    ids = seed_routes(app)

    resp = client.post(f"/listings/{ids['listing']}/claim")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


@pytest.mark.usefixtures("fixed_clock")
def test_conflict_shows_message(app, client):
    ids = seed_routes(app)
    client.get(f"/role/{ids['rec_p']}")
    assert client.post(f"/listings/{ids['listing']}/claim").status_code == 303

    other = app.test_client()
    other.get(f"/role/{ids['other_p']}")
    resp = other.post(f"/listings/{ids['listing']}/claim")

    assert resp.status_code == 303
    location = resp.headers["Location"]
    assert "claim=taken" in location
    page = other.get(location)
    body = page.get_data(as_text=True)
    assert html.escape(MESSAGES["taken"]) in body
    assert "Claimed by:" in body
    assert "Receiver NGO" in body


@pytest.mark.usefixtures("fixed_clock")
def test_claim_button_shown_only_for_receiver_on_posted(app, client):
    ids = seed_routes(app)
    client.get(f"/role/{ids['rec_p']}")

    before = client.get(f"/listings/{ids['listing']}").get_data(as_text=True)
    assert "Claim for" in before

    client.post(f"/listings/{ids['listing']}/claim")
    after = client.get(f"/listings/{ids['listing']}").get_data(as_text=True)
    assert "Claim for" not in after


@pytest.mark.usefixtures("fixed_clock")
def test_unknown_listing_route_404(app, client):
    ids = seed_routes(app)
    client.get(f"/role/{ids['rec_p']}")

    resp = client.post("/listings/9999/claim")

    assert resp.status_code == 404


@pytest.mark.usefixtures("fixed_clock")
def test_organisation_id_not_read_from_form(app, client):
    ids = seed_routes(app)
    client.get(f"/role/{ids['rec_p']}")

    resp = client.post(
        f"/listings/{ids['listing']}/claim",
        data={"organisation_id": str(ids["other_org"])},
    )

    assert resp.status_code == 303
    check = open_conn(app)
    rows = claims_for(check, ids["listing"])
    assert len(rows) == 1
    assert rows[0]["receiver_org_id"] == ids["rec_org"]
    check.close()
