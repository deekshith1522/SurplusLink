import sys
from pathlib import Path

import pytest

# Make the surpluslink/ folder importable so `import surplus_link` works under pytest.
_PROJECT_DIR = str(Path(__file__).resolve().parent.parent)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from surplus_link import create_app  # noqa: E402
from surplus_link.db import init_db  # noqa: E402


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.db"),
            "SECRET_KEY": "test",
        }
    )
    with app.app_context():
        init_db()
    yield app


@pytest.fixture
def client(app):
    return app.test_client()
