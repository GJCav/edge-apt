from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.errors import ValidationError
from edgeapt.domain.lock import LockedPublication, LockFile
from edgeapt.infrastructure.lock_store import write_lock
from edgeapt.templates.base import SourceTemplate
from edgeapt.templates.base import FetchResult
from edgeapt.util import sha256_file
from edgeapt.workflows.planning import build_repo_plan
from edgeapt.workflows.repackage import repackage_project, RepackageEvent
from tests.factories import make_artifact
from tests.factories import make_document
from tests.factories import make_project
from tests.factories import make_source


def test_repackage_reuses_cached_package_and_refreshes_publications(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _source(
        suites=("focal", "jammy", "noble", "resolute"),
        e2e_commands=(("edgeapt-hello", "--help"),),
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

    events: list[RepackageEvent] = []

    result = repackage_project(
        on_event=events.append,
        project=make_project(
            tmp_path,
            fetcher=_FailingFetcher(
                AssertionError("fetcher should not run for cached artifact")
            ),
        ),
    )
    lock = result.lock

    assert lock.artifacts[0].created_at == "2026-07-05T00:00:00Z"
    assert [item.key.suite for item in lock.publications] == [
        "focal",
        "jammy",
        "noble",
        "resolute",
    ]
    assert all(
        claim.commands == (("edgeapt-hello", "--help"),)
        for item in lock.publications
        for claim in item.e2e_claims
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

    with pytest.raises(RuntimeError, match="stop after cache miss"):
        repackage_project(
            on_event=events.append,
            project=make_project(
                tmp_path,
                fetcher=_FailingFetcher(RuntimeError("stop after cache miss")),
            ),
        )

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
        e2e_commands=(("edgeapt-hello",),),
        url="tests/fixtures/hello-world",
    )
    artifact_path = _artifact_path(tmp_path)
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"cached artifact")
    _write_previous_lock(tmp_path, source=previous, artifact_path=artifact_path)
    _patch_environment(monkeypatch, tmp_path, current)

    with pytest.raises(ValidationError, match="build plan changed for DebKey"):
        repackage_project(project=make_project(tmp_path))


def _source(
    *,
    suites: tuple[str, ...],
    e2e_commands: tuple[tuple[str, ...], ...] = (("edgeapt-hello",),),
) -> SourceTemplate:
    return make_source(
        source_id="hello",
        package="edgeapt-hello",
        version="v0.1.0",
        suites=suites,
        url="tests/fixtures/hello-world",
        install_path="/usr/bin/edgeapt-hello",
        e2e_commands=e2e_commands,
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
    source: SourceTemplate,
    artifact_path: Path,
) -> None:
    plan = build_repo_plan((make_document(source),))
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
            e2e_claims=item.e2e_claims,
        )
        for item in plan.publications
    )
    write_lock(
        LockFile(
            schema="edgeapt.lock/v3",
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
    source: SourceTemplate,
) -> None:
    def load_test_sources(*args: object, **kwargs: object):
        return (make_document(source),)

    monkeypatch.setattr(
        "edgeapt.workflows.planning.load_source_documents",
        load_test_sources,
    )


class _FailingFetcher:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def fetch(
        self,
        *,
        url: str,
        sha256: str | None,
        destination: Path,
        root: Path,
    ) -> FetchResult:
        raise self._error

    def prepare_single_binary(
        self,
        *,
        downloaded: Path,
        extract_path: str | None,
        work_dir: Path,
    ) -> Path:
        raise self._error
