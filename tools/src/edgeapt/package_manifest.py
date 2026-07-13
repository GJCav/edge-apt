from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

import attrs

from edgeapt.domain.lock import LockFile
from edgeapt.errors import ValidationError
from edgeapt.util import write_json

PACKAGE_MANIFEST_SCHEMA = "edgeapt.packages/v1"


@attrs.define(kw_only=True, frozen=True, order=True)
class PackageManifestEntry:
    package: str
    version: str
    suite: str
    component: str
    arch: str
    description: str
    homepage: str | None
    size: int
    sha256: str
    filename: str

    def to_json(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "version": self.version,
            "suite": self.suite,
            "component": self.component,
            "arch": self.arch,
            "description": self.description,
            "homepage": self.homepage,
            "size": self.size,
            "sha256": self.sha256,
            "filename": self.filename,
        }


def write_package_manifest(
    *,
    output_dir: Path,
    profile: str,
    lock: LockFile,
    source_ids: tuple[str, ...] = (),
) -> Path:
    indexes: dict[
        tuple[str, str, str],
        Mapping[tuple[str, str, str], Mapping[str, str]],
    ] = {}
    entries: list[PackageManifestEntry] = []
    for publication in sorted(lock.publications, key=lambda item: item.key):
        key = publication.key
        target = (key.suite, key.component, key.arch)
        if target not in indexes:
            packages_path = (
                output_dir
                / "dists"
                / key.suite
                / key.component
                / f"binary-{key.arch}"
                / "Packages"
            )
            indexes[target] = index_package_stanzas(
                parse_debian_control(packages_path.read_text(encoding="utf-8")),
                source=packages_path,
            )
        identity = (key.package, key.deb_version, key.arch)
        fields = indexes[target].get(identity)
        if fields is None:
            raise ValidationError(
                "published package metadata is missing for "
                f"{key.suite}/{key.component}/{key.package}/"
                f"{key.deb_version}/{key.arch}"
            )
        artifact = lock.artifact_for(publication.artifact)
        size = _parse_size(fields, identity)
        sha256 = _required_field(fields, "SHA256", identity)
        if size != artifact.size:
            raise ValidationError(
                f"published size mismatch for {identity}: {size} != {artifact.size}"
            )
        if f"sha256:{sha256}" != artifact.sha256:
            raise ValidationError(
                f"published SHA256 mismatch for {identity}: sha256:{sha256} "
                f"!= {artifact.sha256}"
            )
        filename = _validated_filename(
            _required_field(fields, "Filename", identity),
            output_dir=output_dir,
        )
        entries.append(
            PackageManifestEntry(
                package=key.package,
                version=key.deb_version,
                suite=key.suite,
                component=key.component,
                arch=key.arch,
                description=_format_description(
                    _required_field(fields, "Description", identity)
                ),
                homepage=_safe_homepage(fields.get("Homepage")),
                size=size,
                sha256=f"sha256:{sha256}",
                filename=filename,
            )
        )

    path = output_dir / "packages.json"
    manifest: dict[str, Any] = {
        "schema": PACKAGE_MANIFEST_SCHEMA,
        "generated_at": lock.generated_at,
        "profile": profile,
        "packages": [entry.to_json() for entry in sorted(entries)],
    }
    if source_ids:
        manifest["scope"] = {"sources": list(source_ids)}
    write_json(path, manifest)
    return path


def parse_debian_control(content: str) -> tuple[dict[str, str], ...]:
    stanzas: list[dict[str, str]] = []
    current: dict[str, str] = {}
    current_field: str | None = None
    for line_number, line in enumerate(content.splitlines(), start=1):
        if line == "":
            if current:
                stanzas.append(current)
                current = {}
                current_field = None
            continue
        if line[0] in {" ", "\t"}:
            if current_field is None:
                raise ValidationError(
                    f"orphan continuation line in Packages at line {line_number}"
                )
            current[current_field] += "\n" + line[1:]
            continue
        field, separator, value = line.partition(":")
        if separator == "" or field == "":
            raise ValidationError(
                f"invalid Packages field at line {line_number}: {line}"
            )
        if field in current:
            raise ValidationError(
                f"duplicate Packages field at line {line_number}: {field}"
            )
        current[field] = value.lstrip(" ")
        current_field = field
    if current:
        stanzas.append(current)
    return tuple(stanzas)


def index_package_stanzas(
    stanzas: tuple[dict[str, str], ...],
    *,
    source: Path,
) -> Mapping[tuple[str, str, str], Mapping[str, str]]:
    indexed: dict[tuple[str, str, str], Mapping[str, str]] = {}
    for fields in stanzas:
        identity = (
            _required_field(fields, "Package", source),
            _required_field(fields, "Version", source),
            _required_field(fields, "Architecture", source),
        )
        if identity in indexed:
            raise ValidationError(
                f"duplicate package identity in {source}: {identity}"
            )
        indexed[identity] = fields
    return indexed


def _required_field(
    fields: Mapping[str, str],
    name: str,
    context: object,
) -> str:
    value = fields.get(name)
    if value is None or value == "":
        raise ValidationError(f"missing {name} in Packages metadata for {context}")
    return value


def _parse_size(fields: Mapping[str, str], identity: object) -> int:
    raw = _required_field(fields, "Size", identity)
    try:
        size = int(raw)
    except ValueError as error:
        raise ValidationError(
            f"invalid Size in Packages metadata for {identity}: {raw}"
        ) from error
    if size < 0:
        raise ValidationError(
            f"invalid Size in Packages metadata for {identity}: {raw}"
        )
    return size


def _format_description(value: str) -> str:
    return "\n".join(
        "" if line.strip() == "." else line.strip()
        for line in value.splitlines()
    )


def _safe_homepage(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.netloc == "":
        return None
    return value


def _validated_filename(value: str, *, output_dir: Path) -> str:
    path = PurePosixPath(value)
    if (
        value == ""
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValidationError(f"invalid package Filename: {value}")
    absolute = output_dir.joinpath(*path.parts)
    if not absolute.is_file():
        raise ValidationError(f"published package file does not exist: {value}")
    return value
