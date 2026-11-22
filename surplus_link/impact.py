"""Impact dashboard (M5).

Impact figures count delivered listings only, once per listing, computed from
listing rows. No carbon or emissions figure is calculated.
"""

from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from flask import Blueprint, render_template

from .db import get_db

# Illustrative planning assumption: the one place to change the kg-per-meal figure.
MEAL_KG = 0.4


def build_formulas(meal_kg):
    """Return the displayed formulas, with the kg-per-meal figure taken from meal_kg."""
    m = f"{meal_kg:g}"
    return [
        "Delivered food (kg) = sum of quantity_kg across listings with status Delivered, each listing counted once.",
        f"Meal equivalents = delivered kg / {m} kg per meal, rounded down. {m} kg per meal is an illustrative planning assumption, not a measured portion.",
        "Estimated food waste avoided (kg) = delivered kg. This assumes the food would otherwise have been discarded. It is an estimate, not an independently verified measurement.",
        "Completed deliveries = number of listings with status Delivered.",
    ]


FORMULAS = build_formulas(MEAL_KG)

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
    rows = db.execute(
        "SELECT quantity_kg FROM listing WHERE status = 'delivered'"
    ).fetchall()
    deliveries = len(rows)
    total = Decimal("0")
    for row in rows:
        total += Decimal(str(row["quantity_kg"]))
    if deliveries == 0:
        meal_equivalents = 0
    else:
        meal_equivalents = int(
            (total / Decimal(str(MEAL_KG))).to_integral_value(rounding=ROUND_FLOOR)
        )
    delivered_kg = float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return {
        "deliveries": deliveries,
        "delivered_kg": delivered_kg,
        "meal_equivalents": meal_equivalents,
        "waste_avoided_kg": delivered_kg,
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
        formulas=build_formulas(MEAL_KG),
        limitations=LIMITATIONS,
    )
