from __future__ import annotations

from typing import Any, Protocol

import attrs

from edgeapt.domain.keys import BuildCacheKey, DebKey, PublishKey

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class BuildSpec(Protocol):
    @property
    def template_id(self) -> str: ...

    def to_canonical_data(self) -> JsonObject: ...


@attrs.define(kw_only=True, frozen=True, order=True)
class SourceProvenance:
    source_id: str
    source_file: str
    upstream_index: int

    def to_json(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_file": self.source_file,
            "upstream_index": self.upstream_index,
        }


@attrs.define(kw_only=True, frozen=True)
class BuildIntent:
    deb_key: DebKey
    suites: tuple[str, ...]
    build_spec: BuildSpec
    provenance: SourceProvenance
    e2e_command: tuple[str, ...]
    allow_ubuntu_package_override: bool
    override_reason: str | None


@attrs.define(kw_only=True, frozen=True)
class PublishClaim:
    key: PublishKey
    build_spec: BuildSpec
    provenance: SourceProvenance
    e2e_command: tuple[str, ...]
    allow_ubuntu_package_override: bool
    override_reason: str | None


@attrs.define(kw_only=True, frozen=True)
class BuildUnit:
    deb_key: DebKey
    build_spec: BuildSpec
    plan_digest: str
    provenance: tuple[SourceProvenance, ...]

    @property
    def cache_key(self) -> BuildCacheKey:
        return BuildCacheKey(deb_key=self.deb_key, plan_digest=self.plan_digest)

    def to_json(self) -> dict[str, Any]:
        return {
            "deb_key": self.deb_key.to_json(),
            "build_spec": self.build_spec.to_canonical_data(),
            "plan_digest": self.plan_digest,
            "provenance": [item.to_json() for item in self.provenance],
        }


@attrs.define(kw_only=True, frozen=True)
class Publication:
    key: PublishKey
    deb_key: DebKey
    provenance: tuple[SourceProvenance, ...]
    e2e_commands: tuple[tuple[str, ...], ...]
    allow_ubuntu_package_override: bool
    override_reasons: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key.to_json(),
            "deb_key": self.deb_key.to_json(),
            "provenance": [item.to_json() for item in self.provenance],
            "e2e_commands": [list(command) for command in self.e2e_commands],
            "allow_ubuntu_package_override": self.allow_ubuntu_package_override,
            "override_reasons": list(self.override_reasons),
        }


@attrs.define(kw_only=True, frozen=True)
class RepoPlan:
    plan_digest: str
    builds: tuple[BuildUnit, ...]
    publications: tuple[Publication, ...]

    def build_for(self, key: DebKey) -> BuildUnit:
        for build in self.builds:
            if build.deb_key == key:
                return build
        raise KeyError(key)
