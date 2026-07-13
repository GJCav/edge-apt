from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Literal

from debian.deb822 import PkgRelation
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from edgeapt.domain.planning import BuildIntent, BuildSpec, SourceProvenance
from edgeapt.templates.base import BuildContext, SourceTemplate, TemplateBuildResult
from edgeapt.templates.common import (
    DEBIAN_VERSION_RE,
    PACKAGE_RE,
    DebPackageMetadataSpec,
    MultiArch,
    FetchSpec,
)
from edgeapt.templates.single_binary import (
    ArchiveCopyrightSpec,
    FetchCopyrightSpec,
    SingleBinaryRepackageSpec,
    build_single_binary,
    plan_single_binary,
    validate_archive_member_path,
)
from edgeapt.templates.single_binary_v1 import SingleBinaryUpstreamModel


class SingleBinaryMetadataV11Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field(min_length=1)
    homepage: str | None = None
    section: str = Field(default="utils", pattern=r"^[a-z0-9][a-z0-9+.-]*$")
    multi_arch: MultiArch | None = None
    depends: tuple[str, ...] = ()

    @field_validator("depends")
    @classmethod
    def _normalized_depends(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_depends(value)


class ArchiveCopyrightModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str

    @field_validator("path")
    @classmethod
    def _valid_path(cls, value: str) -> str:
        return validate_archive_member_path(value)


class FetchCopyrightModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SingleBinaryRepackageV11Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    install_path: str = Field(min_length=1)
    metadata: SingleBinaryMetadataV11Model
    copyright: ArchiveCopyrightModel | FetchCopyrightModel

    @field_validator("install_path")
    @classmethod
    def _absolute_install_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("repackage.install_path must be absolute")
        return value


class SingleBinaryV11(SourceTemplate):
    template_id: ClassVar[str] = "edgeapt.single_binary/v1.1"

    template: Literal["edgeapt.single_binary/v1.1"]
    repackage: SingleBinaryRepackageV11Model
    upstream: tuple[SingleBinaryUpstreamModel, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _archive_copyright_requires_archive(self) -> SingleBinaryV11:
        if isinstance(self.repackage.copyright, ArchiveCopyrightModel):
            missing = [
                index
                for index, upstream in enumerate(self.upstream)
                if upstream.extract_path is None
            ]
            if missing:
                indexes = ", ".join(f"upstream[{index}]" for index in missing)
                raise ValueError(
                    "copyright.path requires extract_path for every upstream; "
                    f"missing in {indexes}"
                )
        return self

    def plan(self, provenance: SourceProvenance) -> tuple[BuildIntent, ...]:
        metadata = self.repackage.metadata
        copyright_model = self.repackage.copyright
        copyright = (
            ArchiveCopyrightSpec(path=copyright_model.path)
            if isinstance(copyright_model, ArchiveCopyrightModel)
            else FetchCopyrightSpec(
                fetch=FetchSpec(
                    url=copyright_model.url,
                    sha256=copyright_model.sha256,
                )
            )
        )
        return plan_single_binary(
            template_id=self.template_id,
            package=self.package,
            upstreams=self.upstream,
            repackage=SingleBinaryRepackageSpec(
                install_path=self.repackage.install_path,
                metadata=DebPackageMetadataSpec(
                    description=metadata.description,
                    homepage=metadata.homepage,
                    section=metadata.section,
                    multi_arch=metadata.multi_arch,
                    depends=metadata.depends,
                ),
                copyright=copyright,
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


def normalize_depends(value: tuple[str, ...]) -> tuple[str, ...]:
    normalized: set[str] = set()
    for item in value:
        relations = PkgRelation.parse_relations(item)
        if len(relations) != 1:
            raise ValueError(
                "each depends item must contain exactly one comma-level group"
            )
        alternatives = relations[0]
        if not alternatives:
            raise ValueError("depends item must not be empty")
        for relation in alternatives:
            _validate_relation(relation)
        normalized.add(PkgRelation.str(relations))
    return tuple(sorted(normalized))


def _validate_relation(relation: Mapping[str, Any]) -> None:
    name = relation["name"]
    if not isinstance(name, str) or not PACKAGE_RE.fullmatch(name):
        raise ValueError(f"invalid dependency package name: {name}")
    archqual = relation["archqual"]
    if archqual is not None and (
        not isinstance(archqual, str)
        or not PACKAGE_RE.fullmatch(archqual)
    ):
        raise ValueError(f"invalid dependency architecture qualifier: {archqual}")
    version = relation["version"]
    if version is not None:
        operator, number = version
        if operator not in {"<<", "<=", "=", ">=", ">>"}:
            raise ValueError(f"invalid dependency version operator: {operator}")
        if not DEBIAN_VERSION_RE.fullmatch(number):
            raise ValueError(f"invalid dependency version: {number}")
