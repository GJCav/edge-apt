from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from edgeapt.constants import LOCK_PATH, LOCK_SCHEMA
from edgeapt.models import (
    ArtifactFact,
    DebControlFact,
    LockFile,
    SourceLock,
    UpstreamFact,
)
from edgeapt.util import read_json, write_json


def load_lock(path: Path = LOCK_PATH) -> LockFile | None:
    if not path.exists():
        return None
    raw = read_json(path)
    if raw.get("schema") != LOCK_SCHEMA:
        raise ValueError(f"Unsupported lock schema in {path}: {raw.get('schema')}")
    sources_raw_obj = raw.get("sources", {})
    if not isinstance(sources_raw_obj, dict):
        raise ValueError("lock sources must be an object")
    sources_raw = cast(dict[str, object], sources_raw_obj)
    sources: dict[str, SourceLock] = {}
    for source_id, value in sources_raw.items():
        if not isinstance(value, dict):
            raise ValueError("invalid lock source entry")
        sources[source_id] = _source_lock_from_json(cast(dict[str, Any], value))
    generated_at = raw.get("generated_at")
    if not isinstance(generated_at, str):
        raise ValueError("lock generated_at must be a string")
    return LockFile(schema=LOCK_SCHEMA, generated_at=generated_at, sources=sources)


def write_lock(lock: LockFile, path: Path = LOCK_PATH) -> None:
    write_json(path, lock.to_json())


def _source_lock_from_json(data: Mapping[str, Any]) -> SourceLock:
    artifacts_raw_obj = data.get("artifacts")
    if not isinstance(artifacts_raw_obj, list):
        raise ValueError("source artifacts must be an array")
    artifacts_raw = cast(list[object], artifacts_raw_obj)
    artifacts: list[ArtifactFact] = []
    for item in artifacts_raw:
        if not isinstance(item, dict):
            raise ValueError("artifact must be an object")
        artifacts.append(_artifact_from_json(cast(dict[str, Any], item)))
    return SourceLock(
        source_file=_expect_str(data, "source_file"),
        source_sha256=_expect_str(data, "source_sha256"),
        template=_expect_str(data, "template"),
        package=_expect_str(data, "package"),
        e2e_command=_expect_str_tuple(data, "e2e_command"),
        artifacts=tuple(artifacts),
    )


def _artifact_from_json(data: Mapping[str, Any]) -> ArtifactFact:
    upstream_raw = data.get("upstream")
    if not isinstance(upstream_raw, dict):
        raise ValueError("artifact upstream must be an object")
    suites_raw_obj = data.get("suites")
    if not isinstance(suites_raw_obj, list):
        raise ValueError("artifact suites must be a string array")
    suites_raw = cast(list[object], suites_raw_obj)
    suites: list[str] = []
    for item in suites_raw:
        if not isinstance(item, str):
            raise ValueError("artifact suites must be a string array")
        suites.append(item)
    deb_control = None
    deb_control_raw = data.get("deb_control")
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
        package=_expect_str(data, "package"),
        version=_expect_str(data, "version"),
        upstream_version=_expect_str(data, "upstream_version"),
        revision=revision,
        arch=_expect_str(data, "arch"),
        suites=tuple(suites),
        path=_expect_str(data, "path"),
        sha256=_expect_str(data, "sha256"),
        size=_expect_int(data, "size"),
        upstream=_upstream_from_json(cast(dict[str, Any], upstream_raw)),
        deb_control=deb_control,
        created_at=_expect_str(data, "created_at"),
    )


def _upstream_from_json(data: Mapping[str, Any]) -> UpstreamFact:
    return UpstreamFact(
        url=_expect_str(data, "url"),
        sha256=_expect_str(data, "sha256"),
        size=_expect_int(data, "size"),
        etag=_optional_str(data, "etag"),
        last_modified=_optional_str(data, "last_modified"),
    )


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


def _expect_str_tuple(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"lock field {key} must be a non-empty string array")
    value_list = cast(list[object], value)
    result: list[str] = []
    for item in value_list:
        if not isinstance(item, str) or item == "":
            raise ValueError(f"lock field {key} must be a non-empty string array")
        result.append(item)
    return tuple(result)
