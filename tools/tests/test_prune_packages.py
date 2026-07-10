from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.repackage import prune_packages
from edgeapt.models import LockFile
from edgeapt.util import sha256_file
from tests.factories import make_artifact
from tests.factories import make_lock


def test_prune_packages_dry_run_keeps_orphans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packages_dir, kept, orphan = _package_files(tmp_path)
    monkeypatch.setattr("edgeapt.repackage.ROOT", tmp_path)

    result = prune_packages(
        _lock_for(kept, tmp_path),
        dry_run=True,
        packages_dir=packages_dir,
    )

    assert result.orphans == (orphan.resolve(),)
    assert result.deleted == ()
    assert orphan.exists()


def test_prune_packages_deletes_only_orphans(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packages_dir, kept, orphan = _package_files(tmp_path)
    monkeypatch.setattr("edgeapt.repackage.ROOT", tmp_path)

    result = prune_packages(
        _lock_for(kept, tmp_path),
        dry_run=False,
        packages_dir=packages_dir,
    )

    assert result.deleted == (orphan.resolve(),)
    assert not orphan.exists()
    assert kept.exists()


def test_prune_packages_handles_missing_packages_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("edgeapt.repackage.ROOT", tmp_path)
    lock = _lock_for(tmp_path / "packages" / "missing.deb", tmp_path)

    result = prune_packages(lock, dry_run=False, packages_dir=tmp_path / "missing")

    assert result.orphans == ()
    assert result.deleted == ()


def _package_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    packages_dir = tmp_path / "packages"
    kept = packages_dir / "edgeapt-hello" / "edgeapt-hello_0.1.0-1_amd64.deb"
    orphan = packages_dir / "jless" / "jless_0.9.0-1_amd64.deb"
    kept.parent.mkdir(parents=True)
    orphan.parent.mkdir(parents=True)
    kept.write_bytes(b"kept")
    orphan.write_bytes(b"orphan")
    return packages_dir, kept, orphan


def _lock_for(path: Path, root: Path) -> LockFile:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    artifact = make_artifact(
        path=relative,
        sha256=sha256_file(path) if path.exists() else "sha256:missing",
        size=path.stat().st_size if path.exists() else 0,
    )
    return make_lock(artifacts=(artifact,))
