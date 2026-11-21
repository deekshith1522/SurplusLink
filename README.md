# SurplusLink

A mobile-friendly web app connecting hostel messes with nearby NGOs and volunteer collectors in one pilot city.

**Synthetic-data demo.** All organisations, people, locations and listings are invented. SurplusLink coordinates donations and does not certify food safety.

## Prerequisites

- Python 3.11+ on Windows.

## Windows setup

Run these from the `surpluslink` project folder. Commands work in PowerShell or cmd unless noted.

```
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Set the secret key (optional in development; required in production):

- cmd: `set SURPLUSLINK_SECRET_KEY=your-long-random-string`
- PowerShell: `$env:SURPLUSLINK_SECRET_KEY="your-long-random-string"`

If `SURPLUSLINK_SECRET_KEY` is not set and `SURPLUSLINK_ENV` is not `production`, a development default key is used.

Initialise and seed the database:

```
flask --app surplus_link init-db
flask --app surplus_link seed
flask --app surplus_link seed --reset
```

- `seed` seeds synthetic data once and prints counts. Running it again reports that the data is already seeded.
- `seed --reset` deletes only synthetic rows and reseeds. It is explicit and never runs on its own.

Run the app:

```
flask --app surplus_link run --debug
```

Open http://127.0.0.1:5000

Run the tests:

```
pytest -q
```

## Data safety

- The app never resets data on startup.
- `init-db` never drops tables.
- `.env` is not read automatically. Set variables in your shell (or in the server's WSGI file for deployment).

## Environment variables

| Name | Purpose | Default |
| --- | --- | --- |
| `SURPLUSLINK_SECRET_KEY` | Session signing key. Required in production. | Development default (only when `SURPLUSLINK_ENV` is not `production`) |
| `SURPLUSLINK_ENV` | Set to `production` so a missing secret key fails fast. | `development` |
| `SURPLUSLINK_DB_PATH` | Path to the SQLite database file. | `instance/surpluslink.db` in the project folder |

A template is in `.env.example`. Do not commit real values.

## Project layout

- `surplus_link/` - Flask package (app factory, views, database code, time helpers).
- `surplus_link/templates/` - Jinja templates (base layout, home, about).
- `surplus_link/static/style.css` - mobile-first styles.
- `tests/` - pytest suite.
- `instance/` - local SQLite database (git-ignored, created at runtime).
- `DEPLOYMENT.md` - hosting steps for PythonAnywhere.

## Deployment

See `DEPLOYMENT.md`.
