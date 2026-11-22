"""CP3 tests: public donation board, filters, and receiver-only claiming."""
from datetime import datetime, timezone

import pytest

from surplus_link.board import available_listings
from surplus_link.db import get_db
from surplus_link.timeutils import set_clock

NOW = datetime(2025, 1, 1, 6, 0, tzinfo=timezone.utc)
FUTURE = "2025-01-01T12:00:00+00:00"
PAST = "2025-01-01T05:00:00+00:00"


@pytest.fixture
def fixed_clock():
    set_clock(lambda: NOW)
    yield
    set_clock(None)


def _seed(app, fn):
    with app.app_context():
        db = get_db()
        result = fn(db)
        db.commit()
        return result


def _org(db, name, org_type, zone="Zone A", lat=12.9, lng=77.6, cap=100.0,
         need=100.0, level=2, storage="ambient,chilled,frozen", excl=""):
    cur = db.execute(
        "INSERT INTO organisation (name, org_type, zone, lat, lng, weekly_intake_capacity_kg, "
        "current_need_kg, need_level, accepted_storage, allergen_exclusions) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (name, org_type, zone, lat, lng, cap, need, level, storage, excl),
    )
    return cur.lastrowid


def _listing(db, donor_id, description, qty, storage, status="posted",
             pickup=FUTURE, allergens=""):
    cur = db.execute(
        "INSERT INTO listing (donor_org_id, description, quantity_kg, pickup_by_utc, storage_type, "
        "handling_notes, allergens, status, created_at_utc, updated_at_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (donor_id, description, qty, pickup, storage, "", allergens, status,
         "2025-01-01T06:00:00+00:00", "2025-01-01T06:00:00+00:00"),
    )
    return cur.lastrowid


def _volunteer(db):
    cur = db.execute(
        "INSERT INTO volunteer (name, zone, lat, lng, carrying_capacity_kg, "
        "available_from_ist, available_until_ist) VALUES (?,?,?,?,?,?,?)",
        ("Vol", "Zone A", 12.9, 77.6, 50.0, "00:00", "23:59"),
    )
    return cur.lastrowid


def _persona(db, display_name, role, organisation_id=None, volunteer_id=None):
    cur = db.execute(
        "INSERT INTO persona (display_name, role, organisation_id, volunteer_id) VALUES (?,?,?,?)",
        (display_name, role, organisation_id, volunteer_id),
    )
    return cur.lastrowid


def _avail(app, **kwargs):
    with app.app_context():
        return available_listings(get_db(), NOW, **kwargs)


def _ids(rows):
    return [r["id"] for r in rows]


def _status(app, lid):
    return _seed(app, lambda db: db.execute(
        "SELECT status FROM listing WHERE id = ?", (lid,)).fetchone()[0])


def _as(client, persona_id):
    client.get(f"/role/{persona_id}")


def _board_text(client, query=""):
    resp = client.get("/board" + query)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_open_listing_shown(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        _listing(db, d, "Dal rice", 5, "ambient")
    _seed(app, seed)
    body = _board_text(client)
    assert "Dal rice" in body
    assert "Donation board" in body


def test_expired_listing_excluded(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        old = _listing(db, d, "Old bread", 2, "ambient", pickup=PAST)
        new = _listing(db, d, "Dal rice", 5, "ambient")
        return old, new
    old, new = _seed(app, seed)
    assert _ids(_avail(app)) == [new]
    assert "Old bread" not in _board_text(client)


@pytest.mark.parametrize("status", ["claimed", "collected", "delivered", "expired", "cancelled"])
def test_non_posted_statuses_excluded(app, fixed_clock, status):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        _listing(db, d, "Dal rice", 5, "ambient", status=status)
    _seed(app, seed)
    assert _avail(app) == []


def test_search_matches_description_case_insensitive(app, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        return _listing(db, d, "Dal rice", 5, "ambient"), _listing(db, d, "Veg soup", 3, "chilled")
    dal, _ = _seed(app, seed)
    assert _ids(_avail(app, q="DAL")) == [dal]


def test_search_matches_donor_name(app, fixed_clock):
    def seed(db):
        a = _org(db, "Mess Alpha", "donor")
        b = _org(db, "Mess Beta", "donor")
        _listing(db, a, "Rice", 5, "ambient")
        return _listing(db, b, "Roti", 4, "ambient")
    beta = _seed(app, seed)
    assert _ids(_avail(app, q="beta")) == [beta]


def test_search_matches_allergen(app, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        nuts = _listing(db, d, "Laddoo", 2, "ambient", allergens="dairy,nuts")
        _listing(db, d, "Rice", 5, "ambient", allergens="")
        return nuts
    nuts = _seed(app, seed)
    rows = _avail(app, q="nuts")
    assert _ids(rows) == [nuts]
    assert rows[0]["allergens"] == ["dairy", "nuts"]


def test_storage_filter(app, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        chilled = _listing(db, d, "Curd", 2, "chilled")
        frozen = _listing(db, d, "Peas", 2, "frozen")
        return chilled, frozen
    chilled, frozen = _seed(app, seed)
    assert _ids(_avail(app, storage="frozen")) == [frozen]
    assert sorted(_ids(_avail(app, storage=""))) == sorted([chilled, frozen])
    assert sorted(_ids(_avail(app, storage="bogus"))) == sorted([chilled, frozen])


def test_combined_filters(app, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        chilled_dal = _listing(db, d, "Dal rice", 5, "chilled")
        _listing(db, d, "Dal rice", 5, "frozen")
        _listing(db, d, "Veg soup", 3, "chilled")
        return chilled_dal
    chilled_dal = _seed(app, seed)
    rows = _avail(app, q="dal", storage="chilled")
    assert _ids(rows) == [chilled_dal]


def test_sorted_soonest_deadline_first(app, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        at_14 = _listing(db, d, "Late", 1, "ambient", pickup="2025-01-01T14:00:00+00:00")
        at_12 = _listing(db, d, "Early", 1, "ambient", pickup="2025-01-01T12:00:00+00:00")
        at_13 = _listing(db, d, "Mid", 1, "ambient", pickup="2025-01-01T13:00:00+00:00")
        return at_14, at_12, at_13
    at_14, at_12, at_13 = _seed(app, seed)
    assert _ids(_avail(app)) == [at_12, at_13, at_14]


def test_board_does_not_write(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        return _listing(db, d, "Old bread", 2, "ambient", pickup=PAST)
    lid = _seed(app, seed)

    def counts(db):
        return (db.execute("SELECT COUNT(*) FROM listing").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM status_event").fetchone()[0])

    before = _seed(app, counts)
    assert client.get("/board").status_code == 200
    assert _seed(app, counts) == before
    assert _status(app, lid) == "posted"


def test_empty_state_message(app, client, fixed_clock):
    body = _board_text(client)
    assert "No open donations right now." in body
    assert "No donations match these filters." not in body


def test_no_results_state(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        _listing(db, d, "Dal rice", 5, "ambient")
    _seed(app, seed)
    body = _board_text(client, "?q=zzz")
    assert "No donations match these filters." in body
    assert "Reset filters" in body
    assert "No open donations right now." not in body


def test_reset_link_absent_when_unfiltered(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        _listing(db, d, "Dal rice", 5, "ambient")
    _seed(app, seed)
    assert "Reset filters" not in _board_text(client)


def test_invalid_storage_ignored(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        _listing(db, d, "Dal rice", 5, "ambient")
    _seed(app, seed)
    assert "Dal rice" in _board_text(client, "?storage=bogus")


def test_viewer_notice_visitor(app, client, fixed_clock):
    assert "Browsing as a visitor" in _board_text(client)


def test_viewer_notice_receiver(app, client, fixed_clock):
    pid = _seed(app, lambda db: _persona(
        db, "Shelter", "receiver", organisation_id=_org(db, "Shelter", "ngo")))
    _as(client, pid)
    assert "You are a receiver persona" in _board_text(client)


def test_viewer_notice_donor(app, client, fixed_clock):
    pid = _seed(app, lambda db: _persona(
        db, "Mess", "donor", organisation_id=_org(db, "Mess", "donor")))
    _as(client, pid)
    body = _board_text(client)
    assert "Only receiver personas can claim donations" in body
    assert "donor" in body


def test_board_public_without_persona_and_no_claim_form(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        _listing(db, d, "Dal rice", 5, "ambient")
    _seed(app, seed)
    body = _board_text(client)
    assert 'action="/listings/' not in body
    assert "Claim for" not in body


def test_receiver_can_claim_via_listing_page(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        lid = _listing(db, d, "Dal rice", 5, "ambient")
        r = _org(db, "Shelter", "ngo")
        pid = _persona(db, "Shelter", "receiver", organisation_id=r)
        return lid, pid
    lid, pid = _seed(app, seed)
    _as(client, pid)
    resp = client.post(f"/listings/{lid}/claim")
    assert resp.status_code == 303
    assert "claimed=1" in resp.headers["Location"]
    assert _status(app, lid) == "claimed"


def test_donor_cannot_claim(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        lid = _listing(db, d, "Dal rice", 5, "ambient")
        pid = _persona(db, "Mess", "donor", organisation_id=d)
        return lid, pid
    lid, pid = _seed(app, seed)
    _as(client, pid)
    assert client.post(f"/listings/{lid}/claim").status_code == 403
    assert _status(app, lid) == "posted"


def test_volunteer_cannot_claim(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        lid = _listing(db, d, "Dal rice", 5, "ambient")
        pid = _persona(db, "Vol", "volunteer", volunteer_id=_volunteer(db))
        return lid, pid
    lid, pid = _seed(app, seed)
    _as(client, pid)
    assert client.post(f"/listings/{lid}/claim").status_code == 403
    assert _status(app, lid) == "posted"


def test_anonymous_cannot_claim(app, client, fixed_clock):
    lid = _seed(app, lambda db: _listing(db, _org(db, "Mess A", "donor"), "Dal rice", 5, "ambient"))
    resp = client.post(f"/listings/{lid}/claim")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")
    assert _status(app, lid) == "posted"


def test_view_details_link_present(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        return _listing(db, d, "Dal rice", 5, "ambient")
    lid = _seed(app, seed)
    assert f"/listings/{lid}" in _board_text(client)


def test_home_links_to_board(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'href="/board"' in resp.get_data(as_text=True)


def test_expired_listing_cannot_be_claimed_via_board_flow(app, client, fixed_clock):
    def seed(db):
        d = _org(db, "Mess A", "donor")
        lid = _listing(db, d, "Old bread", 2, "ambient", pickup=PAST)
        r = _org(db, "Shelter", "ngo")
        pid = _persona(db, "Shelter", "receiver", organisation_id=r)
        return lid, pid
    lid, pid = _seed(app, seed)
    _as(client, pid)
    resp = client.post(f"/listings/{lid}/claim")
    assert resp.status_code == 303
    assert "claim=closed" in resp.headers["Location"]
    assert _status(app, lid) == "posted"
