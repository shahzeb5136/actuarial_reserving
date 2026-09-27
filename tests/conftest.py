import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from reserving.data import read_claims  # noqa: E402

PROJECT_DIR = APP_DIR / "archive" / "r_shiny_app"   # archived R app: the data behind the references
CLAIMS_XLSX = PROJECT_DIR / "aggregated_claims.xlsx"


@pytest.fixture(scope="session")
def claims():
    if not CLAIMS_XLSX.exists():
        pytest.skip(f"sample claims file not found at {CLAIMS_XLSX}")
    return read_claims(CLAIMS_XLSX)
