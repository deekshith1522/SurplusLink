"""WSGI entry point.

On PythonAnywhere, the web app's WSGI configuration file should load this module
(or an equivalent that puts the project folder on sys.path and exposes `application`).
"""

import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from surplus_link import create_app  # noqa: E402

application = create_app()
