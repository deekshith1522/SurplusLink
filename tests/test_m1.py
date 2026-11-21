"""M1 tests: donation form, server-side validation, persistence, detail page, donor-only posting."""

import re
from datetime import datetime, timezone

import pytest

from surplus_link import create_app
from surplus_link.db import get_db, seed_db
from surplus_link.donations import validate_listing_form
from surplus_link.timeutils import set_clock
from werkzeug.datastructures import MultiDict

FIXED_NOW = datetime(2025, 1, 1, 6, 0, tzinfo=timezone.utc)  # 11:30 IST


@pytest.fixture
def frozen_clock():
    set_clock(lambda: FIXED_NOW)
    yield
    set_clock(None)


def _valid_form():
    return {
        "description": "Dal and rice",
        "quantity_kg": "12.5",
        "pickup_by": "2025-01-01T15:00",
        "storage_type": "chilled",
        "handling_notes": "Keep covered",
        "allergens": ["dairy", "gluten"],
    }


def _validate(**overrides):
    data = _valid_form()
    data.update(overrides)
    form = MultiDict()
    for key, value in data.items():
        if isinstance(value, list):
            for item in value:
                form.add(key, item)
        else:
            form.add(key, value)
    return validate_listing_form(form, FIXED_NOW)


def _seed_and_get_ids(app):
    with app.app_context():
        seed_db()
        db = get_db()
        ids = {}
        for role in ("donor", "receiver", "volunteer"):
            ids[role] = db.execute(
                "SELECT id FROM persona WHERE role=? ORDER BY id LIMIT 1", (role,)
            ).fetchone()[0]
        ids["donor_org_id"] = db.execute(
            "SELECT organisation_id FROM persona WHERE id=?", (ids["donor"],)
        ).fetchone()[0]
    return ids


def _login(client, persona_id):
    client.get(f"/role/{persona_id}")


def _post(client, **overrides):
    data = _valid_form()
    data.update(overrides)
    return client.post("/donate", data=data)


def _listing_count(app):
    with app.app_context():
        return get_db().execute("SELECT COUNT(*) FROM listing").fetchone()[0]


# ---- validation (pure function) ----


def test_validate_accepts_valid_input():
    cleaned, errors = _validate()
    assert errors == {}
    assert cleaned["pickup_by_utc"] == "2025-01-01T09:30:00+00:00"
    assert set(cleaned["allergens"].split(",")) == {"dairy", "gluten"}


def test_validate_rejects_past_deadline():
    _, errors = _validate(pickup_by="2025-01-01T11:00")
    assert "pickup_by" in errors


@pytest.mark.parametrize("quantity", ["", "0", "-3", "abc", "1001", "nan"])
def test_validate_rejects_bad_quantity(quantity):
    _, errors = _validate(quantity_kg=quantity)
    assert "quantity_kg" in errors


@pytest.mark.parametrize("description", ["", "   "])
def test_validate_requires_description(description):
    _, errors = _validate(description=description)
    assert "description" in errors


def test_validate_rejects_unknown_storage():
    _, errors = _validate(storage_type="frozenish")
    assert "storage_type" in errors


def test_validate_rejects_unknown_allergen():
    _, errors = _validate(allergens=["peanut-ish"])
    assert "allergens" in errors


def test_validate_rejects_long_description():
    _, errors = _validate(description="x" * 201)
    assert "description" in errors


def test_validate_rejects_bad_deadline_format():
    _, errors = _validate(pickup_by="tomorrow")
    assert "pickup_by" in errors


# ---- routes and persistence ----


def test_donor_can_post_and_is_redirected(frozen_clock, app, client):
    ids = _seed_and_get_ids(app)
    _login(client, ids["donor"])
    response = _post(client)
    assert response.status_code == 303
    location = response.headers["Location"]
    assert "/listings/" in location
    assert "created=1" in location


def test_listing_saved_in_database(frozen_clock, app, client):
    ids = _seed_and_get_ids(app)
    _login(client, ids["donor"])
    _post(client)
    with app.app_context():
        db = get_db()
        row = db.execute(
            "SELECT id, status, donor_org_id, pickup_by_utc FROM listing"
        ).fetchone()
        assert row is not None
        listing_id, status, donor_org_id, pickup_by_utc = row
        assert status == "posted"
        assert donor_org_id == ids["donor_org_id"]
        assert pickup_by_utc == "2025-01-01T09:30:00+00:00"
        events = db.execute(
            "SELECT from_status, to_status FROM status_event WHERE listing_id=?",
            (listing_id,),
        ).fetchall()
        assert len(events) == 1
        assert events[0][0] is None
        assert events[0][1] == "posted"


def test_detail_page_shows_ist_deadline(frozen_clock, app, client):
    ids = _seed_and_get_ids(app)
    _login(client, ids["donor"])
    response = _post(client)
    detail = client.get(response.headers["Location"])
    assert detail.status_code == 200
    body = detail.get_data(as_text=True)
    assert "01 Jan 2025, 15:00 IST" in body
    assert "Dal and rice" in body
    assert "Posted" in body


def test_persists_after_refresh(frozen_clock, app, client):
    ids = _seed_and_get_ids(app)
    _login(client, ids["donor"])
    response = _post(client)
    location = response.headers["Location"]

    app2 = create_app(
        {"TESTING": True, "DATABASE": app.config["DATABASE"], "SECRET_KEY": "test"}
    )
    fresh_client = app2.test_client()
    detail = fresh_client.get(location)
    assert detail.status_code == 200
    assert "Dal and rice" in detail.get_data(as_text=True)


def test_invalid_post_preserves_values_and_shows_errors(frozen_clock, app, client):
    ids = _seed_and_get_ids(app)
    _login(client, ids["donor"])
    response = _post(
        client,
        description="Rice",
        quantity_kg="",
        pickup_by="2025-01-01T11:00",
        storage_type="chilled",
        handling_notes="Keep warm",
    )
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert 'value="Rice"' in body
    assert "Keep warm" in body
    assert 'class="field-error"' in body
    assert 'id="quantity_kg-error"' in body
    assert _listing_count(app) == 0


def test_invalid_post_keeps_checked_allergens(frozen_clock, app, client):
    ids = _seed_and_get_ids(app)
    _login(client, ids["donor"])
    response = _post(client, quantity_kg="", allergens=["nuts"])
    assert response.status_code == 400
    assert re.search(r'value="nuts"\s+checked', response.get_data(as_text=True))


def test_receiver_cannot_post(frozen_clock, app, client):
    ids = _seed_and_get_ids(app)
    _login(client, ids["receiver"])
    assert client.get("/donate").status_code == 403
    assert _post(client).status_code == 403
    assert _listing_count(app) == 0


def test_volunteer_cannot_post(frozen_clock, app, client):
    ids = _seed_and_get_ids(app)
    _login(client, ids["volunteer"])
    assert client.get("/donate").status_code == 403
    assert _post(client).status_code == 403
    assert _listing_count(app) == 0


def test_anonymous_redirected(frozen_clock, app, client):
    _seed_and_get_ids(app)
    get_response = client.get("/donate")
    assert get_response.status_code == 302
    assert get_response.headers["Location"].endswith("/")
    post_response = _post(client)
    assert post_response.status_code == 302
    assert post_response.headers["Location"].endswith("/")
    assert _listing_count(app) == 0


def test_unknown_listing_404(app, client):
    _seed_and_get_ids(app)
    assert client.get("/listings/9999").status_code == 404


def test_detail_visible_without_persona(frozen_clock, app, client):
    ids = _seed_and_get_ids(app)
    _login(client, ids["donor"])
    response = _post(client)
    location = response.headers["Location"]
    anonymous_client = app.test_client()
    assert anonymous_client.get(location).status_code == 200


def test_form_renders_for_donor(app, client):
    ids = _seed_and_get_ids(app)
    _login(client, ids["donor"])
    response = client.get("/donate")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'name="pickup_by"' in body
    assert 'type="datetime-local"' in body
    assert 'name="allergens"' in body
    assert 'name="quantity_kg"' in body
    assert 'name="storage_type"' in body
