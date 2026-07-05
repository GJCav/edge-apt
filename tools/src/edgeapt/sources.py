from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn, cast

import yaml

from edgeapt.constants import (
    ROOT,
    SOURCES_DIR,
    SUPPORTED_ARCHES,
    SUPPORTED_SUITES,
    SUPPORTED_TEMPLATES,
)
from edgeapt.errors import ValidationError
from edgeapt.models import (
    RepackageConfig,
    RepackageMetadata,
    SourceConfig,
    UpstreamConfig,
)
from edgeapt.util import relative_to_root

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,}$")
SUITE_RE = re.compile(r"^[a-z][a-z0-9-]*$")
DEBIAN_VERSION_RE = re.compile(r"^[A-Za-z0-9.+:~][A-Za-z0-9.+:~_-]*$")


def load_sources(sources_dir: Path = SOURCES_DIR) -> tuple[SourceConfig, ...]:
    if not sources_dir.exists():
        return ()
    sources = [
        load_source(path)
        for path in sorted(sources_dir.glob("*.yaml"))
        if path.is_file()
    ]
    ids = [source.id for source in sources]
    duplicates = sorted({source_id for source_id in ids if ids.count(source_id) > 1})
    if duplicates:
        raise ValidationError(f"Duplicate source id(s): {', '.join(duplicates)}")
    return tuple(sources)


def load_source(path: Path) -> SourceConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValidationError(f"{path}: source must be a YAML mapping")
    data = cast(dict[str, Any], raw)
    template = _required_str(data, "template", path)
    source_id = _required_str(data, "id", path)
    package = _required_str(data, "package", path)

    validate_template(template, path)
    validate_id(source_id, path)
    validate_package(package, path)

    allow_ubuntu_package_override = _optional_bool(
        data,
        "allow_ubuntu_package_override",
        path,
    )
    override_reason = _optional_str(data, "override_reason", path)
    if allow_ubuntu_package_override and override_reason is None:
        _raise("override_reason is required when package override is allowed", path)
    if not allow_ubuntu_package_override and override_reason is not None:
        _raise(
            "override_reason is only valid with allow_ubuntu_package_override: true",
            path,
        )

    upstream = _parse_upstream(data.get("upstream"), template, path)
    repackage = _parse_repackage(data.get("repackage"), template, path)

    return SourceConfig(
        template=template,
        id=source_id,
        package=package,
        source_file=relative_to_root(path, ROOT),
        repackage=repackage,
        upstream=upstream,
        allow_ubuntu_package_override=allow_ubuntu_package_override,
        override_reason=override_reason,
    )


def validate_template(template: str, path: Path | None = None) -> None:
    if template not in SUPPORTED_TEMPLATES:
        _raise(f"unsupported template: {template}", path)


def validate_id(source_id: str, path: Path | None = None) -> None:
    if not ID_RE.fullmatch(source_id):
        _raise(f"invalid id: {source_id}", path)


def validate_package(package: str, path: Path | None = None) -> None:
    if not PACKAGE_RE.fullmatch(package):
        _raise(f"invalid package name: {package}", path)


def validate_arch(arch: str, path: Path | None = None) -> None:
    if arch not in SUPPORTED_ARCHES:
        _raise(f"unsupported arch: {arch}", path)


def validate_suite(suite: str, path: Path | None = None) -> None:
    if not SUITE_RE.fullmatch(suite):
        _raise(f"invalid suite syntax: {suite}", path)
    if suite not in SUPPORTED_SUITES:
        _raise(f"unsupported suite: {suite}", path)


def normalize_debian_version(version: str) -> str:
    normalized = version[1:] if re.match(r"^[vV][0-9]", version) else version
    if not DEBIAN_VERSION_RE.fullmatch(normalized):
        raise ValidationError(f"invalid Debian version after normalization: {version}")
    return normalized


def artifact_version(source: SourceConfig, upstream: UpstreamConfig) -> str:
    if source.template == "edgeapt.single_binary/v1":
        if upstream.revision is None:
            raise ValidationError(f"{source.id}: single_binary upstream missing revision")
        if upstream.revision < 1:
            raise ValidationError(f"{source.id}: revision must be a positive integer")
        return f"{normalize_debian_version(upstream.version)}-{upstream.revision}"
    return upstream.version


def artifact_path(source: SourceConfig, upstream: UpstreamConfig) -> Path:
    version = artifact_version(source, upstream)
    return Path("packages") / source.id / f"{source.package}_{version}_{upstream.arch}.deb"


def _parse_repackage(
    raw: object,
    template: str,
    path: Path,
) -> RepackageConfig | None:
    if template == "edgeapt.deb_upstream/v1":
        if raw is not None:
            _raise("deb_upstream does not accept repackage", path)
        return None

    if not isinstance(raw, dict):
        _raise("single_binary requires repackage mapping", path)
    data = cast(dict[str, Any], raw)
    repackage_type = _required_str(data, "type", path)
    if repackage_type != "nfpm":
        _raise("repackage.type must be nfpm", path)
    install_path = _required_str(data, "install_path", path)
    if not install_path.startswith("/"):
        _raise("repackage.install_path must be absolute", path)
    metadata_raw = data.get("metadata")
    if not isinstance(metadata_raw, dict):
        _raise("repackage.metadata must be a mapping", path)
    metadata = cast(dict[str, Any], metadata_raw)
    description = _required_str(metadata, "description", path)
    homepage = _optional_str(metadata, "homepage", path)
    return RepackageConfig(
        type=repackage_type,
        install_path=install_path,
        metadata=RepackageMetadata(description=description, homepage=homepage),
    )


def _parse_upstream(
    raw: object,
    template: str,
    path: Path,
) -> tuple[UpstreamConfig, ...]:
    if not isinstance(raw, list):
        _raise("upstream must be a non-empty array", path)
    raw_list = cast(list[object], raw)
    if not raw_list:
        _raise("upstream must be a non-empty array", path)
    entries: list[UpstreamConfig] = []
    for index, item in enumerate(raw_list):
        if not isinstance(item, dict):
            _raise(f"upstream[{index}] must be a mapping", path)
        data = cast(dict[str, Any], item)
        version = _required_str(data, "version", path)
        arch = _required_str(data, "arch", path)
        validate_arch(arch, path)
        suites = _required_str_list(data, "suites", path)
        for suite in suites:
            validate_suite(suite, path)
        url = _required_str(data, "url", path)
        sha256 = _optional_str(data, "sha256", path)
        revision = _optional_int(data, "revision", path)
        extract_path = _optional_str(data, "extract_path", path)

        if template == "edgeapt.single_binary/v1":
            if revision is None:
                _raise(f"upstream[{index}].revision is required", path)
        elif revision is not None:
            _raise(f"upstream[{index}].revision is not valid for deb_upstream", path)

        entries.append(
            UpstreamConfig(
                version=version,
                revision=revision,
                arch=arch,
                suites=tuple(suites),
                url=url,
                sha256=sha256,
                extract_path=extract_path,
            )
        )
    return tuple(entries)


def _required_str(data: Mapping[str, Any], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or value == "":
        _raise(f"{key} must be a non-empty string", path)
    return value


def _optional_str(data: Mapping[str, Any], key: str, path: Path) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        _raise(f"{key} must be a non-empty string", path)
    return value


def _optional_int(data: Mapping[str, Any], key: str, path: Path) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int):
        _raise(f"{key} must be an integer", path)
    return value


def _optional_bool(data: Mapping[str, Any], key: str, path: Path) -> bool:
    value = data.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        _raise(f"{key} must be a boolean", path)
    return value


def _required_str_list(data: Mapping[str, Any], key: str, path: Path) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list):
        _raise(f"{key} must be a non-empty array", path)
    value_list = cast(list[object], value)
    if not value_list:
        _raise(f"{key} must be a non-empty array", path)
    result: list[str] = []
    for item in value_list:
        if not isinstance(item, str) or item == "":
            _raise(f"{key} must contain non-empty strings", path)
        result.append(item)
    return result


def _raise(message: str, path: Path | None) -> NoReturn:
    prefix = f"{path}: " if path is not None else ""
    raise ValidationError(f"{prefix}{message}")
