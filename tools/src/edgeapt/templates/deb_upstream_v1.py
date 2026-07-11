from __future__ import annotations

from typing import ClassVar, Literal, cast

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


class DebUpstreamModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    arch: str
    suites: tuple[str, ...] = Field(min_length=1)
    url: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("version")
    @classmethod
    def _valid_version(cls, value: str) -> str:
        if normalize_debian_version(value) != value:
            raise ValueError("deb_upstream version must be a Debian Version")
        return value

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
class DebUpstreamBuildSpec:
    upstream_version: str
    fetch: FetchSpec

    @property
    def template_id(self) -> str:
        return DebUpstreamV1.template_id

    def to_canonical_data(self) -> JsonObject:
        return cast(
            JsonObject,
            {
                "template": self.template_id,
                "upstream_version": self.upstream_version,
                "fetch": self.fetch.to_canonical_data(),
            },
        )


class DebUpstreamV1(SourceTemplate):
    template_id: ClassVar[str] = "edgeapt.deb_upstream/v1"

    template: Literal["edgeapt.deb_upstream/v1"]
    upstream: tuple[DebUpstreamModel, ...] = Field(min_length=1)

    def plan(self, provenance: SourceProvenance) -> tuple[BuildIntent, ...]:
        intents: list[BuildIntent] = []
        for index, upstream in enumerate(self.upstream):
            intents.append(
                BuildIntent(
                    deb_key=DebKey(
                        package=self.package,
                        deb_version=upstream.version,
                        arch=upstream.arch,
                    ),
                    suites=upstream.suites,
                    build_spec=DebUpstreamBuildSpec(
                        upstream_version=upstream.version,
                        fetch=FetchSpec(url=upstream.url, sha256=upstream.sha256),
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
        if not isinstance(spec, DebUpstreamBuildSpec):
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
        return TemplateBuildResult(
            candidate_deb=download.path,
            upstream_version=spec.upstream_version,
            upstream=download.fact,
        )
