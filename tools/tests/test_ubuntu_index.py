from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.errors import ValidationError
from edgeapt.domain.planning import Publication
from edgeapt.infrastructure.ubuntu_index import ensure_no_ubuntu_package_conflicts
from edgeapt.infrastructure.ubuntu_index import load_ubuntu_index
from edgeapt.infrastructure.ubuntu_index import parse_packages_index
from edgeapt.infrastructure.ubuntu_index import refresh_ubuntu_index
from edgeapt.infrastructure.ubuntu_index import UbuntuIndexRefreshEvent
from edgeapt.workflows.planning import build_repo_plan
from edgeapt.util import write_json
from tests.factories import make_source
from tests.factories import make_document


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


def test_refresh_ubuntu_index_reports_component_downloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[UbuntuIndexRefreshEvent] = []

    def fake_download_component_packages(
        *,
        suite: str,
        arch: str,
        component: str,
        base_url: str,
        on_download_start: object,
    ) -> frozenset[str]:
        assert callable(on_download_start)
        assert base_url == "https://mirror.example.test/ubuntu"
        on_download_start(
            UbuntuIndexRefreshEvent(
                suite=suite,
                arch=arch,
                component=component,
                url=f"{base_url}/dists/{suite}/{component}/binary-{arch}/Packages.gz",
            )
        )
        return frozenset({component})

    monkeypatch.setattr(
        "edgeapt.infrastructure.ubuntu_index._download_component_packages",
        fake_download_component_packages,
    )

    index = refresh_ubuntu_index(
        suite="jammy",
        arch="amd64",
        components=("main", "universe"),
        index_dir=tmp_path,
        base_url="https://mirror.example.test/ubuntu",
        on_download_start=events.append,
    )

    assert index.packages == frozenset({"main", "universe"})
    assert index.base_url == "https://mirror.example.test/ubuntu"
    assert [(event.suite, event.arch, event.component) for event in events] == [
        ("jammy", "amd64", "main"),
        ("jammy", "amd64", "universe"),
    ]
    assert [event.url for event in events] == [
        "https://mirror.example.test/ubuntu/dists/jammy/main/binary-amd64/Packages.gz",
        "https://mirror.example.test/ubuntu/dists/jammy/universe/binary-amd64/Packages.gz",
    ]
    cached = load_ubuntu_index(suite="jammy", arch="amd64", index_dir=tmp_path)
    assert cached.base_url == "https://mirror.example.test/ubuntu"


def test_conflict_check_requires_cached_index(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="refresh-ubuntu-index"):
        ensure_no_ubuntu_package_conflicts(_publications(package="fd"), index_dir=tmp_path)


def test_conflict_check_blocks_unapproved_package(tmp_path: Path) -> None:
    _write_index(tmp_path, packages=["fd"])
    with pytest.raises(ValidationError, match="package 'fd' exists"):
        ensure_no_ubuntu_package_conflicts(_publications(package="fd"), index_dir=tmp_path)


def test_conflict_check_allows_explicit_override(tmp_path: Path) -> None:
    _write_index(tmp_path, packages=["fd"])
    ensure_no_ubuntu_package_conflicts(
        _publications(
            package="fd",
            allow_ubuntu_package_override=True,
            override_reason="Use upstream release.",
        ),
        index_dir=tmp_path,
    )


def test_conflict_check_ignores_non_conflicting_package(tmp_path: Path) -> None:
    _write_index(tmp_path, packages=["fd"])
    ensure_no_ubuntu_package_conflicts(
        _publications(package="edgeapt-hello"),
        index_dir=tmp_path,
    )


def test_load_ubuntu_index_allows_legacy_cache_without_base_url(tmp_path: Path) -> None:
    _write_index(tmp_path, packages=["fd"])

    index = load_ubuntu_index(suite="jammy", arch="amd64", index_dir=tmp_path)

    assert index.base_url is None


def _publications(
    *,
    package: str,
    allow_ubuntu_package_override: bool = False,
    override_reason: str | None = None,
) -> tuple[Publication, ...]:
    source = make_source(
        source_id=package.replace(".", "-"),
        package=package,
        template="edgeapt.deb_upstream/v1",
        version="0.1.0",
        revision=None,
        suites=("jammy",),
        e2e_command=(package,),
        allow_ubuntu_package_override=allow_ubuntu_package_override,
        override_reason=override_reason,
    )
    return build_repo_plan((make_document(source),)).publications


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
