from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from edgeapt.templates.base import DebTools, Fetcher
    from edgeapt.templates.registry import TemplateRegistry


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def packages_dir(self) -> Path:
        return self.root / "packages"

    @property
    def public_dir(self) -> Path:
        return self.root / "public"

    @property
    def tmp_dir(self) -> Path:
        return self.root / "tmp"

    @property
    def test_public_dir(self) -> Path:
        return self.tmp_dir / "public-test"

    @property
    def lock_path(self) -> Path:
        return self.root / "lock.json"

    @property
    def ubuntu_index_dir(self) -> Path:
        return self.tmp_dir / "ubuntu-index"


@dataclass(frozen=True)
class EdgeAptProject:
    paths: ProjectPaths
    templates: TemplateRegistry
    fetcher: Fetcher
    deb_tools: DebTools


def create_project(root: Path) -> EdgeAptProject:
    from edgeapt.infrastructure.deb import DefaultDebTools
    from edgeapt.infrastructure.fetcher import DefaultFetcher
    from edgeapt.templates.registry import DEFAULT_TEMPLATES

    return EdgeAptProject(
        paths=ProjectPaths(root),
        templates=DEFAULT_TEMPLATES,
        fetcher=DefaultFetcher(),
        deb_tools=DefaultDebTools(),
    )
