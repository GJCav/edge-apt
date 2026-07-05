from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.errors import ValidationError
from edgeapt.models import SourceConfig
from edgeapt.models import UpstreamConfig
from edgeapt.ubuntu_index import ensure_no_ubuntu_package_conflicts
from edgeapt.ubuntu_index import parse_packages_index
from edgeapt.util import write_json


def test_parse_packages_index() -> None:
    packages = parse_packages_index(
        """
Package: fd
Version: 10.4.1

Package: bat
Version: 0.25.0
"""
    )
    assert packages == frozenset({"fd", "bat"})


def test_conflict_check_requires_cached_index(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="refresh-ubuntu-index"):
        ensure_no_ubuntu_package_conflicts([_source(package="fd")], index_dir=tmp_path)


def test_conflict_check_blocks_unapproved_package(tmp_path: Path) -> None:
    _write_index(tmp_path, packages=["fd"])
    with pytest.raises(ValidationError, match="package 'fd' exists"):
        ensure_no_ubuntu_package_conflicts([_source(package="fd")], index_dir=tmp_path)


def test_conflict_check_allows_explicit_override(tmp_path: Path) -> None:
    _write_index(tmp_path, packages=["fd"])
    ensure_no_ubuntu_package_conflicts(
        [
            _source(
                package="fd",
                allow_ubuntu_package_override=True,
                override_reason="Use upstream release.",
            )
        ],
        index_dir=tmp_path,
    )


def test_conflict_check_ignores_non_conflicting_package(tmp_path: Path) -> None:
    _write_index(tmp_path, packages=["fd"])
    ensure_no_ubuntu_package_conflicts([_source(package="edgeapt-hello")], index_dir=tmp_path)


def _source(
    *,
    package: str,
    allow_ubuntu_package_override: bool = False,
    override_reason: str | None = None,
) -> SourceConfig:
    return SourceConfig(
        template="edgeapt.single_binary/v1",
        id=package.replace(".", "-"),
        package=package,
        source_file=f"sources/{package}.yaml",
        repackage=None,
        upstream=(
            UpstreamConfig(
                version="v0.1.0",
                revision=1,
                arch="amd64",
                suites=("jammy",),
                url="tests/fixtures/hello-world",
            ),
        ),
        allow_ubuntu_package_override=allow_ubuntu_package_override,
        override_reason=override_reason,
    )


def _write_index(tmp_path: Path, *, packages: list[str]) -> None:
    write_json(
        tmp_path / "jammy-amd64.json",
        {
            "suite": "jammy",
            "arch": "amd64",
            "components": ["main", "restricted", "universe", "multiverse"],
            "packages": packages,
            "refreshed_at": "2026-07-05T00:00:00Z",
        },
    )
