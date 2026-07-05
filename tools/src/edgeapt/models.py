from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import attrs


@attrs.define(kw_only=True, frozen=True)
class RepackageMetadata:
    description: str
    homepage: str | None = None


@attrs.define(kw_only=True, frozen=True)
class RepackageConfig:
    type: str
    install_path: str
    metadata: RepackageMetadata


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
    source_file: str
    repackage: RepackageConfig | None
    upstream: tuple[UpstreamConfig, ...]


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
    package: str
    version: str
    upstream_version: str
    arch: str
    suites: tuple[str, ...]
    path: str
    sha256: str
    size: int
    upstream: UpstreamFact
    created_at: str
    revision: int | None = None
    deb_control: DebControlFact | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "package": self.package,
            "version": self.version,
            "upstream_version": self.upstream_version,
            "arch": self.arch,
            "suites": list(self.suites),
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
class SourceLock:
    source_file: str
    source_sha256: str
    template: str
    package: str
    artifacts: tuple[ArtifactFact, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "template": self.template,
            "package": self.package,
            "artifacts": [artifact.to_json() for artifact in self.artifacts],
        }


@attrs.define(kw_only=True, frozen=True)
class LockFile:
    schema: str
    generated_at: str
    sources: Mapping[str, SourceLock]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at,
            "sources": {
                source_id: self.sources[source_id].to_json()
                for source_id in sorted(self.sources)
            },
        }
