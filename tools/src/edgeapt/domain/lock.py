from __future__ import annotations

from typing import Any

import attrs

from edgeapt.domain.artifacts import ArtifactFact
from edgeapt.domain.keys import DebKey, PublishKey
from edgeapt.domain.planning import SourceProvenance


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
