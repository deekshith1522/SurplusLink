"""Impact dashboard (M5).

Impact figures count delivered listings only, once per listing, computed from
listing rows. No carbon or emissions figure is calculated.
"""

import math

from flask import Blueprint, render_template

from .db import get_db

# Illustrative planning assumption: the one place to change the kg-per-meal figure.
MEAL_KG = 0.4

FORMULAS = [
    "Delivered food (kg) = sum of quantity_kg across listings with status Delivered, each listing counted once.",
    "Meal equivalents = delivered kg / 0.4 kg per meal, rounded down. 0.4 kg per meal is an illustrative planning assumption, not a measured portion.",
    "Estimated food waste avoided (kg) = delivered kg. This assumes the food would otherwise have been discarded. It is an estimate, not an independently verified measurement.",
    "Completed deliveries = number of listings with status Delivered.",
]

LIMITATIONS = [
    "Synthetic demo data only: organisations, people, locations and listings are invented.",
    "Posted, claimed, collected, expired and cancelled listings are excluded: food not yet delivered is not counted as rescued.",
    "Figures depend on donor-reported quantities and are not weighed or audited by SurplusLink.",
    "SurplusLink coordinates donations and does not certify food safety.",
    "No carbon or emissions savings are calculated.",
]

impact_bp = Blueprint("impact", __name__)


def impact_summary(db):
    """Return delivered-only impact totals computed from listing rows."""
    row = db.execute(
        "SELECT COUNT(*) AS deliveries, COALESCE(SUM(quantity_kg), 0) AS delivered_kg "
        "FROM listing WHERE status = 'delivered'"
    ).fetchone()
    deliveries = int(row["deliveries"])
    delivered_kg = float(row["delivered_kg"])
    meal_equivalents = int(math.floor(delivered_kg / MEAL_KG)) if deliveries else 0
    return {
        "deliveries": deliveries,
        "delivered_kg": round(delivered_kg, 1),
        "meal_equivalents": meal_equivalents,
        "waste_avoided_kg": round(delivered_kg, 1),
        "meal_kg": MEAL_KG,
        "is_zero": deliveries == 0,
    }


@impact_bp.route("/impact")
def dashboard():
    db = get_db()
    summary = impact_summary(db)
    return render_template(
        "impact.html",
        summary=summary,
        formulas=FORMULAS,
        limitations=LIMITATIONS,
    )
