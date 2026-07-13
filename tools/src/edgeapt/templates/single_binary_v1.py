from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from edgeapt.domain.planning import BuildIntent, BuildSpec, SourceProvenance
from edgeapt.templates.base import BuildContext, SourceTemplate, TemplateBuildResult
from edgeapt.templates.common import (
    DebPackageMetadataModel,
    DebPackageMetadataSpec,
    validate_arch,
    validate_suite,
)
from edgeapt.templates.single_binary import (
    SingleBinaryRepackageSpec,
    build_single_binary,
    plan_single_binary,
)


class SingleBinaryRepackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    install_path: str = Field(min_length=1)
    metadata: DebPackageMetadataModel

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


class SingleBinaryV1(SourceTemplate):
    template_id: ClassVar[str] = "edgeapt.single_binary/v1"

    template: Literal["edgeapt.single_binary/v1"]
    repackage: SingleBinaryRepackageModel
    upstream: tuple[SingleBinaryUpstreamModel, ...] = Field(min_length=1)

    def plan(self, provenance: SourceProvenance) -> tuple[BuildIntent, ...]:
        return plan_single_binary(
            template_id=self.template_id,
            package=self.package,
            upstreams=self.upstream,
            repackage=SingleBinaryRepackageSpec(
                install_path=self.repackage.install_path,
                metadata=DebPackageMetadataSpec.from_model(self.repackage.metadata),
            ),
            provenance=provenance,
            e2e_commands=self.e2e_commands,
            allow_ubuntu_package_override=self.allow_ubuntu_package_override,
            override_reason=self.override_reason,
        )

    @classmethod
    def build(
        cls,
        spec: BuildSpec,
        context: BuildContext,
    ) -> TemplateBuildResult:
        return build_single_binary(
            template_id=cls.template_id,
            spec=spec,
            context=context,
        )
