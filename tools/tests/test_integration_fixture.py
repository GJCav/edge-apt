from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.workflows.repackage import repackage_project, RepackageEvent
from tests.factories import make_project, write_hello_source


@pytest.mark.integration
def test_repackage_writes_lock_and_packages(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    paths = project.paths
    write_hello_source(tmp_path)

    lock = repackage_project(project=project).lock

    assert paths.lock_path.exists()
    assert any(
        publication.key.package == "edgeapt-hello"
        and publication.e2e_commands == (("edgeapt-hello",),)
        for publication in lock.publications
    )
    assert (
        paths.packages_dir
        / "edgeapt-hello"
        / "edgeapt-hello_0.1.0-1_amd64.deb"
    ).exists()
    assert all(Path(artifact.path).suffix == ".deb" for artifact in lock.artifacts)


@pytest.mark.integration
def test_repackage_reports_progress_events(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    write_hello_source(tmp_path)
    events: list[RepackageEvent] = []

    repackage_project(on_event=events.append, project=project)

    kinds = {event.kind for event in events}
    assert "source_start" in kinds
    assert "upstream_start" in kinds
    assert "cache_hit" in kinds or "fetch_start" in kinds
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
