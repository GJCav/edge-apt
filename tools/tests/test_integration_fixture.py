from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.constants import LOCK_PATH, PACKAGES_DIR
from edgeapt.repackage import repackage_all, RepackageEvent


@pytest.mark.integration
def test_repackage_writes_lock_and_packages() -> None:
    lock = repackage_all()
    assert LOCK_PATH.exists()
    assert "hello" in lock.sources
    assert lock.sources["hello"].e2e_command == ("edgeapt-hello",)
    assert (PACKAGES_DIR / "hello" / "edgeapt-hello_0.1.0-1_amd64.deb").exists()
    assert all(Path(artifact.path).suffix == ".deb" for source in lock.sources.values() for artifact in source.artifacts)


@pytest.mark.integration
def test_repackage_reports_progress_events() -> None:
    events: list[RepackageEvent] = []

    repackage_all(on_event=events.append)

    kinds = {event.kind for event in events}
    assert "source_start" in kinds
    assert "upstream_start" in kinds
    assert "fetch_start" in kinds
    assert "artifact_done" in kinds
    assert any(event.package == "edgeapt-hello" for event in events)
    artifact_done = next(event for event in events if event.kind == "artifact_done")
    assert artifact_done.package is not None
    assert artifact_done.template is not None
    assert artifact_done.version is not None
    assert artifact_done.arch is not None
    assert artifact_done.path is not None
    assert artifact_done.url is not None
    assert artifact_done.size is not None
    assert artifact_done.size > 0
