from __future__ import annotations

from typing import Any

import attrs


@attrs.define(kw_only=True, frozen=True)
class RepackageMetadata:
    description: str
    homepage: str | None = None

    def to_json(self) -> dict[str, str]:
        data = {"description": self.description}
        if self.homepage is not None:
            data["homepage"] = self.homepage
        return data


@attrs.define(kw_only=True, frozen=True)
class RepackageConfig:
    type: str
    install_path: str
    metadata: RepackageMetadata

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "install_path": self.install_path,
            "metadata": self.metadata.to_json(),
        }


@attrs.define(kw_only=True, frozen=True)
class UpstreamConfig:
    version: str
    arch: str
    suites: tuple[str, ...]
    url: str
    sha256: str | None = None
    revision: int | None = None
    extract_path: str | None = None


@attrs.define(kw_only=True, frozen=True)
class SourceConfig:
    template: str
    id: str
    package: str
    e2e_command: tuple[str, ...]
    source_file: str
    repackage: RepackageConfig | None
    upstream: tuple[UpstreamConfig, ...]
    allow_ubuntu_package_override: bool = False
    override_reason: str | None = None


@attrs.define(kw_only=True, frozen=True, order=True)
class DebKey:
    package: str
    deb_version: str
    arch: str

    def to_json(self) -> dict[str, str]:
        return {
            "package": self.package,
            "deb_version": self.deb_version,
            "arch": self.arch,
        }


@attrs.define(kw_only=True, frozen=True, order=True)
class PublishKey:
    suite: str
    component: str
    package: str
    deb_version: str
    arch: str

    @property
    def deb_key(self) -> DebKey:
        return DebKey(
            package=self.package,
            deb_version=self.deb_version,
            arch=self.arch,
        )

    def to_json(self) -> dict[str, str]:
        return {
            "suite": self.suite,
            "component": self.component,
            "package": self.package,
            "deb_version": self.deb_version,
            "arch": self.arch,
        }


@attrs.define(kw_only=True, frozen=True, order=True)
class BuildCacheKey:
    deb_key: DebKey
    plan_digest: str

    def to_json(self) -> dict[str, Any]:
        return {
            "deb_key": self.deb_key.to_json(),
            "plan_digest": self.plan_digest,
        }


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
class FetchSpec:
    url: str
    sha256: str | None

    def to_json(self) -> dict[str, str]:
        data = {"url": self.url}
        if self.sha256 is not None:
            data["sha256"] = self.sha256
        return data


@attrs.define(kw_only=True, frozen=True)
class SingleBinaryBuildSpec:
    template: str
    upstream_version: str
    revision: int
    fetch: FetchSpec
    extract_path: str | None
    repackage: RepackageConfig

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "template": self.template,
            "upstream_version": self.upstream_version,
            "revision": self.revision,
            "fetch": self.fetch.to_json(),
            "repackage": self.repackage.to_json(),
        }
        if self.extract_path is not None:
            data["extract_path"] = self.extract_path
        return data


@attrs.define(kw_only=True, frozen=True)
class DebUpstreamBuildSpec:
    template: str
    upstream_version: str
    fetch: FetchSpec

    def to_json(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "upstream_version": self.upstream_version,
            "fetch": self.fetch.to_json(),
        }


BuildSpec = SingleBinaryBuildSpec | DebUpstreamBuildSpec


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
            "build_spec": self.build_spec.to_json(),
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


@attrs.define(kw_only=True, frozen=True)
class UpstreamFact:
    url: str
    sha256: str
    size: int
    etag: str | None = None
    last_modified: str | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "url": self.url,
            "sha256": self.sha256,
            "size": self.size,
        }
        if self.etag is not None:
            data["etag"] = self.etag
        if self.last_modified is not None:
            data["last_modified"] = self.last_modified
        return data


@attrs.define(kw_only=True, frozen=True)
class DebControlFact:
    package: str
    version: str
    architecture: str

    def to_json(self) -> dict[str, str]:
        return {
            "package": self.package,
            "version": self.version,
            "architecture": self.architecture,
        }


@attrs.define(kw_only=True, frozen=True)
class ArtifactFact:
    deb_key: DebKey
    build_plan_digest: str
    upstream_version: str
    path: str
    sha256: str
    size: int
    upstream: UpstreamFact
    created_at: str
    revision: int | None = None
    deb_control: DebControlFact | None = None

    @property
    def package(self) -> str:
        return self.deb_key.package

    @property
    def version(self) -> str:
        return self.deb_key.deb_version

    @property
    def arch(self) -> str:
        return self.deb_key.arch

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "deb_key": self.deb_key.to_json(),
            "build_plan_digest": self.build_plan_digest,
            "upstream_version": self.upstream_version,
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "upstream": self.upstream.to_json(),
            "created_at": self.created_at,
        }
        if self.revision is not None:
            data["revision"] = self.revision
        if self.deb_control is not None:
            data["deb_control"] = self.deb_control.to_json()
        return data


@attrs.define(kw_only=True, frozen=True)
class LockedPublication:
    key: PublishKey
    artifact: DebKey
    provenance: tuple[SourceProvenance, ...]
    e2e_commands: tuple[tuple[str, ...], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key.to_json(),
            "artifact": self.artifact.to_json(),
            "provenance": [item.to_json() for item in self.provenance],
            "e2e_commands": [list(command) for command in self.e2e_commands],
        }


@attrs.define(kw_only=True, frozen=True)
class LockFile:
    schema: str
    generated_at: str
    plan_digest: str
    artifacts: tuple[ArtifactFact, ...]
    publications: tuple[LockedPublication, ...]

    def artifact_for(self, key: DebKey) -> ArtifactFact:
        for artifact in self.artifacts:
            if artifact.deb_key == key:
                return artifact
        raise KeyError(key)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at,
            "plan_digest": self.plan_digest,
            "artifacts": [
                artifact.to_json()
                for artifact in sorted(self.artifacts, key=lambda item: item.deb_key)
            ],
            "publications": [
                publication.to_json()
                for publication in sorted(self.publications, key=lambda item: item.key)
            ],
        }
