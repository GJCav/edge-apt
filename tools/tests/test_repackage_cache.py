from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.constants import LOCK_SCHEMA
from edgeapt.models import ArtifactFact
from edgeapt.models import LockFile
from edgeapt.models import RepackageConfig
from edgeapt.models import RepackageMetadata
from edgeapt.models import SourceConfig
from edgeapt.models import SourceLock
from edgeapt.models import UpstreamConfig
from edgeapt.models import UpstreamFact
from edgeapt.repackage import repackage_all
from edgeapt.repackage import RepackageEvent
from edgeapt.util import sha256_file
from edgeapt.util import write_json


def test_repackage_reuses_cached_package_without_fetching(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _source(suites=("jammy", "noble"))
    artifact_path = tmp_path / "packages" / "hello" / "edgeapt-hello_0.1.0-1_amd64.deb"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"cached artifact")
    artifact = _artifact(
        path="packages/hello/edgeapt-hello_0.1.0-1_amd64.deb",
        sha256=sha256_file(artifact_path),
        suites=("jammy", "noble"),
    )
    _write_lock(tmp_path / "lock.json", artifact=artifact, source=source)
    _write_source_file(tmp_path, source)
    _patch_repackage_environment(monkeypatch, tmp_path, [source])

    def fail_fetch(*args: object, **kwargs: object) -> object:
        raise AssertionError("fetch_upstream should not run for cached artifact")

    monkeypatch.setattr("edgeapt.repackage.fetch_upstream", fail_fetch)
    events: list[RepackageEvent] = []

    lock = repackage_all(on_event=events.append)

    assert lock.sources["hello"].artifacts[0].sha256 == artifact.sha256
    assert lock.sources["hello"].artifacts[0].created_at == "2026-07-05T00:00:00Z"
    assert any(event.kind == "cache_hit" for event in events)
    assert not any(event.kind == "fetch_start" for event in events)


def test_repackage_cache_hit_updates_suites_from_current_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _source(suites=("focal", "jammy", "noble", "resolute"))
    artifact_path = tmp_path / "packages" / "hello" / "edgeapt-hello_0.1.0-1_amd64.deb"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"cached artifact")
    artifact = _artifact(
        path="packages/hello/edgeapt-hello_0.1.0-1_amd64.deb",
        sha256=sha256_file(artifact_path),
        suites=("jammy", "noble"),
    )
    _write_lock(tmp_path / "lock.json", artifact=artifact, source=source)
    _write_source_file(tmp_path, source)
    _patch_repackage_environment(monkeypatch, tmp_path, [source])

    def fail_fetch(*args: object, **kwargs: object) -> object:
        pytest.fail("fetch_upstream should not run")

    monkeypatch.setattr("edgeapt.repackage.fetch_upstream", fail_fetch)

    lock = repackage_all()

    assert lock.sources["hello"].artifacts[0].suites == (
        "focal",
        "jammy",
        "noble",
        "resolute",
    )


def test_repackage_cache_hit_allows_e2e_command_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _source(suites=("jammy", "noble"), e2e_command=("edgeapt-hello", "--help"))
    artifact_path = tmp_path / "packages" / "hello" / "edgeapt-hello_0.1.0-1_amd64.deb"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"cached artifact")
    artifact = _artifact(
        path="packages/hello/edgeapt-hello_0.1.0-1_amd64.deb",
        sha256=sha256_file(artifact_path),
        suites=("jammy", "noble"),
    )
    _write_lock(
        tmp_path / "lock.json",
        artifact=artifact,
        source=_source(suites=("jammy", "noble"), e2e_command=("edgeapt-hello",)),
    )
    _write_source_file(tmp_path, source)
    _patch_repackage_environment(monkeypatch, tmp_path, [source])

    def fail_fetch(*args: object, **kwargs: object) -> object:
        pytest.fail("fetch_upstream should not run")

    monkeypatch.setattr("edgeapt.repackage.fetch_upstream", fail_fetch)
    events: list[RepackageEvent] = []

    lock = repackage_all(on_event=events.append)

    assert any(event.kind == "cache_hit" for event in events)
    assert lock.sources["hello"].e2e_command == ("edgeapt-hello", "--help")


def test_repackage_cache_miss_when_artifact_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _source(suites=("jammy", "noble"))
    artifact = _artifact(
        path="packages/hello/edgeapt-hello_0.1.0-1_amd64.deb",
        sha256="sha256:missing",
        suites=("jammy", "noble"),
    )
    _write_lock(tmp_path / "lock.json", artifact=artifact, source=source)
    _write_source_file(tmp_path, source)
    _patch_repackage_environment(monkeypatch, tmp_path, [source])
    events: list[RepackageEvent] = []

    def stop_after_cache_miss(*args: object, **kwargs: object) -> object:
        raise RuntimeError("stop after cache miss")

    monkeypatch.setattr("edgeapt.repackage.fetch_upstream", stop_after_cache_miss)

    with pytest.raises(RuntimeError, match="stop after cache miss"):
        repackage_all(on_event=events.append)

    assert any(
        event.kind == "cache_miss" and event.message == "miss - artifact missing"
        for event in events
    )
    assert any(event.kind == "fetch_start" for event in events)


def test_repackage_cache_miss_when_upstream_sha_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = _source(suites=("jammy", "noble"), upstream_sha256="sha256:new")
    artifact_path = tmp_path / "packages" / "hello" / "edgeapt-hello_0.1.0-1_amd64.deb"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"cached artifact")
    artifact = _artifact(
        path="packages/hello/edgeapt-hello_0.1.0-1_amd64.deb",
        sha256=sha256_file(artifact_path),
        suites=("jammy", "noble"),
        upstream_sha256="sha256:old",
    )
    _write_lock(tmp_path / "lock.json", artifact=artifact, source=source)
    _write_source_file(tmp_path, source)
    _patch_repackage_environment(monkeypatch, tmp_path, [source])
    events: list[RepackageEvent] = []

    def stop_after_cache_miss(*args: object, **kwargs: object) -> object:
        raise RuntimeError("stop after cache miss")

    monkeypatch.setattr("edgeapt.repackage.fetch_upstream", stop_after_cache_miss)

    with pytest.raises(RuntimeError, match="stop after cache miss"):
        repackage_all(on_event=events.append)

    assert any(
        event.kind == "cache_miss" and event.message == "miss - artifact not in previous lock"
        for event in events
    )


def _source(
    *,
    suites: tuple[str, ...],
    upstream_sha256: str | None = None,
    e2e_command: tuple[str, ...] = ("edgeapt-hello",),
) -> SourceConfig:
    return SourceConfig(
        template="edgeapt.single_binary/v1",
        id="hello",
        package="edgeapt-hello",
        e2e_command=e2e_command,
        source_file="sources/hello.yaml",
        repackage=RepackageConfig(
            type="nfpm",
            install_path="/usr/bin/edgeapt-hello",
            metadata=RepackageMetadata(description="hello"),
        ),
        upstream=(
            UpstreamConfig(
                version="v0.1.0",
                revision=1,
                arch="amd64",
                suites=suites,
                url="tests/fixtures/hello-world",
                sha256=upstream_sha256,
            ),
        ),
    )


def _artifact(
    *,
    path: str,
    sha256: str,
    suites: tuple[str, ...],
    upstream_sha256: str = "sha256:upstream",
) -> ArtifactFact:
    return ArtifactFact(
        package="edgeapt-hello",
        version="0.1.0-1",
        upstream_version="v0.1.0",
        revision=1,
        arch="amd64",
        suites=suites,
        path=path,
        sha256=sha256,
        size=15,
        upstream=UpstreamFact(
            url="tests/fixtures/hello-world",
            sha256=upstream_sha256,
            size=10,
        ),
        created_at="2026-07-05T00:00:00Z",
    )


def _write_lock(path: Path, *, artifact: ArtifactFact, source: SourceConfig) -> None:
    write_json(
        path,
        LockFile(
            schema=LOCK_SCHEMA,
            generated_at="2026-07-05T00:00:00Z",
            sources={
                source.id: SourceLock(
                    source_file=source.source_file,
                    source_sha256="sha256:source",
                    template=source.template,
                    package=source.package,
                    e2e_command=source.e2e_command,
                    artifacts=(artifact,),
                )
            },
        ).to_json(),
    )


def _write_source_file(tmp_path: Path, source: SourceConfig) -> None:
    source_path = tmp_path / source.source_file
    source_path.parent.mkdir(parents=True)
    source_path.write_text("source fixture\n", encoding="utf-8")


def _patch_repackage_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sources: list[SourceConfig],
) -> None:
    monkeypatch.setattr("edgeapt.repackage.ROOT", tmp_path)
    monkeypatch.setattr("edgeapt.repackage.LOCK_PATH", tmp_path / "lock.json")
    monkeypatch.setattr("edgeapt.repackage.TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr("edgeapt.repackage.load_sources", lambda: tuple(sources))
