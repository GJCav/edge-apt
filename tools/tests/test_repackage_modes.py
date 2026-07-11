from __future__ import annotations

import json
from pathlib import Path

import pytest

from edgeapt.errors import ValidationError
from edgeapt.infrastructure.fetcher import DefaultFetcher
from edgeapt.util import sha256_file
from edgeapt.workflows.repackage import repackage_project
from tests.factories import make_project


def test_locked_mode_cold_build_preserves_lock(tmp_path: Path) -> None:
    _write_source(tmp_path)
    project = make_project(tmp_path)
    updated = repackage_project(mode="update-lock", project=project)
    artifact = tmp_path / updated.lock.artifacts[0].path
    expected = artifact.read_bytes()
    lock_bytes = project.paths.lock_path.read_bytes()
    artifact.unlink()

    result = repackage_project(mode="locked", project=project)

    assert result.mode == "locked"
    assert artifact.read_bytes() == expected
    assert project.paths.lock_path.read_bytes() == lock_bytes


def test_locked_mode_reuses_valid_cache(tmp_path: Path) -> None:
    _write_source(tmp_path)
    project = make_project(tmp_path)
    repackage_project(mode="update-lock", project=project)

    result = repackage_project(
        mode="locked",
        project=make_project(tmp_path, fetcher=_FailingFetcher()),
    )

    assert result.mode == "locked"


def test_locked_mode_repairs_corrupt_cache(tmp_path: Path) -> None:
    _write_source(tmp_path)
    project = make_project(tmp_path)
    updated = repackage_project(mode="update-lock", project=project)
    artifact = tmp_path / updated.lock.artifacts[0].path
    expected_sha256 = updated.lock.artifacts[0].sha256
    artifact.write_bytes(b"corrupt")

    repackage_project(mode="locked", project=project)

    assert sha256_file(artifact) == expected_sha256


def test_locked_mode_rejects_rebuilt_artifact_mismatch(tmp_path: Path) -> None:
    _write_source(tmp_path)
    project = make_project(tmp_path)
    updated = repackage_project(mode="update-lock", project=project)
    artifact = tmp_path / updated.lock.artifacts[0].path
    raw = json.loads(project.paths.lock_path.read_text(encoding="utf-8"))
    raw["artifacts"][0]["sha256"] = f"sha256:{'0' * 64}"
    project.paths.lock_path.write_text(
        json.dumps(raw, indent=2) + "\n",
        encoding="utf-8",
    )
    artifact.unlink()

    with pytest.raises(ValidationError, match="rebuilt artifact does not match lock"):
        repackage_project(mode="locked", project=project)

    assert not artifact.exists()


def test_locked_mode_rejects_upstream_hash_mismatch(tmp_path: Path) -> None:
    fixture = _write_source(tmp_path)
    project = make_project(tmp_path)
    updated = repackage_project(mode="update-lock", project=project)
    (tmp_path / updated.lock.artifacts[0].path).unlink()
    fixture.write_bytes(b"changed upstream\n")

    with pytest.raises(ValidationError, match="sha256 mismatch"):
        repackage_project(mode="locked", project=project)


def test_locked_mode_rejects_stale_sources(tmp_path: Path) -> None:
    _write_source(tmp_path)
    project = make_project(tmp_path)
    repackage_project(mode="update-lock", project=project)
    source = tmp_path / "sources" / "hello.yaml"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "suites: [noble]",
            "suites: [jammy, noble]",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="sources changed since lock.json"):
        repackage_project(mode="locked", project=project)


def _write_source(root: Path) -> Path:
    fixture = root / "fixture"
    fixture.write_bytes(b"#!/bin/sh\necho hello\n")
    source = root / "sources" / "hello.yaml"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            [
                "template: edgeapt.single_binary/v1",
                "id: hello",
                "package: edgeapt-hello",
                "e2e_commands:",
                "  - [edgeapt-hello]",
                "repackage:",
                "  install_path: /usr/bin/edgeapt-hello",
                "  metadata:",
                "    description: test fixture",
                "upstream:",
                "  - version: v0.1.0",
                "    revision: 1",
                "    arch: amd64",
                "    suites: [noble]",
                "    url: fixture",
                f"    sha256: {sha256_file(fixture)}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return fixture


class _FailingFetcher(DefaultFetcher):
    def fetch(self, **kwargs: object):
        raise AssertionError("fetcher should not run for a valid cache")
