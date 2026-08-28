from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from edgeapt.infrastructure.aptly import publish_with_aptly
from tests.factories import make_artifact
from tests.factories import make_lock
from tests.factories import make_project
from tests.factories import make_publication


def test_publish_with_aptly_includes_architecture_all(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    artifact = make_artifact(path="packages/example_1.0-1_all.deb")
    publication = make_publication(deb_key=artifact.deb_key)
    lock = make_lock(artifacts=(artifact,), publications=(publication,))
    package_path = tmp_path / artifact.path
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_bytes(b"deb")
    captured_config: dict[str, Any] = {}
    calls: list[tuple[str, ...]] = []

    def fake_write_json(path: Path, data: dict[str, Any]) -> None:
        assert path == project.paths.tmp_dir / "aptly-test.conf"
        captured_config.update(data)

    def fake_aptly(config: Path, *args: str) -> None:
        assert config == project.paths.tmp_dir / "aptly-test.conf"
        calls.append(args)

    monkeypatch.setattr("edgeapt.infrastructure.aptly.write_json", fake_write_json)
    monkeypatch.setattr("edgeapt.infrastructure.aptly._aptly", fake_aptly)

    publish_with_aptly(
        lock=lock,
        paths=project.paths,
        profile="test",
        output_dir=tmp_path / "public",
        signing_key_fingerprint="ABCD",
    )

    assert captured_config["architectures"] == ["all", "amd64", "arm64"]
    assert (
        "repo",
        "create",
        "-distribution=noble",
        "-component=main",
        "-architectures=all,amd64,arm64",
        "edgeapt-noble-main",
    ) in calls
    assert (
        "publish",
        "snapshot",
        "-batch",
        "-skip-contents",
        "-gpg-key=ABCD",
        "-architectures=all,amd64,arm64",
        "-distribution=noble",
        "-component=main",
        "edgeapt-noble-main-snapshot",
        "filesystem:local:",
    ) in calls
