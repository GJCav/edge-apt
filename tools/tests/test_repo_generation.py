from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from edgeapt.constants import PUBLIC_DIR, TEST_PUBLIC_DIR
from edgeapt.errors import ValidationError
from edgeapt.keyring import ensure_test_key
from edgeapt.repo import check_static_asset_size_limit
from edgeapt.repo import generate_repo
from edgeapt.repackage import repackage_all
from edgeapt.util import run


@pytest.mark.integration
def test_generate_repo_writes_signed_metadata() -> None:
    ensure_test_key()
    repackage_all()
    if PUBLIC_DIR.exists():
        shutil.rmtree(PUBLIC_DIR)
    result = generate_repo(profile="test")
    assert result.output_dir == TEST_PUBLIC_DIR

    inrelease = TEST_PUBLIC_DIR / "dists" / "noble" / "InRelease"
    release = TEST_PUBLIC_DIR / "dists" / "noble" / "Release"
    release_gpg = TEST_PUBLIC_DIR / "dists" / "noble" / "Release.gpg"

    assert inrelease.exists()
    assert release.exists()
    assert release_gpg.exists()
    assert not PUBLIC_DIR.exists()
    run(["gpg", "--verify", inrelease])


def test_prod_requires_explicit_real_key() -> None:
    with pytest.raises(ValidationError):
        generate_repo(profile="prod")


def test_static_asset_size_limit_allows_exact_limit(tmp_path: Path) -> None:
    path = tmp_path / "asset.deb"
    path.write_bytes(b"0" * 10)
    check_static_asset_size_limit(tmp_path, limit_bytes=10)


def test_static_asset_size_limit_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "asset.deb"
    path.write_bytes(b"0" * 11)

    with pytest.raises(ValidationError, match="asset.deb"):
        check_static_asset_size_limit(tmp_path, limit_bytes=10)


def test_static_asset_size_limit_reports_nested_multiple_files(tmp_path: Path) -> None:
    nested = tmp_path / "pool" / "main"
    nested.mkdir(parents=True)
    first = nested / "first.deb"
    second = tmp_path / "Release"
    first.write_bytes(b"0" * 11)
    second.write_bytes(b"0" * 12)

    with pytest.raises(ValidationError) as exc_info:
        check_static_asset_size_limit(tmp_path, limit_bytes=10)

    message = str(exc_info.value)
    assert "first.deb" in message
    assert "Release" in message
