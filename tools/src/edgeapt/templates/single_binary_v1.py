from __future__ import annotations

import shutil
from typing import Any, ClassVar, Literal, cast

import attrs
from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    validate_suite,
)


class SingleBinaryMetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field(min_length=1)
    homepage: str | None = None


class SingleBinaryRepackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    install_path: str = Field(min_length=1)
    metadata: SingleBinaryMetadataModel

    @field_validator("install_path")
    @classmethod
    def _absolute_install_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("repackage.install_path must be absolute")
        return value


class SingleBinaryUpstreamModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    revision: int = Field(ge=1)
    arch: str
    suites: tuple[str, ...] = Field(min_length=1)
    url: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    extract_path: str | None = None

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


@attrs.define(kw_only=True, frozen=True)
class SingleBinaryMetadataSpec:
    description: str
    homepage: str | None

    def to_canonical_data(self) -> JsonObject:
        data: dict[str, Any] = {"description": self.description}
        if self.homepage is not None:
            data["homepage"] = self.homepage
        return cast(JsonObject, data)


@attrs.define(kw_only=True, frozen=True)
class SingleBinaryRepackageSpec:
    install_path: str
    metadata: SingleBinaryMetadataSpec

    def to_canonical_data(self) -> JsonObject:
        return cast(
            JsonObject,
            {
                "install_path": self.install_path,
                "metadata": self.metadata.to_canonical_data(),
            },
        )


@attrs.define(kw_only=True, frozen=True)
class SingleBinaryBuildSpec:
    upstream_version: str
    revision: int
    fetch: FetchSpec
    extract_path: str | None
    repackage: SingleBinaryRepackageSpec

    @property
    def template_id(self) -> str:
        return SingleBinaryV1.template_id

    def to_canonical_data(self) -> JsonObject:
        data = cast(
            JsonObject,
            {
                "template": self.template_id,
                "upstream_version": self.upstream_version,
                "revision": self.revision,
                "fetch": self.fetch.to_canonical_data(),
                "repackage": self.repackage.to_canonical_data(),
            },
        )
        if self.extract_path is not None:
            data["extract_path"] = self.extract_path
        return data


class SingleBinaryV1(SourceTemplate):
    template_id: ClassVar[str] = "edgeapt.single_binary/v1"

    template: Literal["edgeapt.single_binary/v1"]
    repackage: SingleBinaryRepackageModel
    upstream: tuple[SingleBinaryUpstreamModel, ...] = Field(min_length=1)

    def plan(self, provenance: SourceProvenance) -> tuple[BuildIntent, ...]:
        repackage = SingleBinaryRepackageSpec(
            install_path=self.repackage.install_path,
            metadata=SingleBinaryMetadataSpec(
                description=self.repackage.metadata.description,
                homepage=self.repackage.metadata.homepage,
            ),
        )
        intents: list[BuildIntent] = []
        for index, upstream in enumerate(self.upstream):
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
                    build_spec=SingleBinaryBuildSpec(
                        upstream_version=upstream.version,
                        revision=upstream.revision,
                        fetch=FetchSpec(url=upstream.url, sha256=upstream.sha256),
                        extract_path=upstream.extract_path,
                        repackage=repackage,
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
        if not isinstance(spec, SingleBinaryBuildSpec):
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
        if spec.extract_path is not None:
            context.report("extract_start", spec.extract_path, None)
            extracted = context.archive_extractor.extract_regular_files(
                archive=download.path,
                strip_components=0,
                paths=(spec.extract_path,),
                destination=context.work_dir / "extract",
            )
            binary = extracted[spec.extract_path]
        else:
            binary = download.path
        payload_root = context.work_dir / "payload"
        if payload_root.exists():
            shutil.rmtree(payload_root)
        target = payload_root / spec.repackage.install_path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binary, target)
        target.chmod(0o755)
        candidate = context.work_dir / (
            f"{context.deb_key.package}_{context.deb_key.deb_version}_"
            f"{context.deb_key.arch}.deb"
        )
        context.report("build_start", f"Building {candidate.name}", None)
        context.deb_tools.build_package(
            payload_root=payload_root,
            deb_key=context.deb_key,
            description=spec.repackage.metadata.description,
            homepage=spec.repackage.metadata.homepage,
            output=candidate,
            work_dir=context.work_dir,
        )
        return TemplateBuildResult(
            candidate_deb=candidate,
            upstream_version=spec.upstream_version,
            revision=spec.revision,
            upstream=download.fact,
        )
