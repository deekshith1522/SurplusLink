"""M0 views: home (persona picker), role selection, switch, about."""

from flask import Blueprint, abort, redirect, render_template, session, url_for

from .db import get_db

bp = Blueprint("main", __name__)

_PERSONA_SQL = """
    SELECT p.id, p.display_name, p.role,
           COALESCE(o.name, v.name) AS org_name
    FROM persona p
    LEFT JOIN organisation o ON o.id = p.organisation_id
    LEFT JOIN volunteer v ON v.id = p.volunteer_id
"""


def current_persona():
    """Return the persona row for the session's persona_id, or None."""
    persona_id = session.get("persona_id")
    if persona_id is None:
        return None
    return get_db().execute(_PERSONA_SQL + " WHERE p.id = ?", (persona_id,)).fetchone()


@bp.app_context_processor
def inject_current_persona():
    return {"current_persona": current_persona()}


@bp.route("/")
def home():
    rows = get_db().execute(_PERSONA_SQL + " ORDER BY p.id").fetchall()
    groups = {"donor": [], "receiver": [], "volunteer": []}
    for row in rows:
        groups[row["role"]].append(row)
    return render_template("home.html", groups=groups)


@bp.route("/role/<int:persona_id>", methods=["GET", "POST"])
def select_role(persona_id):
    exists = get_db().execute("SELECT 1 FROM persona WHERE id = ?", (persona_id,)).fetchone()
    if exists is None:
        abort(404)
    session["persona_id"] = persona_id
    return redirect(url_for("main.home"))


@bp.route("/switch")
def switch():
    session.pop("persona_id", None)
    return redirect(url_for("main.home"))


@bp.route("/about")
def about():
    return render_template("about.html")
