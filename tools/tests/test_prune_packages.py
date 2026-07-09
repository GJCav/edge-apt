from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.constants import LOCK_SCHEMA
from edgeapt.models import ArtifactFact
from edgeapt.models import LockFile
from edgeapt.models import SourceLock
from edgeapt.models import UpstreamFact
from edgeapt.repackage import prune_packages
from edgeapt.util import sha256_file


def test_prune_packages_dry_run_keeps_orphans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("edgeapt.repackage.ROOT", tmp_path)
    packages_dir = tmp_path / "packages"
    kept = packages_dir / "hello" / "edgeapt-hello_0.1.0-1_amd64.deb"
    orphan = packages_dir / "jless" / "jless_0.9.0-1_amd64.deb"
    kept.parent.mkdir(parents=True)
    orphan.parent.mkdir(parents=True)
    kept.write_bytes(b"kept")
    orphan.write_bytes(b"orphan")

    result = prune_packages(_lock(path=kept, root=tmp_path), dry_run=True, packages_dir=packages_dir)

    assert result.orphans == (orphan.resolve(),)
    assert result.deleted == ()
    assert orphan.exists()
    assert kept.exists()


def test_prune_packages_deletes_only_orphans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("edgeapt.repackage.ROOT", tmp_path)
    packages_dir = tmp_path / "packages"
    kept = packages_dir / "hello" / "edgeapt-hello_0.1.0-1_amd64.deb"
    orphan = packages_dir / "jless" / "jless_0.9.0-1_amd64.deb"
    kept.parent.mkdir(parents=True)
    orphan.parent.mkdir(parents=True)
    kept.write_bytes(b"kept")
    orphan.write_bytes(b"orphan")

    result = prune_packages(_lock(path=kept, root=tmp_path), dry_run=False, packages_dir=packages_dir)

    assert result.orphans == (orphan.resolve(),)
    assert result.deleted == (orphan.resolve(),)
    assert not orphan.exists()
    assert not orphan.parent.exists()
    assert kept.exists()


def test_prune_packages_handles_missing_packages_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("edgeapt.repackage.ROOT", tmp_path)
    packages_dir = tmp_path / "missing"

    result = prune_packages(_lock(path=tmp_path / "packages" / "missing.deb", root=tmp_path), dry_run=False, packages_dir=packages_dir)

    assert result.orphans == ()
    assert result.deleted == ()


def _lock(*, path: Path, root: Path) -> LockFile:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    return LockFile(
        schema=LOCK_SCHEMA,
        generated_at="2026-07-05T00:00:00Z",
        sources={
            "hello": SourceLock(
                source_file="sources/hello.yaml",
                source_sha256="sha256:source",
                template="edgeapt.single_binary/v1",
                package="edgeapt-hello",
                e2e_command=("edgeapt-hello",),
                artifacts=(
                    ArtifactFact(
                        package="edgeapt-hello",
                        version="0.1.0-1",
                        upstream_version="v0.1.0",
                        revision=1,
                        arch="amd64",
                        suites=("noble",),
                        path=relative,
                        sha256=sha256_file(path) if path.exists() else "sha256:missing",
                        size=path.stat().st_size if path.exists() else 0,
                        upstream=UpstreamFact(
                            url="tests/fixtures/hello-world",
                            sha256="sha256:upstream",
                            size=10,
                        ),
                        created_at="2026-07-05T00:00:00Z",
                    ),
                ),
            ),
        },
    )
