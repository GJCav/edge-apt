from __future__ import annotations

from typing import Any

import attrs

from edgeapt.domain.keys import DebKey


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
