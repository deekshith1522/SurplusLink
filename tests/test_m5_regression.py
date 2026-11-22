from decimal import Decimal

import pytest

from surplus_link import impact
from surplus_link.impact import impact_summary, build_formulas
from surplus_link.db import get_db


def add_donor(db):
    cur = db.execute(
        "INSERT INTO organisation (name, org_type, zone, lat, lng, is_synthetic) "
        "VALUES ('Regression donor', 'donor', 'Zone A', 12.9, 77.6, 1)"
    )
    return cur.lastrowid


def add_listing(db, donor_id, qty, status="delivered"):
    cur = db.execute(
        "INSERT INTO listing (donor_org_id, description, quantity_kg, pickup_by_utc, "
        "storage_type, handling_notes, allergens, status, created_at_utc, updated_at_utc) "
        "VALUES (?, 'x', ?, '2030-01-01T15:00:00+00:00', 'chilled', '', '', ?, "
        "'2025-01-01T06:00:00+00:00', '2025-01-01T06:00:00+00:00')",
        (donor_id, qty, status),
    )
    return cur.lastrowid


def summary(app):
    with app.app_context():
        return impact_summary(get_db())


def seed(app, rows):
    """rows: list of (qty, status)."""
    with app.app_context():
        db = get_db()
        donor = add_donor(db)
        for qty, status in rows:
            add_listing(db, donor, qty, status)
        db.commit()


def test_1_2kg_gives_3_meals(app):
    seed(app, [(1.2, "delivered")])
    assert summary(app)["meal_equivalents"] == 3


def test_2_4kg_gives_6_meals(app):
    seed(app, [(2.4, "delivered")])
    assert summary(app)["meal_equivalents"] == 6


def test_two_0_6kg_rows_give_3_meals(app):
    seed(app, [(0.6, "delivered"), (0.6, "delivered")])
    s = summary(app)
    assert s["meal_equivalents"] == 3
    assert s["deliveries"] == 2


def test_two_1_2kg_rows_give_6_meals(app):
    seed(app, [(1.2, "delivered"), (1.2, "delivered")])
    assert summary(app)["meal_equivalents"] == 6


def test_ten_0_12kg_rows_give_3_meals(app):
    seed(app, [(0.12, "delivered")] * 10)
    assert summary(app)["meal_equivalents"] == 3


def test_three_0_8kg_rows_give_6_meals(app):
    seed(app, [(0.8, "delivered")] * 3)
    assert summary(app)["meal_equivalents"] == 6


def test_1_19kg_gives_2_meals(app):
    seed(app, [(1.19, "delivered")])
    assert summary(app)["meal_equivalents"] == 2


@pytest.mark.parametrize("qty, expected", [(0.3, 0), (0.8, 2), (2.0, 5), (2.39, 5)])
def test_meal_floor_parametrized(app, qty, expected):
    seed(app, [(qty, "delivered")])
    assert summary(app)["meal_equivalents"] == expected
    assert expected == int(Decimal(str(qty)) // Decimal("0.4"))


def test_1_19kg_display_and_waste(app):
    seed(app, [(1.19, "delivered")])
    s = summary(app)
    assert s["delivered_kg"] == 1.19
    assert s["waste_avoided_kg"] == 1.19


def test_1_194kg_displays_1_19_and_gives_2_meals(app):
    seed(app, [(1.194, "delivered")])
    s = summary(app)
    assert s["delivered_kg"] == 1.19
    assert s["meal_equivalents"] == 2


def test_only_delivered_rows_count_toward_meals(app):
    seed(app, [(1.2, "delivered"), (50.0, "posted"), (50.0, "claimed")])
    assert summary(app)["meal_equivalents"] == 3


def test_build_formulas_follows_meal_kg():
    assert "0.4 kg per meal" in build_formulas(0.4)[1]
    assert "0.5 kg per meal" in build_formulas(0.5)[1]
    assert "0.4 kg per meal" not in build_formulas(0.5)[1]


def test_meal_kg_read_at_call_time(app, monkeypatch):
    monkeypatch.setattr(impact, "MEAL_KG", 0.5)
    seed(app, [(1.0, "delivered")])
    s = summary(app)
    assert s["meal_equivalents"] == 2
    assert s["meal_kg"] == 0.5


def test_impact_page_shows_meal_kg_formula(app, client, monkeypatch):
    monkeypatch.setattr(impact, "MEAL_KG", 0.5)
    response = client.get("/impact")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "0.5 kg per meal" in body
    assert "0.4 kg per meal" not in body


def test_empty_database_is_zero(app):
    s = summary(app)
    assert s["meal_equivalents"] == 0
    assert s["is_zero"] is True
