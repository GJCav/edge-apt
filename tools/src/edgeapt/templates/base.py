from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import ClassVar, Protocol

import attrs
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from edgeapt.domain.artifacts import DebControlFact, UpstreamFact
from edgeapt.domain.keys import DebKey
from edgeapt.domain.planning import BuildIntent, BuildSpec, SourceProvenance
from edgeapt.templates.common import validate_id, validate_package


@attrs.define(kw_only=True, frozen=True)
class SourceDocument:
    source: SourceTemplate
    source_file: str


@attrs.define(kw_only=True, frozen=True)
class TemplateBuildResult:
    candidate_deb: Path
    upstream_version: str
    upstream: UpstreamFact
    revision: int | None = None


@attrs.define(kw_only=True, frozen=True)
class FetchResult:
    path: Path
    fact: UpstreamFact


class Fetcher(Protocol):
    def fetch(
        self,
        *,
        url: str,
        sha256: str | None,
        destination: Path,
        root: Path,
    ) -> FetchResult: ...

class ArchiveExtractor(Protocol):
    def extract_regular_files(
        self,
        *,
        archive: Path,
        strip_components: int,
        paths: tuple[str, ...],
        destination: Path,
    ) -> Mapping[str, Path]: ...


class DebTools(Protocol):
    def build_package(
        self,
        *,
        payload_root: Path,
        deb_key: DebKey,
        description: str,
        homepage: str | None,
        section: str,
        multi_arch: str | None,
        output: Path,
        work_dir: Path,
    ) -> None: ...

    def read_control(self, path: Path) -> DebControlFact: ...


@attrs.define(kw_only=True, frozen=True)
class BuildContext:
    deb_key: DebKey
    root: Path
    work_dir: Path
    fetcher: Fetcher
    archive_extractor: ArchiveExtractor
    deb_tools: DebTools
    report: Callable[[str, str, str | None], None]


class SourceTemplate(BaseModel, ABC):
    model_config = ConfigDict(extra="forbid", frozen=True)

    template_id: ClassVar[str]

    id: str
    package: str
    e2e_commands: tuple[tuple[str, ...], ...] = Field(min_length=1)
    allow_ubuntu_package_override: bool = False
    override_reason: str | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        validate_id(value)
        return value

    @field_validator("package")
    @classmethod
    def _validate_package(cls, value: str) -> str:
        validate_package(value)
        return value

    @field_validator("e2e_commands")
    @classmethod
    def _validate_e2e_commands(
        cls,
        value: tuple[tuple[str, ...], ...],
    ) -> tuple[tuple[str, ...], ...]:
        if any(not command or any(item == "" for item in command) for command in value):
            raise ValueError(
                "e2e_commands must contain non-empty string arrays"
            )
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def _validate_override(self) -> SourceTemplate:
        if self.allow_ubuntu_package_override and self.override_reason is None:
            raise ValueError(
                "override_reason is required when package override is allowed"
            )
        if not self.allow_ubuntu_package_override and self.override_reason is not None:
            raise ValueError(
                "override_reason is only valid with allow_ubuntu_package_override: true"
            )
        return self

    @abstractmethod
    def plan(
        self,
        provenance: SourceProvenance,
    ) -> tuple[BuildIntent, ...]: ...

    @classmethod
    @abstractmethod
    def build(
        cls,
        spec: BuildSpec,
        context: BuildContext,
    ) -> TemplateBuildResult: ...
