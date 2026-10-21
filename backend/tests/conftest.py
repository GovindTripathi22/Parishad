import asyncio
import sys
from pathlib import Path
import pytest

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database.connection import init_db


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    """Ensure database tables and columns are initialized before running tests."""
    asyncio.run(init_db())
    yield
