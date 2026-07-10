from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.errors import ValidationError
from edgeapt.constants import ProjectPaths
from edgeapt.lockfile import write_lock
from edgeapt.models import LockedPublication
from edgeapt.models import LockFile
from edgeapt.models import SourceConfig
from edgeapt.planner import build_repo_plan
from edgeapt.repackage import repackage_all
from edgeapt.repackage import RepackageEvent
from edgeapt.util import sha256_file
from tests.factories import make_artifact
from tests.factories import make_source


def test_repackage_reuses_cached_package_and_refreshes_publications(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _source(
        suites=("focal", "jammy", "noble", "resolute"),
        e2e_command=("edgeapt-hello", "--help"),
    )
    artifact_path = _artifact_path(tmp_path)
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"cached artifact")
    previous_source = _source(suites=("jammy", "noble"))
    _write_previous_lock(
        tmp_path,
        source=previous_source,
        artifact_path=artifact_path,
    )
    _patch_environment(monkeypatch, tmp_path, source)

    def fail_fetch(*args: object, **kwargs: object) -> object:
        raise AssertionError("fetch_upstream should not run for cached artifact")

    monkeypatch.setattr("edgeapt.repackage.fetch_upstream", fail_fetch)
    events: list[RepackageEvent] = []

    lock = repackage_all(on_event=events.append, paths=ProjectPaths(tmp_path))

    assert lock.artifacts[0].created_at == "2026-07-05T00:00:00Z"
    assert [item.key.suite for item in lock.publications] == [
        "focal",
        "jammy",
        "noble",
        "resolute",
    ]
    assert all(
        item.e2e_commands == (("edgeapt-hello", "--help"),)
        for item in lock.publications
    )
    assert any(event.kind == "cache_hit" for event in events)


def test_repackage_cache_miss_when_artifact_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _source(suites=("noble",))
    artifact_path = _artifact_path(tmp_path)
    _write_previous_lock(tmp_path, source=source, artifact_path=artifact_path)
    _patch_environment(monkeypatch, tmp_path, source)
    events: list[RepackageEvent] = []

    def stop_after_cache_miss(*args: object, **kwargs: object) -> object:
        raise RuntimeError("stop after cache miss")

    monkeypatch.setattr("edgeapt.repackage.fetch_upstream", stop_after_cache_miss)

    with pytest.raises(RuntimeError, match="stop after cache miss"):
        repackage_all(on_event=events.append, paths=ProjectPaths(tmp_path))

    assert any(
        event.kind == "cache_miss" and event.message == "miss - artifact missing"
        for event in events
    )


def test_repackage_rejects_changed_plan_for_same_deb_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    previous = _source(suites=("noble",))
    current = make_source(
        source_id="hello",
        package="edgeapt-hello",
        version="v0.1.0",
        suites=("noble",),
        install_path="/usr/local/bin/edgeapt-hello",
        e2e_command=("edgeapt-hello",),
        url="tests/fixtures/hello-world",
    )
    artifact_path = _artifact_path(tmp_path)
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"cached artifact")
    _write_previous_lock(tmp_path, source=previous, artifact_path=artifact_path)
    _patch_environment(monkeypatch, tmp_path, current)

    with pytest.raises(ValidationError, match="build plan changed for DebKey"):
        repackage_all(paths=ProjectPaths(tmp_path))


def _source(
    *,
    suites: tuple[str, ...],
    e2e_command: tuple[str, ...] = ("edgeapt-hello",),
) -> SourceConfig:
    return make_source(
        source_id="hello",
        package="edgeapt-hello",
        version="v0.1.0",
        suites=suites,
        url="tests/fixtures/hello-world",
        install_path="/usr/bin/edgeapt-hello",
        e2e_command=e2e_command,
    )


def _artifact_path(tmp_path: Path) -> Path:
    return (
        tmp_path
        / "packages"
        / "edgeapt-hello"
        / "edgeapt-hello_0.1.0-1_amd64.deb"
    )


def _write_previous_lock(
    tmp_path: Path,
    *,
    source: SourceConfig,
    artifact_path: Path,
) -> None:
    plan = build_repo_plan((source,))
    build = plan.builds[0]
    relative_path = artifact_path.relative_to(tmp_path).as_posix()
    artifact = make_artifact(
        deb_key=build.deb_key,
        build_plan_digest=build.plan_digest,
        path=relative_path,
        sha256=(
            sha256_file(artifact_path)
            if artifact_path.exists()
            else "sha256:missing"
        ),
        created_at="2026-07-05T00:00:00Z",
    )
    publications = tuple(
        LockedPublication(
            key=item.key,
            artifact=item.deb_key,
            provenance=item.provenance,
            e2e_commands=item.e2e_commands,
        )
        for item in plan.publications
    )
    write_lock(
        LockFile(
            schema="edgeapt.lock/v2",
            generated_at="2026-07-05T00:00:00Z",
            plan_digest=plan.plan_digest,
            artifacts=(artifact,),
            publications=publications,
        ),
        tmp_path / "lock.json",
    )


def _patch_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: SourceConfig,
) -> None:
    def load_test_sources(*args: object, **kwargs: object) -> tuple[SourceConfig, ...]:
        return (source,)

    monkeypatch.setattr(
        "edgeapt.repackage.load_sources",
        load_test_sources,
    )
