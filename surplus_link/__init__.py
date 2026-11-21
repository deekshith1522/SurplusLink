"""SurplusLink application factory (M0: skeleton, schema, seed, persona picker)."""

import os
from pathlib import Path

import click
from flask import Flask

from . import db, timeutils, views
from .donations import donations_bp
from .matching import matching_bp
from .claiming import claiming_bp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEV_SECRET_KEY = "dev-only-insecure-key"


def create_app(test_config=None):
    test_config = test_config or {}
    app = Flask(__name__)

    env_secret = os.environ.get("SURPLUSLINK_SECRET_KEY")
    if "SECRET_KEY" in test_config:
        secret = test_config["SECRET_KEY"]
    elif env_secret:
        secret = env_secret
    elif os.environ.get("SURPLUSLINK_ENV") == "production":
        raise RuntimeError("SURPLUSLINK_SECRET_KEY must be set when SURPLUSLINK_ENV=production")
    else:
        secret = DEV_SECRET_KEY

    default_db = PROJECT_ROOT / "instance" / "surpluslink.db"
    app.config.from_mapping(
        SECRET_KEY=secret,
        DATABASE=os.environ.get("SURPLUSLINK_DB_PATH", str(default_db)),
    )
    app.config.from_mapping(test_config)

    app.teardown_appcontext(db.close_db)
    app.register_blueprint(views.bp)
    app.register_blueprint(donations_bp)
    app.register_blueprint(matching_bp)
    app.register_blueprint(claiming_bp)
    app.add_template_filter(timeutils.format_ist, "ist")

    @app.cli.command("init-db")
    def init_db_command():
        """Create the database tables if they do not exist. Keeps existing data."""
        db.init_db()
        click.echo(f"Database ready at {app.config['DATABASE']}")

    @app.cli.command("seed")
    @click.option("--reset", is_flag=True, help="Delete synthetic rows only, then seed again.")
    def seed_command(reset):
        """Insert synthetic demo data (idempotent unless --reset)."""
        db.init_db()
        result = db.seed_db(reset=reset)
        click.echo(result["message"])
        click.echo(
            f"donor orgs={result['donor_orgs']} ngo orgs={result['ngo_orgs']} "
            f"volunteers={result['volunteers']} personas={result['personas']} "
            f"listings={result['listings']}"
        )

    return app
