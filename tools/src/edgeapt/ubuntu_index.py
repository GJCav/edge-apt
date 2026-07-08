from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import attrs

from edgeapt.constants import (
    SUPPORTED_SUITES,
    UBUNTU_COMPONENTS,
    UBUNTU_INDEX_ARCHES,
    UBUNTU_INDEX_DIR,
)
from edgeapt.errors import ValidationError
from edgeapt.models import SourceConfig
from edgeapt.util import write_json

UBUNTU_ARCHIVE_BASE_URL = "http://archive.ubuntu.com/ubuntu"


@attrs.define(kw_only=True, frozen=True)
class UbuntuPackageIndex:
    suite: str
    arch: str
    components: tuple[str, ...]
    packages: frozenset[str]
    refreshed_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "arch": self.arch,
            "components": list(self.components),
            "packages": sorted(self.packages),
            "refreshed_at": self.refreshed_at,
        }


@attrs.define(kw_only=True, frozen=True)
class PackageConflict:
    source_id: str
    source_file: str
    package: str
    suite: str


def refresh_ubuntu_indexes(
    *,
    suites: Iterable[str] = SUPPORTED_SUITES,
    arches: Iterable[str] = UBUNTU_INDEX_ARCHES,
    components: Iterable[str] = UBUNTU_COMPONENTS,
    index_dir: Path = UBUNTU_INDEX_DIR,
) -> tuple[UbuntuPackageIndex, ...]:
    refreshed: list[UbuntuPackageIndex] = []
    component_tuple = tuple(components)
    for suite in sorted(suites):
        for arch in sorted(arches):
            refreshed.append(
                refresh_ubuntu_index(
                    suite=suite,
                    arch=arch,
                    components=component_tuple,
                    index_dir=index_dir,
                )
            )
    return tuple(refreshed)


def refresh_ubuntu_index(
    *,
    suite: str,
    arch: str,
    components: tuple[str, ...] = UBUNTU_COMPONENTS,
    index_dir: Path = UBUNTU_INDEX_DIR,
) -> UbuntuPackageIndex:
    packages: set[str] = set()
    for component in components:
        packages.update(
            _download_component_packages(suite=suite, arch=arch, component=component)
        )
    index = UbuntuPackageIndex(
        suite=suite,
        arch=arch,
        components=components,
        packages=frozenset(packages),
        refreshed_at=datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    )
    write_json(_index_path(index_dir=index_dir, suite=suite, arch=arch), index.to_json())
    return index


def load_ubuntu_index(
    *,
    suite: str,
    arch: str,
    index_dir: Path = UBUNTU_INDEX_DIR,
) -> UbuntuPackageIndex:
    path = _index_path(index_dir=index_dir, suite=suite, arch=arch)
    if not path.exists():
        raise ValidationError(
            f"Ubuntu package index missing for {suite}/{arch}; "
            "run `uv run refresh-ubuntu-index` or pass `--skip-ubuntu-conflicts`"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValidationError(f"invalid Ubuntu index format: {path}")
    data = cast(dict[str, Any], raw)
    packages_raw = data.get("packages")
    components_raw = data.get("components")
    if not isinstance(packages_raw, list):
        raise ValidationError(f"invalid package list in Ubuntu index: {path}")
    package_items = cast(list[object], packages_raw)
    if not all(isinstance(item, str) for item in package_items):
        raise ValidationError(f"invalid package list in Ubuntu index: {path}")
    if not isinstance(components_raw, list):
        raise ValidationError(f"invalid component list in Ubuntu index: {path}")
    component_items = cast(list[object], components_raw)
    if not all(isinstance(item, str) for item in component_items):
        raise ValidationError(f"invalid component list in Ubuntu index: {path}")
    return UbuntuPackageIndex(
        suite=_expect_str(data, "suite", path),
        arch=_expect_str(data, "arch", path),
        components=tuple(cast(list[str], component_items)),
        packages=frozenset(cast(list[str], package_items)),
        refreshed_at=_expect_str(data, "refreshed_at", path),
    )


def find_ubuntu_package_conflicts(
    sources: Iterable[SourceConfig],
    *,
    index_dir: Path = UBUNTU_INDEX_DIR,
) -> tuple[PackageConflict, ...]:
    indexes: dict[tuple[str, str], UbuntuPackageIndex] = {}
    conflicts: list[PackageConflict] = []
    for source in sources:
        source_suites = sorted(
            {suite for upstream in source.upstream for suite in upstream.suites}
        )
        for suite in source_suites:
            key = (suite, "amd64")
            index = indexes.get(key)
            if index is None:
                index = load_ubuntu_index(suite=suite, arch="amd64", index_dir=index_dir)
                indexes[key] = index
            if source.package in index.packages and not source.allow_ubuntu_package_override:
                conflicts.append(
                    PackageConflict(
                        source_id=source.id,
                        source_file=source.source_file,
                        package=source.package,
                        suite=suite,
                    )
                )
    return tuple(sorted(conflicts, key=lambda item: (item.source_id, item.suite)))


def ensure_no_ubuntu_package_conflicts(
    sources: Iterable[SourceConfig],
    *,
    index_dir: Path = UBUNTU_INDEX_DIR,
) -> None:
    conflicts = find_ubuntu_package_conflicts(sources, index_dir=index_dir)
    if not conflicts:
        return
    lines = [
        "Ubuntu package name conflict(s) found:",
        *[
            f"- {conflict.source_file}: package {conflict.package!r} "
            f"exists in Ubuntu {conflict.suite}"
            for conflict in conflicts
        ],
        "Set allow_ubuntu_package_override: true with override_reason to accept this.",
    ]
    raise ValidationError("\n".join(lines))


def parse_packages_index(text: str) -> frozenset[str]:
    packages: set[str] = set()
    for line in text.splitlines():
        if line.startswith("Package: "):
            package = line.removeprefix("Package: ").strip()
            if package:
                packages.add(package)
    return frozenset(packages)


def _download_component_packages(*, suite: str, arch: str, component: str) -> frozenset[str]:
    url = (
        f"{UBUNTU_ARCHIVE_BASE_URL}/dists/{suite}/{component}/"
        f"binary-{arch}/Packages.gz"
    )
    try:
        with urllib.request.urlopen(url) as response:
            compressed = response.read()
    except urllib.error.URLError as exc:
        raise ValidationError(f"failed to download Ubuntu package index: {url}: {exc}") from exc
    return parse_packages_index(gzip.decompress(compressed).decode("utf-8", errors="replace"))


def _index_path(*, index_dir: Path, suite: str, arch: str) -> Path:
    return index_dir / f"{suite}-{arch}.json"


def _expect_str(data: Mapping[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValidationError(f"invalid {key} in Ubuntu index: {path}")
    return value
