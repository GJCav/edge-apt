from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.constants import LOCK_PATH, PACKAGES_DIR
from edgeapt.repackage import repackage_all


@pytest.mark.integration
def test_repackage_writes_lock_and_packages() -> None:
    lock = repackage_all()
    assert LOCK_PATH.exists()
    assert "hello" in lock.sources
    assert (PACKAGES_DIR / "hello" / "edgeapt-hello_0.1.0-1_amd64.deb").exists()
    assert all(Path(artifact.path).suffix == ".deb" for source in lock.sources.values() for artifact in source.artifacts)
