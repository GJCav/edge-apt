from __future__ import annotations

from typing import Any

import attrs


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
