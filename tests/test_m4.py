"""M4 tests: volunteer assignment, collection and delivery."""
import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from surplus_link import claiming, matching, timeutils, volunteering

NOW = datetime(2025, 1, 1, 6, 0, tzinfo=timezone.utc)
EARLY = datetime(2025, 1, 1, 5, 0, tzinfo=timezone.utc)
LATE = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
STAMP = "2025-01-01T06:00:00+00:00"
PICKUP_OPEN = "2025-01-01T12:00:00+00:00"
T_0500 = "2025-01-01T05:00:00+00:00"
T_0600 = "2025-01-01T06:00:00+00:00"
T_0800 = "2025-01-01T08:00:00+00:00"


@pytest.fixture
def fixed_clock():
    timeutils.set_clock(lambda: NOW)
    yield NOW
    timeutils.set_clock(None)


def _connect(app):
    conn = sqlite3.connect(app.config["DATABASE"], timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@pytest.fixture
def db(app):
    conn = _connect(app)
    yield conn
    conn.close()


# ---------- insert helpers ----------

def insert_org(db, name, org_type, lat, lng, zone="Zone A", cap=None, need=None,
               level=None, storage="ambient,chilled,frozen", excl=""):
    cur = db.execute(
        "INSERT INTO organisation (name, org_type, zone, lat, lng, weekly_intake_capacity_kg, "
        "current_need_kg, need_level, accepted_storage, allergen_exclusions, is_synthetic) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        (name, org_type, zone, lat, lng,
         100 if cap is None else cap,
         100 if need is None else need,
         2 if level is None else level,
         storage, excl),
    )
    db.commit()
    return cur.lastrowid


def insert_volunteer(db, name, lat, lng, cap, frm="00:00", until="23:59", zone="Zone A"):
    cur = db.execute(
        "INSERT INTO volunteer (name, zone, lat, lng, carrying_capacity_kg, "
        "available_from_ist, available_until_ist, is_synthetic) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
        (name, zone, lat, lng, cap, frm, until),
    )
    db.commit()
    return cur.lastrowid


def insert_persona(db, display_name, role, organisation_id=None, volunteer_id=None):
    cur = db.execute(
        "INSERT INTO persona (display_name, role, organisation_id, volunteer_id) "
        "VALUES (?, ?, ?, ?)",
        (display_name, role, organisation_id, volunteer_id),
    )
    db.commit()
    return cur.lastrowid


def insert_listing(db, donor_id, qty, pickup_utc_iso, storage="chilled", allergens="",
                   status="posted"):
    cur = db.execute(
        "INSERT INTO listing (donor_org_id, description, quantity_kg, pickup_by_utc, "
        "storage_type, handling_notes, allergens, status, created_at_utc, updated_at_utc) "
        "VALUES (?, 'Test food', ?, ?, ?, '', ?, ?, '2025-01-01T06:00:00+00:00', "
        "'2025-01-01T06:00:00+00:00')",
        (donor_id, qty, pickup_utc_iso, storage, allergens, status),
    )
    db.commit()
    return cur.lastrowid


def insert_claim(db, listing_id, receiver_id, qty, status="active", volunteer_id=None,
                 claimed_at_utc=STAMP):
    assigned = claimed_at_utc if volunteer_id is not None else None
    cur = db.execute(
        "INSERT INTO claim (listing_id, receiver_org_id, volunteer_id, quantity_kg, status, "
        "claimed_at_utc, assigned_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (listing_id, receiver_id, volunteer_id, qty, status, claimed_at_utc, assigned),
    )
    db.commit()
    return cur.lastrowid


# ---------- query helpers ----------

def _claim(db, listing_id):
    return db.execute("SELECT * FROM claim WHERE listing_id = ?", (listing_id,)).fetchone()


def _status(db, listing_id):
    return db.execute("SELECT status FROM listing WHERE id = ?",
                      (listing_id,)).fetchone()["status"]


def _events(db, listing_id, to_status=None):
    if to_status is None:
        return db.execute("SELECT * FROM status_event WHERE listing_id = ? ORDER BY id",
                          (listing_id,)).fetchall()
    return db.execute(
        "SELECT * FROM status_event WHERE listing_id = ? AND to_status = ? ORDER BY id",
        (listing_id, to_status),
    ).fetchall()


def _get(item, key, default=None):
    try:
        return item[key]
    except (KeyError, IndexError, TypeError):
        return default


# ---------- setup helpers ----------

def setup_open_claimed(db, pickup=PICKUP_OPEN, qty=10, n_volunteers=2, vol_cap=50,
                       vol_from="00:00", vol_until="23:59", claim_now=NOW):
    donor = insert_org(db, "Donor", "donor", 12.9000, 77.6000)
    rorg = insert_org(db, "Receiver", "ngo", 12.9000, 77.6000, cap=100, need=100, level=2)
    rp = insert_persona(db, "Receiver Persona", "receiver", organisation_id=rorg)
    listing = insert_listing(db, donor, qty, pickup)
    vols = []
    for i in range(n_volunteers):
        vid = insert_volunteer(db, f"Volunteer {i}", 12.9000, 77.6000, vol_cap,
                               vol_from, vol_until)
        pid = insert_persona(db, f"Volunteer {i} persona", "volunteer", volunteer_id=vid)
        vols.append((vid, pid))
    res = claiming.attempt_claim(db, listing, rp, rorg, claim_now)
    assert res["ok"], res
    return {
        "donor": donor,
        "receiver_org": rorg,
        "receiver_persona": rp,
        "listing": listing,
        "claim": res["claim_id"],
        "volunteers": vols,
    }


def setup_direct_claimed(db, pickup, qty=10, rcap=100):
    """Listing already in 'claimed' status with an active claim (no volunteer)."""
    donor = insert_org(db, "Donor", "donor", 12.9000, 77.6000)
    rorg = insert_org(db, "Receiver", "ngo", 12.9000, 77.6000, cap=rcap, need=100, level=2)
    rp = insert_persona(db, "Receiver Persona", "receiver", organisation_id=rorg)
    listing = insert_listing(db, donor, qty, pickup, status="claimed")
    insert_claim(db, listing, rorg, qty)
    return {"donor": donor, "receiver_org": rorg, "receiver_persona": rp, "listing": listing}


def _new_volunteer(db, name, cap=50, frm="00:00", until="23:59"):
    vid = insert_volunteer(db, name, 12.9000, 77.6000, cap, frm, until)
    pid = insert_persona(db, f"{name} persona", "volunteer", volunteer_id=vid)
    return vid, pid


# ---------- direct function tests ----------

def test_accept_pickup_assigns_volunteer(db):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    res = volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    assert res["ok"] is True
    claim = _claim(db, s["listing"])
    assert claim["volunteer_id"] == vid
    assert claim["assigned_at_utc"] == STAMP
    assert _status(db, s["listing"]) == "claimed"


def test_accept_records_assignment_event(db):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    rows = [r for r in _events(db, s["listing"])
            if r["from_status"] == "claimed" and r["to_status"] == "claimed"]
    assert len(rows) == 1
    assert rows[0]["actor_persona_id"] == vp


def test_competing_acceptances_one_wins(app, db):
    s = setup_open_claimed(db, n_volunteers=6)
    vols = s["volunteers"]
    barrier = threading.Barrier(len(vols))
    results = [None] * len(vols)
    errors = []

    def worker(i):
        conn = None
        try:
            conn = _connect(app)
            barrier.wait()
            vid, vp = vols[i]
            results[i] = volunteering.accept_pickup(conn, s["listing"], vp, vid, NOW)
        except Exception as exc:  # collected and asserted below
            errors.append(exc)
        finally:
            if conn is not None:
                conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(vols))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    winners = [i for i, r in enumerate(results) if r and r["ok"] is True]
    assert len(winners) == 1
    for i, r in enumerate(results):
        if i != winners[0]:
            assert r["code"] == "taken"
    claim = _claim(db, s["listing"])
    assert claim["volunteer_id"] == vols[winners[0]][0]
    assigned = [r for r in _events(db, s["listing"])
                if r["from_status"] == "claimed" and r["to_status"] == "claimed"]
    assert len(assigned) == 1


def test_ineligible_volunteer_rejected(db):
    s = setup_open_claimed(db, qty=12.5, n_volunteers=1, vol_cap=5)
    vid, vp = s["volunteers"][0]
    res = volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    assert res["code"] == "ineligible"
    assert _claim(db, s["listing"])["volunteer_id"] is None


def test_volunteer_window_rejects_outside_hours(db):
    s = setup_open_claimed(db, n_volunteers=1, vol_from="00:00", vol_until="05:00")
    vid, vp = s["volunteers"][0]
    res = volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    assert res["code"] == "ineligible"
    assert _claim(db, s["listing"])["volunteer_id"] is None


def test_already_assigned_accept_is_taken(db):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    assert volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)["ok"] is True
    res = volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    assert res["code"] == "taken"


def test_accept_after_deadline_closed(db):
    s = setup_direct_claimed(db, T_0500)
    vid, vp = _new_volunteer(db, "Late Vol")
    res = volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    assert res["code"] == "closed"
    assert _claim(db, s["listing"])["volunteer_id"] is None


def test_accept_unknown_listing_not_found(db):
    vid, vp = _new_volunteer(db, "Lost Vol")
    res = volunteering.accept_pickup(db, 9999, vp, vid, NOW)
    assert res["code"] == "not_found"


def test_collect_by_assigned_volunteer(db):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    res = volunteering.advance(db, s["listing"], vp, vid, "collected", NOW)
    assert res["ok"] is True
    assert _status(db, s["listing"]) == "collected"
    assert _claim(db, s["listing"])["status"] == "active"
    ev = _events(db, s["listing"], "collected")
    assert len(ev) == 1
    assert ev[0]["from_status"] == "claimed"
    assert ev[0]["actor_persona_id"] == vp


def test_collect_by_other_volunteer_rejected(db):
    s = setup_open_claimed(db)
    vid_a, vp_a = s["volunteers"][0]
    vid_b, vp_b = s["volunteers"][1]
    volunteering.accept_pickup(db, s["listing"], vp_a, vid_a, NOW)
    res = volunteering.advance(db, s["listing"], vp_b, vid_b, "collected", NOW)
    assert res["code"] == "not_assigned"
    assert _status(db, s["listing"]) == "claimed"


def test_collect_before_accept_no_assignment(db):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    res = volunteering.advance(db, s["listing"], vp, vid, "collected", NOW)
    assert res["code"] == "not_assigned"
    assert _claim(db, s["listing"])["volunteer_id"] is None


def test_collect_exactly_at_deadline_rejected(db):
    # Pickup at 06:00 UTC; assignment made at 05:00 UTC; collection attempted at 06:00 UTC.
    s = setup_open_claimed(db, pickup=T_0600, claim_now=EARLY)
    vid, vp = s["volunteers"][0]
    assert volunteering.accept_pickup(db, s["listing"], vp, vid, EARLY)["ok"] is True
    res = volunteering.advance(db, s["listing"], vp, vid, "collected", NOW)
    assert res["code"] in {"after_deadline", "expired"}
    assert _status(db, s["listing"]) != "collected"


def test_collect_after_deadline_rejected(db):
    s = setup_open_claimed(db, pickup=T_0800)
    vid, vp = s["volunteers"][0]
    assert volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)["ok"] is True
    res = volunteering.advance(db, s["listing"], vp, vid, "collected", LATE)
    assert res["code"] in {"after_deadline", "expired"}
    assert _status(db, s["listing"]) != "collected"


def test_deliver_after_deadline_allowed_if_collected_before(db):
    s = setup_open_claimed(db, pickup=T_0800)
    vid, vp = s["volunteers"][0]
    volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    assert volunteering.advance(db, s["listing"], vp, vid, "collected", NOW)["ok"] is True
    assert volunteering.expire_overdue(db, LATE) == 0
    assert _status(db, s["listing"]) == "collected"
    res = volunteering.advance(db, s["listing"], vp, vid, "delivered", LATE)
    assert res["ok"] is True
    assert _status(db, s["listing"]) == "delivered"
    assert _claim(db, s["listing"])["status"] == "completed"


def test_deliver_requires_collected(db):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    res = volunteering.advance(db, s["listing"], vp, vid, "delivered", NOW)
    assert res["code"] == "bad_transition"
    assert _status(db, s["listing"]) == "claimed"
    assert _claim(db, s["listing"])["status"] == "active"


def test_skipped_transition_posted_to_delivered_rejected(db):
    donor = insert_org(db, "Donor", "donor", 12.9000, 77.6000)
    posted = insert_listing(db, donor, 10, PICKUP_OPEN)
    vid, vp = _new_volunteer(db, "Skip Vol")
    res = volunteering.advance(db, posted, vp, vid, "delivered", NOW)
    assert res["code"] == "no_claim"

    s = setup_open_claimed(db)
    avid, avp = s["volunteers"][0]
    volunteering.accept_pickup(db, s["listing"], avp, avid, NOW)
    res = volunteering.advance(db, s["listing"], avp, avid, "delivered", NOW)
    assert res["code"] == "bad_transition"
    assert _status(db, s["listing"]) == "claimed"


def test_double_collect_rejected(db):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    assert volunteering.advance(db, s["listing"], vp, vid, "collected", NOW)["ok"] is True
    res = volunteering.advance(db, s["listing"], vp, vid, "collected", NOW)
    assert res["code"] == "bad_transition"


def test_expire_overdue_releases_reservation(db):
    s = setup_direct_claimed(db, T_0500, qty=15, rcap=20)
    other = insert_listing(db, s["donor"], 10, PICKUP_OPEN)
    res = claiming.attempt_claim(db, other, s["receiver_persona"], s["receiver_org"], NOW)
    assert res["code"] == "ineligible"

    assert volunteering.expire_overdue(db, NOW) == 1
    assert _status(db, s["listing"]) == "expired"
    assert _claim(db, s["listing"])["status"] == "released"
    active_kg, _window_kg = matching.reserved_kg(db, s["receiver_org"], NOW)
    assert active_kg == 0.0

    res = claiming.attempt_claim(db, other, s["receiver_persona"], s["receiver_org"], NOW)
    assert res["ok"] is True


def test_expire_overdue_expires_uncollected_posted(db):
    donor = insert_org(db, "Donor", "donor", 12.9000, 77.6000)
    posted = insert_listing(db, donor, 10, T_0500)
    assert volunteering.expire_overdue(db, NOW) == 1
    assert _status(db, posted) == "expired"
    ev = _events(db, posted, "expired")
    assert len(ev) == 1
    assert ev[0]["from_status"] == "posted"


def test_expired_listing_cannot_be_accepted(db):
    s = setup_direct_claimed(db, T_0500)
    assert volunteering.expire_overdue(db, NOW) == 1
    vid, vp = _new_volunteer(db, "Too Late Vol")
    res = volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    assert res["code"] == "closed"
    assert _claim(db, s["listing"])["volunteer_id"] is None


def test_expire_is_idempotent(db):
    donor = insert_org(db, "Donor", "donor", 12.9000, 77.6000)
    insert_listing(db, donor, 10, T_0500)
    assert volunteering.expire_overdue(db, NOW) == 1
    assert volunteering.expire_overdue(db, NOW) == 0


def test_collected_not_expired_by_expiry(db):
    donor = insert_org(db, "Donor", "donor", 12.9000, 77.6000)
    listing = insert_listing(db, donor, 10, T_0500, status="collected")
    assert volunteering.expire_overdue(db, NOW) == 0
    assert _status(db, listing) == "collected"


def test_rollback_on_status_event_failure_accept(db, monkeypatch):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    before = len(_events(db, s["listing"]))

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(volunteering, "_insert_status_event", boom)
    with pytest.raises(sqlite3.OperationalError):
        volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    db.rollback()
    assert _claim(db, s["listing"])["volunteer_id"] is None
    assert len(_events(db, s["listing"])) == before


def test_rollback_on_claim_update_failure_advance(db, monkeypatch):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(volunteering, "_insert_status_event", boom)
    with pytest.raises(sqlite3.OperationalError):
        volunteering.advance(db, s["listing"], vp, vid, "collected", NOW)
    db.rollback()
    assert _status(db, s["listing"]) == "claimed"
    assert _events(db, s["listing"], "collected") == []


def test_rollback_on_claim_completion_failure(db, monkeypatch):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    assert volunteering.advance(db, s["listing"], vp, vid, "collected", NOW)["ok"] is True

    original = volunteering._insert_status_event

    def boom_on_delivered(*args, **kwargs):
        if "delivered" in args or kwargs.get("to_status") == "delivered":
            raise sqlite3.OperationalError("boom")
        return original(*args, **kwargs)

    monkeypatch.setattr(volunteering, "_insert_status_event", boom_on_delivered)
    with pytest.raises(sqlite3.OperationalError):
        volunteering.advance(db, s["listing"], vp, vid, "delivered", NOW)
    db.rollback()
    assert _status(db, s["listing"]) == "collected"
    assert _claim(db, s["listing"])["status"] == "active"


def test_persistence_after_reconnect(app, db):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    volunteering.advance(db, s["listing"], vp, vid, "collected", NOW)
    db.close()

    conn = _connect(app)
    try:
        assert _status(conn, s["listing"]) == "collected"
        assert _claim(conn, s["listing"])["volunteer_id"] == vid
        assert len(volunteering.listing_timeline(conn, s["listing"])) > 0
    finally:
        conn.close()


def test_timeline_order(db):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    volunteering.advance(db, s["listing"], vp, vid, "collected", NOW)
    volunteering.advance(db, s["listing"], vp, vid, "delivered", NOW)
    to_statuses = [r["to_status"] for r in volunteering.listing_timeline(db, s["listing"])]
    assert to_statuses[-3:] == ["claimed", "collected", "delivered"]


def test_board_shows_open_and_mine(db):
    s = setup_open_claimed(db)
    vid, vp = s["volunteers"][0]
    board = volunteering.volunteer_board(db, vid, NOW)
    open_item = [it for it in board["open"] if _get(it, "listing_id") == s["listing"]]
    assert len(open_item) == 1
    assert open_item[0]["eligible"] is True

    volunteering.accept_pickup(db, s["listing"], vp, vid, NOW)
    board = volunteering.volunteer_board(db, vid, NOW)
    assert not any(_get(it, "listing_id") == s["listing"] for it in board["open"])
    mine = [it for it in board["mine"] if _get(it, "listing_id") == s["listing"]]
    assert len(mine) == 1
    assert _get(mine[0], "status") == "claimed"


def test_board_shows_reason_for_ineligible_open(db):
    s = setup_open_claimed(db, qty=12.5, n_volunteers=1, vol_cap=5)
    vid, _vp = s["volunteers"][0]
    board = volunteering.volunteer_board(db, vid, NOW)
    items = [it for it in board["open"] if _get(it, "listing_id") == s["listing"]]
    assert len(items) == 1
    assert items[0]["eligible"] is False
    reasons = _get(items[0], "reasons") or []
    assert reasons
    assert any("Carrying capacity" in r for r in reasons)


# ---------- route tests ----------

def _as(client, persona_id):
    client.get(f"/role/{persona_id}")


def test_volunteer_accepts_via_route(db, client, fixed_clock):
    s = setup_open_claimed(db, n_volunteers=1)
    vid, vp = s["volunteers"][0]
    _as(client, vp)
    resp = client.post(f"/listings/{s['listing']}/accept")
    assert resp.status_code == 303
    assert "vol=accepted" in resp.headers["Location"]
    page = client.get(f"/listings/{s['listing']}")
    body = page.get_data(as_text=True)
    assert "Volunteer:" in body
    assert f"Volunteer 0" in body


def test_receiver_cannot_accept_403(db, client, fixed_clock):
    s = setup_open_claimed(db, n_volunteers=1)
    _as(client, s["receiver_persona"])
    resp = client.post(f"/listings/{s['listing']}/accept")
    assert resp.status_code == 403
    assert _claim(db, s["listing"])["volunteer_id"] is None


def test_donor_cannot_collect_403(db, client, fixed_clock):
    s = setup_open_claimed(db, n_volunteers=1)
    dp = insert_persona(db, "Donor Persona", "donor", organisation_id=s["donor"])
    _as(client, dp)
    resp = client.post(f"/listings/{s['listing']}/collect")
    assert resp.status_code == 403


def test_anonymous_accept_redirected(db, client, fixed_clock):
    s = setup_open_claimed(db, n_volunteers=1)
    resp = client.post(f"/listings/{s['listing']}/accept")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_board_requires_volunteer(db, client, fixed_clock):
    s = setup_open_claimed(db, n_volunteers=1)
    vid, vp = s["volunteers"][0]
    _as(client, s["receiver_persona"])
    assert client.get("/volunteer/board").status_code == 403
    _as(client, vp)
    resp = client.get("/volunteer/board")
    assert resp.status_code == 200
    assert "Volunteer pickup board" in resp.get_data(as_text=True)


def test_route_collect_then_deliver(db, client, fixed_clock):
    s = setup_open_claimed(db, n_volunteers=1)
    vid, vp = s["volunteers"][0]
    _as(client, vp)
    r1 = client.post(f"/listings/{s['listing']}/accept")
    assert r1.status_code == 303 and "vol=accepted" in r1.headers["Location"]
    r2 = client.post(f"/listings/{s['listing']}/collect")
    assert r2.status_code == 303 and "vol=collected" in r2.headers["Location"]
    r3 = client.post(f"/listings/{s['listing']}/deliver")
    assert r3.status_code == 303 and "vol=delivered" in r3.headers["Location"]
    assert _status(db, s["listing"]) == "delivered"


def test_timeline_shown_on_listing_page(db, client, fixed_clock):
    s = setup_open_claimed(db, n_volunteers=1)
    _as(client, s["receiver_persona"])
    body = client.get(f"/listings/{s['listing']}").get_data(as_text=True)
    assert 'class="timeline"' in body
    assert 'data-refresh-seconds="12"' in body


def test_confirmation_text_updated(db, client, fixed_clock):
    s = setup_open_claimed(db, n_volunteers=1)
    _as(client, s["receiver_persona"])
    body = client.get(f"/listings/{s['listing']}?claimed=1").get_data(as_text=True)
    assert "Volunteers can now accept the pickup from the volunteer board." in body
    assert "Collection coordination comes in a later milestone." not in body


def test_stylesheet_and_script_served(client):
    css = client.get("/static/style.css")
    assert css.status_code == 200
    assert "@media (max-width: 600px)" in css.get_data(as_text=True)
    js = client.get("/static/refresh.js")
    assert js.status_code == 200
    assert "X-Refresh" in js.get_data(as_text=True)


def test_board_nav_link_for_volunteer(db, client, fixed_clock):
    s = setup_open_claimed(db, n_volunteers=1)
    vid, vp = s["volunteers"][0]
    _as(client, vp)
    assert "Volunteer board" in client.get("/").get_data(as_text=True)
    _as(client, s["receiver_persona"])
    assert "/volunteer/board" not in client.get("/").get_data(as_text=True)
