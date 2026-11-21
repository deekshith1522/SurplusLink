from surplus_link import create_app
from surplus_link.db import count_rows, get_db, init_db, seed_db
from surplus_link.timeutils import format_ist

SAFETY_NOTICE = (
    "SurplusLink coordinates donations and does not certify food safety. "
    "Pickup-by is a donor-provided collection deadline, not a guarantee of food safety. "
    "Nothing here is a tax or legal benefit claim."
)

EXPECTED_COUNTS = {"donor_orgs": 6, "ngo_orgs": 5, "volunteers": 4, "personas": 15, "listings": 0}


def _seed(app):
    with app.app_context():
        return seed_db()


def test_schema_creates_tables(app):
    with app.app_context():
        names = {r["name"] for r in get_db().execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"organisation", "volunteer", "persona", "listing", "claim", "status_event"} <= names


def test_init_db_twice_keeps_data(app):
    _seed(app)
    with app.app_context():
        before = count_rows()
        init_db()
        assert count_rows() == before


def test_seed_counts(app):
    _seed(app)
    with app.app_context():
        assert count_rows() == EXPECTED_COUNTS


def test_seed_no_duplicate(app):
    _seed(app)
    with app.app_context():
        result = seed_db()
        assert result["already_seeded"] is True
        assert count_rows() == EXPECTED_COUNTS


def test_seed_reset(app):
    _seed(app)
    with app.app_context():
        result = seed_db(reset=True)
        assert result["already_seeded"] is False
        assert count_rows() == EXPECTED_COUNTS


def test_ist_format(app):
    assert format_ist("2025-01-01T00:00:00+00:00") == "01 Jan 2025, 05:30 IST"
    assert app.jinja_env.filters["ist"]("2025-01-01T00:00:00+00:00") == "01 Jan 2025, 05:30 IST"


def test_home_loads(client, app):
    _seed(app)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Synthetic-data demo" in body
    assert SAFETY_NOTICE in body
    assert "Mess Donor 1 (Synthetic)" in body


def test_role_selection(client, app):
    _seed(app)
    with app.app_context():
        persona_id = get_db().execute(
            "SELECT id FROM persona WHERE display_name = ?", ("Receiver Alpha (Synthetic)",)
        ).fetchone()[0]
    resp = client.get(f"/role/{persona_id}")
    assert resp.status_code == 302
    body = client.get("/").get_data(as_text=True)
    assert "Acting as" in body
    assert "Receiver Alpha (Synthetic)" in body


def test_mobile_meta(client):
    body = client.get("/").get_data(as_text=True)
    assert 'name="viewport"' in body
    assert "width=device-width" in body


def test_no_auto_reset(app):
    _seed(app)
    config = {"TESTING": True, "DATABASE": app.config["DATABASE"], "SECRET_KEY": "test"}
    app2 = create_app(config)
    with app2.app_context():
        assert count_rows() == EXPECTED_COUNTS


def test_about_has_heading(client):
    resp = client.get("/about")
    assert resp.status_code == 200
    assert "Proposed business models" in resp.get_data(as_text=True)
