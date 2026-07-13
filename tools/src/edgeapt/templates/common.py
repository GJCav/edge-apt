from __future__ import annotations

import re
from typing import Any, Literal, cast

import attrs
from pydantic import BaseModel, ConfigDict, Field

from edgeapt.constants import SUPPORTED_ARCHES, SUPPORTED_SUITES
from edgeapt.domain.planning import JsonObject

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,}$")
DEBIAN_VERSION_RE = re.compile(r"^[A-Za-z0-9.+:~][A-Za-z0-9.+:~_-]*$")

type MultiArch = Literal["allowed", "foreign", "same"]


class DebPackageMetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str = Field(min_length=1)
    homepage: str | None = None
    section: str = Field(default="utils", pattern=r"^[a-z0-9][a-z0-9+.-]*$")
    multi_arch: MultiArch | None = None


@attrs.define(kw_only=True, frozen=True)
class DebPackageMetadataSpec:
    description: str
    homepage: str | None
    section: str
    multi_arch: MultiArch | None

    @classmethod
    def from_model(cls, model: DebPackageMetadataModel) -> DebPackageMetadataSpec:
        return cls(
            description=model.description,
            homepage=model.homepage,
            section=model.section,
            multi_arch=model.multi_arch,
        )

    def to_canonical_data(self) -> JsonObject:
        data: dict[str, Any] = {"description": self.description}
        if self.homepage is not None:
            data["homepage"] = self.homepage
        if self.section != "utils":
            data["section"] = self.section
        if self.multi_arch is not None:
            data["multi_arch"] = self.multi_arch
        return cast(JsonObject, data)


def validate_id(source_id: str) -> None:
    if not ID_RE.fullmatch(source_id):
        raise ValueError(f"invalid id: {source_id}")


def validate_package(package: str) -> None:
    if not PACKAGE_RE.fullmatch(package):
        raise ValueError(f"invalid package name: {package}")


def validate_arch(arch: str) -> None:
    if arch not in SUPPORTED_ARCHES:
        raise ValueError(f"unsupported arch: {arch}")


def validate_suite(suite: str) -> None:
    if suite not in SUPPORTED_SUITES:
        raise ValueError(f"unsupported suite: {suite}")


def normalize_debian_version(version: str) -> str:
    normalized = version[1:] if re.match(r"^[vV][0-9]", version) else version
    if not DEBIAN_VERSION_RE.fullmatch(normalized):
        raise ValueError(f"invalid Debian version after normalization: {version}")
    return normalized


@attrs.define(kw_only=True, frozen=True)
class FetchSpec:
    url: str
    sha256: str | None

    def to_canonical_data(self) -> JsonObject:
        data: dict[str, Any] = {"url": self.url}
        if self.sha256 is not None:
            data["sha256"] = self.sha256
        return cast(JsonObject, data)
