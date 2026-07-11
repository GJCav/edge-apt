from __future__ import annotations

import gzip
import shutil
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar, Literal, cast

import attrs
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from edgeapt.domain.keys import DebKey
from edgeapt.domain.planning import (
    BuildIntent,
    BuildSpec,
    JsonObject,
    SourceProvenance,
)
from edgeapt.templates.base import BuildContext, SourceTemplate, TemplateBuildResult
from edgeapt.templates.common import (
    FetchSpec,
    normalize_debian_version,
    validate_arch,
    validate_id,
    validate_suite,
)

type FileMode = Literal["0644", "0755"]
type FileTransform = Literal["copy", "gzip"]


class PrebuiltArchiveMetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field(min_length=1)
    homepage: str | None = None


class PrebuiltArchiveFileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    path: str
    destination: str
    mode: FileMode
    transform: FileTransform = "copy"

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        validate_id(value)
        return value

    @field_validator("path")
    @classmethod
    def _valid_path(cls, value: str) -> str:
        _validate_relative_path(value)
        return value

    @field_validator("destination")
    @classmethod
    def _valid_destination(cls, value: str) -> str:
        _validate_destination(value)
        return value

    @model_validator(mode="after")
    def _valid_transform_destination(self) -> PrebuiltArchiveFileModel:
        if self.transform == "gzip" and not self.destination.endswith(".gz"):
            raise ValueError("gzip destination must end with .gz")
        return self


class PrebuiltArchiveUpstreamModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    revision: int = Field(ge=1)
    arch: str
    suites: tuple[str, ...] = Field(min_length=1)
    url: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    strip_components: int = Field(ge=0)
    path_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("arch")
    @classmethod
    def _supported_arch(cls, value: str) -> str:
        validate_arch(value)
        return value

    @field_validator("suites")
    @classmethod
    def _supported_suites(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for suite in value:
            validate_suite(suite)
        return value

    @field_validator("path_overrides")
    @classmethod
    def _valid_override_paths(cls, value: dict[str, str]) -> dict[str, str]:
        for path in value.values():
            _validate_relative_path(path)
        return value


@attrs.define(kw_only=True, frozen=True)
class PrebuiltArchiveMetadataSpec:
    description: str
    homepage: str | None

    def to_canonical_data(self) -> JsonObject:
        data: dict[str, Any] = {"description": self.description}
        if self.homepage is not None:
            data["homepage"] = self.homepage
        return cast(JsonObject, data)


@attrs.define(kw_only=True, frozen=True, order=True)
class ResolvedArchiveFile:
    path: str
    destination: str
    mode: FileMode
    transform: FileTransform

    def to_canonical_data(self) -> JsonObject:
        return cast(
            JsonObject,
            {
                "path": self.path,
                "destination": self.destination,
                "mode": self.mode,
                "transform": self.transform,
            },
        )


@attrs.define(kw_only=True, frozen=True)
class PrebuiltArchiveBuildSpec:
    upstream_version: str
    revision: int
    fetch: FetchSpec
    strip_components: int
    metadata: PrebuiltArchiveMetadataSpec
    files: tuple[ResolvedArchiveFile, ...]

    @property
    def template_id(self) -> str:
        return PrebuiltArchiveV1.template_id

    def to_canonical_data(self) -> JsonObject:
        return cast(
            JsonObject,
            {
                "template": self.template_id,
                "upstream_version": self.upstream_version,
                "revision": self.revision,
                "fetch": self.fetch.to_canonical_data(),
                "strip_components": self.strip_components,
                "metadata": self.metadata.to_canonical_data(),
                "files": [item.to_canonical_data() for item in self.files],
            },
        )


class PrebuiltArchiveV1(SourceTemplate):
    template_id: ClassVar[str] = "edgeapt.prebuilt_archive/v1"

    template: Literal["edgeapt.prebuilt_archive/v1"]
    metadata: PrebuiltArchiveMetadataModel
    files: tuple[PrebuiltArchiveFileModel, ...] = Field(min_length=1)
    upstream: tuple[PrebuiltArchiveUpstreamModel, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _valid_file_set(self) -> PrebuiltArchiveV1:
        ids = [item.id for item in self.files]
        duplicates = sorted(
            item_id for item_id, count in Counter(ids).items() if count > 1
        )
        if duplicates:
            raise ValueError(f"duplicate file id(s): {', '.join(duplicates)}")
        _validate_destination_conflicts(self.files)
        known_ids = set(ids)
        for index, upstream in enumerate(self.upstream):
            unknown = sorted(set(upstream.path_overrides) - known_ids)
            if unknown:
                raise ValueError(
                    f"upstream[{index}] path_overrides contain unknown id(s): "
                    f"{', '.join(unknown)}"
                )
        return self

    def plan(self, provenance: SourceProvenance) -> tuple[BuildIntent, ...]:
        metadata = PrebuiltArchiveMetadataSpec(
            description=self.metadata.description,
            homepage=self.metadata.homepage,
        )
        intents: list[BuildIntent] = []
        for index, upstream in enumerate(self.upstream):
            files = tuple(
                sorted(
                    (
                        ResolvedArchiveFile(
                            path=upstream.path_overrides.get(item.id, item.path),
                            destination=item.destination,
                            mode=item.mode,
                            transform=item.transform,
                        )
                        for item in self.files
                    ),
                    key=lambda item: (
                        item.destination,
                        item.path,
                        item.mode,
                        item.transform,
                    ),
                )
            )
            deb_version = (
                f"{normalize_debian_version(upstream.version)}-{upstream.revision}"
            )
            intents.append(
                BuildIntent(
                    deb_key=DebKey(
                        package=self.package,
                        deb_version=deb_version,
                        arch=upstream.arch,
                    ),
                    suites=upstream.suites,
                    build_spec=PrebuiltArchiveBuildSpec(
                        upstream_version=upstream.version,
                        revision=upstream.revision,
                        fetch=FetchSpec(url=upstream.url, sha256=upstream.sha256),
                        strip_components=upstream.strip_components,
                        metadata=metadata,
                        files=files,
                    ),
                    provenance=attrs.evolve(provenance, upstream_index=index),
                    e2e_commands=self.e2e_commands,
                    allow_ubuntu_package_override=self.allow_ubuntu_package_override,
                    override_reason=self.override_reason,
                )
            )
        return tuple(intents)

    @classmethod
    def build(
        cls,
        spec: BuildSpec,
        context: BuildContext,
    ) -> TemplateBuildResult:
        if not isinstance(spec, PrebuiltArchiveBuildSpec):
            raise TypeError(
                f"{cls.template_id} cannot build {type(spec).__name__}"
            )
        context.report(
            "fetch_start",
            f"Fetching {context.deb_key.package} {spec.upstream_version} "
            f"{context.deb_key.arch}",
            spec.fetch.url,
        )
        download = context.fetcher.fetch(
            url=spec.fetch.url,
            sha256=spec.fetch.sha256,
            destination=context.work_dir / "upstream",
            root=context.root,
        )
        context.report(
            "extract_start",
            f"Extracting {len(spec.files)} archive file(s)",
            None,
        )
        extracted = context.archive_extractor.extract_regular_files(
            archive=download.path,
            strip_components=spec.strip_components,
            paths=tuple(item.path for item in spec.files),
            destination=context.work_dir / "extract",
        )
        payload_root = context.work_dir / "payload"
        if payload_root.exists():
            shutil.rmtree(payload_root)
        for item in spec.files:
            target = payload_root / item.destination.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.transform == "copy":
                shutil.copyfile(extracted[item.path], target)
            else:
                _write_deterministic_gzip(extracted[item.path], target)
            target.chmod(int(item.mode, 8))

        candidate = context.work_dir / (
            f"{context.deb_key.package}_{context.deb_key.deb_version}_"
            f"{context.deb_key.arch}.deb"
        )
        context.report("build_start", f"Building {candidate.name}", None)
        context.deb_tools.build_package(
            payload_root=payload_root,
            deb_key=context.deb_key,
            description=spec.metadata.description,
            homepage=spec.metadata.homepage,
            output=candidate,
            work_dir=context.work_dir,
        )
        return TemplateBuildResult(
            candidate_deb=candidate,
            upstream_version=spec.upstream_version,
            revision=spec.revision,
            upstream=download.fact,
        )


def _write_deterministic_gzip(source: Path, destination: Path) -> None:
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=output_file,
            mtime=0,
        ) as compressed:
            shutil.copyfileobj(input_file, compressed)


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        value == ""
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"path must be normalized and relative: {value}")


def _validate_destination(value: str) -> None:
    path = PurePosixPath(value)
    if not path.is_absolute() or path.as_posix() != value or ".." in path.parts:
        raise ValueError(f"destination must be normalized and absolute: {value}")
    if len(path.parts) < 2 or path.parts[1] != "usr":
        raise ValueError("destination must be under /usr")


def _validate_destination_conflicts(
    files: tuple[PrebuiltArchiveFileModel, ...],
) -> None:
    destinations = [PurePosixPath(item.destination) for item in files]
    if len(set(destinations)) != len(destinations):
        raise ValueError("file destinations must be unique")
    for index, first in enumerate(destinations):
        for second in destinations[index + 1 :]:
            if _is_parent(first, second) or _is_parent(second, first):
                raise ValueError(
                    f"file destination parent conflict: {first} and {second}"
                )


def _is_parent(parent: PurePosixPath, child: PurePosixPath) -> bool:
    return (
        len(parent.parts) < len(child.parts)
        and child.parts[: len(parent.parts)] == parent.parts
    )
