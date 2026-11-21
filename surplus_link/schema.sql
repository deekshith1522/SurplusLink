-- SurplusLink schema (SQLite). Synthetic data only.
--
-- DOCUMENTATION OF CAPACITY RULES (logic is NOT implemented in M0):
--   * organisation.weekly_intake_capacity_kg = maximum kg a receiver (NGO) can take in a week.
--   * organisation.current_need_kg           = kg the NGO currently wants.
--   * An active claim (claim.status = 'active') reserves kg from the receiver's
--     remaining weekly capacity.
--   * A claim is 'released' when it is cancelled, or when its listing expires.
--   * Delivered / completed claims consume the receiver's capacity for the week.
--
-- Timestamps are UTC ISO-8601 text (e.g. 2025-01-01T00:00:00+00:00).
-- Volunteer availability times are IST text (HH:MM).
-- Every table here is created with IF NOT EXISTS; nothing is ever dropped by init.

CREATE TABLE IF NOT EXISTS organisation (
    id                        INTEGER PRIMARY KEY,
    name                      TEXT NOT NULL,
    org_type                  TEXT NOT NULL CHECK (org_type IN ('donor', 'ngo')),
    zone                      TEXT NOT NULL,
    lat                       REAL NOT NULL,
    lng                       REAL NOT NULL,
    weekly_intake_capacity_kg REAL,
    current_need_kg           REAL,
    need_level                INTEGER CHECK (need_level BETWEEN 1 AND 3),
    accepted_storage          TEXT NOT NULL DEFAULT '',
    allergen_exclusions       TEXT NOT NULL DEFAULT '',
    open_hours                TEXT,
    is_synthetic              INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS volunteer (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT NOT NULL,
    zone                 TEXT NOT NULL,
    lat                  REAL NOT NULL,
    lng                  REAL NOT NULL,
    carrying_capacity_kg REAL NOT NULL CHECK (carrying_capacity_kg > 0),
    available_from_ist   TEXT NOT NULL,
    available_until_ist  TEXT NOT NULL,
    is_synthetic         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS persona (
    id              INTEGER PRIMARY KEY,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('donor', 'receiver', 'volunteer')),
    organisation_id INTEGER REFERENCES organisation(id),
    volunteer_id    INTEGER REFERENCES volunteer(id),
    CHECK (
        (role IN ('donor', 'receiver') AND organisation_id IS NOT NULL AND volunteer_id IS NULL)
        OR
        (role = 'volunteer' AND volunteer_id IS NOT NULL AND organisation_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS listing (
    id              INTEGER PRIMARY KEY,
    donor_org_id    INTEGER NOT NULL REFERENCES organisation(id),
    description     TEXT NOT NULL,
    quantity_kg     REAL NOT NULL CHECK (quantity_kg > 0),
    pickup_by_utc   TEXT NOT NULL,
    storage_type    TEXT NOT NULL CHECK (storage_type IN ('ambient', 'chilled', 'frozen')),
    handling_notes  TEXT NOT NULL DEFAULT '',
    allergens       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL CHECK (status IN ('posted', 'claimed', 'collected', 'delivered', 'expired', 'cancelled')),
    created_at_utc  TEXT NOT NULL,
    updated_at_utc  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim (
    id               INTEGER PRIMARY KEY,
    listing_id       INTEGER NOT NULL UNIQUE REFERENCES listing(id),
    receiver_org_id  INTEGER NOT NULL REFERENCES organisation(id),
    volunteer_id     INTEGER REFERENCES volunteer(id),
    quantity_kg      REAL NOT NULL CHECK (quantity_kg > 0),
    status           TEXT NOT NULL CHECK (status IN ('active', 'released', 'completed')),
    claimed_at_utc   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS status_event (
    id               INTEGER PRIMARY KEY,
    listing_id       INTEGER NOT NULL REFERENCES listing(id),
    from_status      TEXT,
    to_status        TEXT NOT NULL,
    actor_persona_id INTEGER REFERENCES persona(id),
    at_utc           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_listing_status ON listing(status);
CREATE INDEX IF NOT EXISTS idx_claim_receiver_status ON claim(receiver_org_id, status);
CREATE INDEX IF NOT EXISTS idx_status_event_listing ON status_event(listing_id);
