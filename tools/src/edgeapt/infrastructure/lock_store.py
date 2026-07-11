from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from edgeapt.constants import LOCK_PATH, LOCK_SCHEMA
from edgeapt.domain.artifacts import ArtifactFact, DebControlFact, UpstreamFact
from edgeapt.domain.keys import DebKey, PublishKey
from edgeapt.domain.lock import LockedPublication, LockFile
from edgeapt.domain.planning import PublicationE2EClaim, SourceProvenance
from edgeapt.util import read_json, write_json


def load_lock(path: Path = LOCK_PATH) -> LockFile | None:
    if not path.exists():
        return None
    raw = read_json(path)
    schema = raw.get("schema")
    if schema != LOCK_SCHEMA:
        raise ValueError(
            f"Unsupported lock schema in {path}: {schema}; regenerate lock.json"
        )
    generated_at = _expect_str(raw, "generated_at")
    plan_digest = _expect_str(raw, "plan_digest")
    artifacts = tuple(
        sorted(
            (_artifact_from_json(item) for item in _expect_object_array(raw, "artifacts")),
            key=lambda item: item.deb_key,
        )
    )
    publications = tuple(
        sorted(
            (
                _publication_from_json(item)
                for item in _expect_object_array(raw, "publications")
            ),
            key=lambda item: item.key,
        )
    )
    return LockFile(
        schema=LOCK_SCHEMA,
        generated_at=generated_at,
        plan_digest=plan_digest,
        artifacts=artifacts,
        publications=publications,
    )


def write_lock(lock: LockFile, path: Path = LOCK_PATH) -> None:
    write_json(path, lock.to_json())


def _artifact_from_json(data: Mapping[str, Any]) -> ArtifactFact:
    upstream = _expect_object(data, "upstream")
    deb_control_raw = data.get("deb_control")
    deb_control = None
    if isinstance(deb_control_raw, dict):
        deb_control_data = cast(dict[str, Any], deb_control_raw)
        deb_control = DebControlFact(
            package=_expect_str(deb_control_data, "package"),
            version=_expect_str(deb_control_data, "version"),
            architecture=_expect_str(deb_control_data, "architecture"),
        )
    revision_raw = data.get("revision")
    revision = revision_raw if isinstance(revision_raw, int) else None
    return ArtifactFact(
        deb_key=_deb_key_from_json(_expect_object(data, "deb_key")),
        build_plan_digest=_expect_str(data, "build_plan_digest"),
        upstream_version=_expect_str(data, "upstream_version"),
        revision=revision,
        path=_expect_str(data, "path"),
        sha256=_expect_str(data, "sha256"),
        size=_expect_int(data, "size"),
        upstream=_upstream_from_json(upstream),
        deb_control=deb_control,
        created_at=_expect_str(data, "created_at"),
    )


def _publication_from_json(data: Mapping[str, Any]) -> LockedPublication:
    claims = tuple(
        sorted(
            (_e2e_claim_from_json(item) for item in _expect_object_array(data, "e2e_claims")),
            key=lambda claim: (claim.provenance, claim.commands),
        )
    )
    if not claims:
        raise ValueError("lock field e2e_claims must be a non-empty array")
    return LockedPublication(
        key=_publish_key_from_json(_expect_object(data, "key")),
        artifact=_deb_key_from_json(_expect_object(data, "artifact")),
        e2e_claims=claims,
    )


def _e2e_claim_from_json(data: Mapping[str, Any]) -> PublicationE2EClaim:
    commands_raw = data.get("commands")
    if not isinstance(commands_raw, list) or not commands_raw:
        raise ValueError("lock field commands must be a non-empty array")
    commands = tuple(
        sorted(
            _str_tuple(item, "commands")
            for item in cast(list[object], commands_raw)
        )
    )
    return PublicationE2EClaim(
        provenance=_provenance_from_json(_expect_object(data, "provenance")),
        commands=commands,
    )


def _deb_key_from_json(data: Mapping[str, Any]) -> DebKey:
    return DebKey(
        package=_expect_str(data, "package"),
        deb_version=_expect_str(data, "deb_version"),
        arch=_expect_str(data, "arch"),
    )


def _publish_key_from_json(data: Mapping[str, Any]) -> PublishKey:
    return PublishKey(
        suite=_expect_str(data, "suite"),
        component=_expect_str(data, "component"),
        package=_expect_str(data, "package"),
        deb_version=_expect_str(data, "deb_version"),
        arch=_expect_str(data, "arch"),
    )


def _provenance_from_json(data: Mapping[str, Any]) -> SourceProvenance:
    return SourceProvenance(
        source_id=_expect_str(data, "source_id"),
        source_file=_expect_str(data, "source_file"),
        upstream_index=_expect_int(data, "upstream_index"),
    )


def _upstream_from_json(data: Mapping[str, Any]) -> UpstreamFact:
    return UpstreamFact(
        url=_expect_str(data, "url"),
        sha256=_expect_str(data, "sha256"),
        size=_expect_int(data, "size"),
        etag=_optional_str(data, "etag"),
        last_modified=_optional_str(data, "last_modified"),
    )


def _expect_object(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"lock field {key} must be an object")
    return cast(dict[str, Any], value)


def _expect_object_array(data: Mapping[str, Any], key: str) -> tuple[dict[str, Any], ...]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"lock field {key} must be an array")
    result: list[dict[str, Any]] = []
    for item in cast(list[object], value):
        if not isinstance(item, dict):
            raise ValueError(f"lock field {key} must contain objects")
        result.append(cast(dict[str, Any], item))
    return tuple(result)


def _expect_str(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"lock field {key} must be a string")
    return value


def _optional_str(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"lock field {key} must be a string")
    return value


def _expect_int(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int):
        raise ValueError(f"lock field {key} must be an integer")
    return value


def _str_tuple(value: object, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"lock field {key} must contain non-empty string arrays")
    result: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or item == "":
            raise ValueError(f"lock field {key} must contain non-empty string arrays")
        result.append(item)
    return tuple(result)
