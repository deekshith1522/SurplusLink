"""SQLite access helpers. Plain sqlite3; one connection per app context, stored on flask.g."""

import sqlite3
from pathlib import Path

from flask import current_app, g

from . import seed_data

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Rows that count as synthetic for reset purposes.
_SYN_ORG_SQL = "SELECT id FROM organisation WHERE is_synthetic = 1"
_SYN_VOL_SQL = "SELECT id FROM volunteer WHERE is_synthetic = 1"
_SYN_PERSONA_SQL = (
    "(substr(display_name, -12) = ' (Synthetic)'"
    f" OR organisation_id IN ({_SYN_ORG_SQL})"
    f" OR volunteer_id IN ({_SYN_VOL_SQL}))"
)
_SYN_LISTING_SQL = f"SELECT id FROM listing WHERE donor_org_id IN ({_SYN_ORG_SQL})"

# Deleted in foreign-key-safe order. Only synthetic rows are touched.
_RESET_STATEMENTS = [
    "DELETE FROM status_event WHERE listing_id IN ("
    f"{_SYN_LISTING_SQL}) OR actor_persona_id IN (SELECT id FROM persona WHERE {_SYN_PERSONA_SQL})",
    "DELETE FROM claim WHERE listing_id IN ("
    f"{_SYN_LISTING_SQL}) OR receiver_org_id IN ({_SYN_ORG_SQL}) OR volunteer_id IN ({_SYN_VOL_SQL})",
    f"DELETE FROM listing WHERE donor_org_id IN ({_SYN_ORG_SQL})",
    f"DELETE FROM persona WHERE {_SYN_PERSONA_SQL}",
    "DELETE FROM organisation WHERE is_synthetic = 1",
    "DELETE FROM volunteer WHERE is_synthetic = 1",
]


def get_db():
    """Return the sqlite3 connection for the current app context, opening it on first use."""
    if "db" not in g:
        path = current_app.config["DATABASE"]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables and indexes if missing. Never drops or deletes data."""
    db = get_db()
    db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    db.commit()


def count_rows():
    db = get_db()

    def scalar(sql):
        return db.execute(sql).fetchone()[0]

    return {
        "donor_orgs": scalar("SELECT COUNT(*) FROM organisation WHERE org_type = 'donor'"),
        "ngo_orgs": scalar("SELECT COUNT(*) FROM organisation WHERE org_type = 'ngo'"),
        "volunteers": scalar("SELECT COUNT(*) FROM volunteer"),
        "personas": scalar("SELECT COUNT(*) FROM persona"),
        "listings": scalar("SELECT COUNT(*) FROM listing"),
    }


def seed_db(reset=False):
    """Insert synthetic seed data in one transaction.

    Without reset, does nothing if synthetic organisations already exist.
    With reset, deletes only synthetic rows first, then inserts again.
    """
    db = get_db()
    has_synthetic = db.execute(
        "SELECT 1 FROM organisation WHERE is_synthetic = 1 LIMIT 1"
    ).fetchone() is not None

    if has_synthetic and not reset:
        return {
            "already_seeded": True,
            "message": "Synthetic data already present; nothing inserted. Use --reset to re-seed.",
            **count_rows(),
        }

    if not db.in_transaction:
        db.execute("BEGIN")
    try:
        if reset:
            for statement in _RESET_STATEMENTS:
                db.execute(statement)
        _insert_seed_rows(db)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "already_seeded": False,
        "message": "Synthetic data seeded.",
        **count_rows(),
    }


def _insert_seed_rows(db):
    org_ids = {}
    for org in seed_data.DONOR_ORGS + seed_data.NGO_ORGS:
        cur = db.execute(
            """
            INSERT INTO organisation (name, org_type, zone, lat, lng, weekly_intake_capacity_kg,
                current_need_kg, need_level, accepted_storage, allergen_exclusions, open_hours, is_synthetic)
            VALUES (:name, :org_type, :zone, :lat, :lng, :weekly_intake_capacity_kg,
                :current_need_kg, :need_level, :accepted_storage, :allergen_exclusions, :open_hours, 1)
            """,
            org,
        )
        org_ids[org["name"]] = cur.lastrowid

    volunteer_ids = {}
    for vol in seed_data.VOLUNTEERS:
        cur = db.execute(
            """
            INSERT INTO volunteer (name, zone, lat, lng, carrying_capacity_kg,
                available_from_ist, available_until_ist, is_synthetic)
            VALUES (:name, :zone, :lat, :lng, :carrying_capacity_kg,
                :available_from_ist, :available_until_ist, 1)
            """,
            vol,
        )
        volunteer_ids[vol["name"]] = cur.lastrowid

    for persona in seed_data.PERSONAS:
        org_id = org_ids[persona["organisation_name"]] if persona["organisation_name"] else None
        vol_id = volunteer_ids[persona["volunteer_name"]] if persona["volunteer_name"] else None
        db.execute(
            "INSERT INTO persona (display_name, role, organisation_id, volunteer_id) VALUES (?, ?, ?, ?)",
            (persona["display_name"], persona["role"], org_id, vol_id),
        )
