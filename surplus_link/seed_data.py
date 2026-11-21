"""Synthetic seed data only. No real places, addresses, or people.

Coordinates are invented offsets near 12.9000, 77.6000 for a fictional pilot city.
"""

ZONES = ("A", "B", "C")

# ---------------------------------------------------------------------------
# Organisations: 6 donor messes and 5 receiving NGOs (all synthetic)
# ---------------------------------------------------------------------------
DONOR_ORGS = [
    {"name": "Mess Donor 1 (Synthetic)", "org_type": "donor", "zone": "A", "lat": 12.9010, "lng": 77.6010,
     "weekly_intake_capacity_kg": None, "current_need_kg": None, "need_level": None,
     "accepted_storage": "", "allergen_exclusions": "", "open_hours": None},
    {"name": "Mess Donor 2 (Synthetic)", "org_type": "donor", "zone": "B", "lat": 12.9020, "lng": 77.6020,
     "weekly_intake_capacity_kg": None, "current_need_kg": None, "need_level": None,
     "accepted_storage": "", "allergen_exclusions": "", "open_hours": None},
    {"name": "Mess Donor 3 (Synthetic)", "org_type": "donor", "zone": "C", "lat": 12.9030, "lng": 77.6030,
     "weekly_intake_capacity_kg": None, "current_need_kg": None, "need_level": None,
     "accepted_storage": "", "allergen_exclusions": "", "open_hours": None},
    {"name": "Mess Donor 4 (Synthetic)", "org_type": "donor", "zone": "A", "lat": 12.9040, "lng": 77.6040,
     "weekly_intake_capacity_kg": None, "current_need_kg": None, "need_level": None,
     "accepted_storage": "", "allergen_exclusions": "", "open_hours": None},
    {"name": "Mess Donor 5 (Synthetic)", "org_type": "donor", "zone": "B", "lat": 12.9050, "lng": 77.6050,
     "weekly_intake_capacity_kg": None, "current_need_kg": None, "need_level": None,
     "accepted_storage": "", "allergen_exclusions": "", "open_hours": None},
    {"name": "Mess Donor 6 (Synthetic)", "org_type": "donor", "zone": "C", "lat": 12.9060, "lng": 77.6060,
     "weekly_intake_capacity_kg": None, "current_need_kg": None, "need_level": None,
     "accepted_storage": "", "allergen_exclusions": "", "open_hours": None},
]

NGO_ORGS = [
    {"name": "NGO Alpha (Synthetic)", "org_type": "ngo", "zone": "A", "lat": 12.9500, "lng": 77.6200,
     "weekly_intake_capacity_kg": 300, "current_need_kg": 120, "need_level": 2,
     "accepted_storage": "ambient,chilled", "allergen_exclusions": "nuts", "open_hours": "08:00-20:00 IST"},
    {"name": "NGO Bravo (Synthetic)", "org_type": "ngo", "zone": "B", "lat": 12.9550, "lng": 77.6250,
     "weekly_intake_capacity_kg": 200, "current_need_kg": 80, "need_level": 1,
     "accepted_storage": "ambient,chilled", "allergen_exclusions": "", "open_hours": "08:00-20:00 IST"},
    {"name": "NGO Charlie (Synthetic)", "org_type": "ngo", "zone": "C", "lat": 12.9600, "lng": 77.6300,
     "weekly_intake_capacity_kg": 250, "current_need_kg": 200, "need_level": 3,
     "accepted_storage": "ambient,chilled,frozen", "allergen_exclusions": "dairy", "open_hours": "09:00-21:00 IST"},
    {"name": "NGO Delta (Synthetic)", "org_type": "ngo", "zone": "A", "lat": 12.9650, "lng": 77.6350,
     "weekly_intake_capacity_kg": 150, "current_need_kg": 60, "need_level": 1,
     "accepted_storage": "ambient", "allergen_exclusions": "", "open_hours": "10:00-18:00 IST"},
    {"name": "NGO Echo (Synthetic)", "org_type": "ngo", "zone": "B", "lat": 12.9700, "lng": 77.6400,
     "weekly_intake_capacity_kg": 400, "current_need_kg": 150, "need_level": 2,
     "accepted_storage": "ambient,chilled", "allergen_exclusions": "nuts", "open_hours": "08:00-22:00 IST"},
]

# ---------------------------------------------------------------------------
# Volunteers (synthetic collectors)
# ---------------------------------------------------------------------------
VOLUNTEERS = [
    {"name": "Volunteer Ada (Synthetic)", "zone": "A", "lat": 12.9080, "lng": 77.6080,
     "carrying_capacity_kg": 25, "available_from_ist": "08:00", "available_until_ist": "20:00"},
    {"name": "Volunteer Bilal (Synthetic)", "zone": "B", "lat": 12.9120, "lng": 77.6120,
     "carrying_capacity_kg": 40, "available_from_ist": "10:00", "available_until_ist": "22:00"},
    {"name": "Volunteer Chitra (Synthetic)", "zone": "C", "lat": 12.9160, "lng": 77.6160,
     "carrying_capacity_kg": 15, "available_from_ist": "12:00", "available_until_ist": "18:00"},
    {"name": "Volunteer Dev (Synthetic)", "zone": "A", "lat": 12.9200, "lng": 77.6200,
     "carrying_capacity_kg": 30, "available_from_ist": "16:00", "available_until_ist": "23:00"},
]

# ---------------------------------------------------------------------------
# Personas: one per donor org, one per NGO (role receiver), one per volunteer.
# organisation_name / volunteer_name are resolved to ids at seed time.
# ---------------------------------------------------------------------------
PERSONAS = [
    {"display_name": "Donor 1 (Synthetic)", "role": "donor", "organisation_name": "Mess Donor 1 (Synthetic)", "volunteer_name": None},
    {"display_name": "Donor 2 (Synthetic)", "role": "donor", "organisation_name": "Mess Donor 2 (Synthetic)", "volunteer_name": None},
    {"display_name": "Donor 3 (Synthetic)", "role": "donor", "organisation_name": "Mess Donor 3 (Synthetic)", "volunteer_name": None},
    {"display_name": "Donor 4 (Synthetic)", "role": "donor", "organisation_name": "Mess Donor 4 (Synthetic)", "volunteer_name": None},
    {"display_name": "Donor 5 (Synthetic)", "role": "donor", "organisation_name": "Mess Donor 5 (Synthetic)", "volunteer_name": None},
    {"display_name": "Donor 6 (Synthetic)", "role": "donor", "organisation_name": "Mess Donor 6 (Synthetic)", "volunteer_name": None},
    {"display_name": "Receiver Alpha (Synthetic)", "role": "receiver", "organisation_name": "NGO Alpha (Synthetic)", "volunteer_name": None},
    {"display_name": "Receiver Bravo (Synthetic)", "role": "receiver", "organisation_name": "NGO Bravo (Synthetic)", "volunteer_name": None},
    {"display_name": "Receiver Charlie (Synthetic)", "role": "receiver", "organisation_name": "NGO Charlie (Synthetic)", "volunteer_name": None},
    {"display_name": "Receiver Delta (Synthetic)", "role": "receiver", "organisation_name": "NGO Delta (Synthetic)", "volunteer_name": None},
    {"display_name": "Receiver Echo (Synthetic)", "role": "receiver", "organisation_name": "NGO Echo (Synthetic)", "volunteer_name": None},
    {"display_name": "Volunteer Ada (Synthetic)", "role": "volunteer", "organisation_name": None, "volunteer_name": "Volunteer Ada (Synthetic)"},
    {"display_name": "Volunteer Bilal (Synthetic)", "role": "volunteer", "organisation_name": None, "volunteer_name": "Volunteer Bilal (Synthetic)"},
    {"display_name": "Volunteer Chitra (Synthetic)", "role": "volunteer", "organisation_name": None, "volunteer_name": "Volunteer Chitra (Synthetic)"},
    {"display_name": "Volunteer Dev (Synthetic)", "role": "volunteer", "organisation_name": None, "volunteer_name": "Volunteer Dev (Synthetic)"},
]

# M0: no listings are seeded.
LISTINGS = []
