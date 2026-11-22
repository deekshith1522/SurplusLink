import html
import re

import pytest

from surplus_link import impact
from surplus_link.db import get_db
from surplus_link.impact import impact_summary, FORMULAS, LIMITATIONS


def insert_org(db, name, org_type, zone="Zone A", lat=12.9, lng=77.6):
    cur = db.execute(
        "INSERT INTO organisation (name, org_type, zone, lat, lng) VALUES (?, ?, ?, ?, ?)",
        (name, org_type, zone, lat, lng),
    )
    return cur.lastrowid


def insert_listing(db, donor_id, qty, status, pickup="2030-01-01T15:00:00+00:00"):
    cur = db.execute(
        "INSERT INTO listing (donor_org_id, description, quantity_kg, pickup_by_utc, "
        "storage_type, status, created_at_utc, updated_at_utc) "
        "VALUES (?, 'Test food', ?, ?, 'ambient', ?, "
        "'2025-01-01T06:00:00+00:00', '2025-01-01T06:00:00+00:00')",
        (donor_id, qty, pickup, status),
    )
    return cur.lastrowid


def insert_event(db, listing_id, frm, to, at="2025-01-01T07:00:00+00:00"):
    cur = db.execute(
        "INSERT INTO status_event (listing_id, from_status, to_status, actor_persona_id, at_utc) "
        "VALUES (?, ?, ?, NULL, ?)",
        (listing_id, frm, to, at),
    )
    return cur.lastrowid


def seed(app, rows):
    """rows: list of (qty_kg, status). Returns listing ids in order."""
    with app.app_context():
        db = get_db()
        donor = insert_org(db, "Donor", "donor")
        ids = [insert_listing(db, donor, qty, status) for qty, status in rows]
        db.commit()
    return ids


def summary(app):
    with app.app_context():
        return impact_summary(get_db())


def test_empty_database_is_zero(app):
    s = summary(app)
    assert s["deliveries"] == 0
    assert s["delivered_kg"] == 0.0
    assert s["meal_equivalents"] == 0
    assert s["waste_avoided_kg"] == 0.0
    assert s["is_zero"] is True


def test_empty_state_page_shows_message(app, client):
    resp = client.get("/impact")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "No deliveries recorded yet." in body
    assert "Synthetic-data demo" in body
    assert "How the figures are calculated" in body
    assert "Limitations" in body


def test_mixed_statuses_only_delivered_counted(app):
    seed(app, [
        (10.0, "delivered"),
        (5.5, "delivered"),
        (100, "posted"),
        (50, "claimed"),
        (40, "collected"),
        (30, "expired"),
        (20, "cancelled"),
    ])
    s = summary(app)
    assert s["deliveries"] == 2
    assert s["delivered_kg"] == 15.5
    assert s["meal_equivalents"] == 38
    assert s["waste_avoided_kg"] == 15.5
    assert s["is_zero"] is False


@pytest.mark.parametrize("status", ["posted", "claimed", "collected", "expired", "cancelled"])
def test_excluded_statuses_not_counted_individually(app, status):
    seed(app, [(7.0, status)])
    s = summary(app)
    assert s["deliveries"] == 0
    assert s["is_zero"] is True


def test_duplicate_status_events_do_not_double_count(app):
    (lid,) = seed(app, [(8.0, "delivered")])
    with app.app_context():
        db = get_db()
        for _ in range(3):
            insert_event(db, lid, "collected", "delivered")
        insert_event(db, lid, "claimed", "collected")
        db.commit()
    s = summary(app)
    assert s["deliveries"] == 1
    assert s["delivered_kg"] == 8.0
    assert s["meal_equivalents"] == 20


@pytest.mark.parametrize(
    "qty, meals",
    [(1.0, 2), (0.3, 0)],
)
def test_meal_rounding_down(app, qty, meals):
    seed(app, [(qty, "delivered")])
    s = summary(app)
    assert s["deliveries"] == 1
    assert s["meal_equivalents"] == meals
    assert s["is_zero"] is False


def test_new_delivery_updates_totals(app):
    (first,) = seed(app, [(4.0, "delivered")])
    (posted,) = seed(app, [(6.0, "posted")])
    before = summary(app)
    assert before["delivered_kg"] == 4.0
    with app.app_context():
        db = get_db()
        db.execute("UPDATE listing SET status = 'delivered' WHERE id = ?", (posted,))
        db.commit()
    after = summary(app)
    assert after["delivered_kg"] == 10.0
    assert after["deliveries"] == 2
    assert after["meal_equivalents"] == 25


def test_dashboard_shows_figures_and_formulas(app, client):
    seed(app, [(12.5, "delivered")])
    resp = client.get("/impact")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "12.5 kg" in body
    assert "31" in body
    for f in FORMULAS:
        assert html.escape(f) in body
    for lim in LIMITATIONS:
        assert html.escape(lim) in body
    assert "not an independently verified measurement" in body


def test_dashboard_does_not_show_carbon(app, client):
    seed(app, [(12.5, "delivered")])
    body = client.get("/impact").get_data(as_text=True)
    assert body.lower().count("carbon") == 1
    assert "No carbon or emissions savings are calculated." in body


def test_dashboard_public_without_persona(app):
    fresh = app.test_client()
    resp = fresh.get("/impact")
    assert resp.status_code == 200


def test_nav_has_impact_link(client):
    body = client.get("/").get_data(as_text=True)
    assert 'href="/impact"' in body


def test_about_business_models_replaced(client):
    resp = client.get("/about")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for phrase in [
        "Target customer",
        "Proposed payer",
        "Revenue model (proposed)",
        "Pricing assumptions (unvalidated)",
        "All business models below are proposals",
    ]:
        assert phrase in body
    assert "Details to be added in a later milestone." not in body
    assert re.search(r"[£$€₹]\s?\d", body) is None


def test_meal_constant_documented():
    assert impact.MEAL_KG == 0.4
    assert any("0.4 kg per meal" in f for f in FORMULAS)


def test_limitations_exclude_unfinished_statuses():
    assert any("Posted, claimed, collected, expired and cancelled" in l for l in LIMITATIONS)


def test_summary_reads_listing_rows_not_events(app):
    (lid,) = seed(app, [(9.0, "collected")])
    with app.app_context():
        db = get_db()
        insert_event(db, lid, "collected", "delivered")
        insert_event(db, lid, "delivered", "delivered")
        db.commit()
    s = summary(app)
    assert s["deliveries"] == 0
