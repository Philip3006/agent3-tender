from pathlib import Path

import pytest

from agent3_tender.core.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(path=tmp_path / "test.db")
    yield s
    s.close()
