from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

import attrs

from edgeapt.domain.keys import DebKey
from edgeapt.domain.planning import (
    BuildIntent,
    BuildSpec,
    JsonObject,
    SourceProvenance,
)
from edgeapt.templates.base import BuildContext, TemplateBuildResult
from edgeapt.templates.common import (
    DebPackageMetadataSpec,
    FetchSpec,
    normalize_debian_version,
)


class CopyrightSpec(Protocol):
    def to_canonical_data(self) -> JsonObject: ...


@attrs.define(kw_only=True, frozen=True)
class ArchiveCopyrightSpec:
    path: str

    def to_canonical_data(self) -> JsonObject:
        return cast(JsonObject, {"path": self.path})


@attrs.define(kw_only=True, frozen=True)
class FetchCopyrightSpec:
    fetch: FetchSpec

    def to_canonical_data(self) -> JsonObject:
        return self.fetch.to_canonical_data()


@attrs.define(kw_only=True, frozen=True)
class SingleBinaryRepackageSpec:
    install_path: str
    metadata: DebPackageMetadataSpec
    copyright: CopyrightSpec | None = None

    def to_canonical_data(self) -> JsonObject:
        data = cast(
            JsonObject,
            {
                "install_path": self.install_path,
                "metadata": self.metadata.to_canonical_data(),
            },
        )
        if self.copyright is not None:
            data["copyright"] = self.copyright.to_canonical_data()
        return data


@attrs.define(kw_only=True, frozen=True)
class SingleBinaryBuildSpec:
    resolved_template_id: str
    upstream_version: str
    revision: int
    fetch: FetchSpec
    extract_path: str | None
    repackage: SingleBinaryRepackageSpec

    @property
    def template_id(self) -> str:
        return self.resolved_template_id

    def to_canonical_data(self) -> JsonObject:
        data = cast(
            JsonObject,
            {
                "template": self.template_id,
                "upstream_version": self.upstream_version,
                "revision": self.revision,
                "fetch": self.fetch.to_canonical_data(),
                "repackage": self.repackage.to_canonical_data(),
            },
        )
        if self.extract_path is not None:
            data["extract_path"] = self.extract_path
        return data


class SingleBinaryUpstream(Protocol):
    version: str
    revision: int
    arch: str
    suites: tuple[str, ...]
    url: str
    sha256: str
    extract_path: str | None


def plan_single_binary(
    *,
    template_id: str,
    package: str,
    upstreams: tuple[SingleBinaryUpstream, ...],
    repackage: SingleBinaryRepackageSpec,
    provenance: SourceProvenance,
    e2e_commands: tuple[tuple[str, ...], ...],
    allow_ubuntu_package_override: bool,
    override_reason: str | None,
) -> tuple[BuildIntent, ...]:
    intents: list[BuildIntent] = []
    for index, upstream in enumerate(upstreams):
        deb_version = (
            f"{normalize_debian_version(upstream.version)}-{upstream.revision}"
        )
        intents.append(
            BuildIntent(
                deb_key=DebKey(
                    package=package,
                    deb_version=deb_version,
                    arch=upstream.arch,
                ),
                suites=upstream.suites,
                build_spec=SingleBinaryBuildSpec(
                    resolved_template_id=template_id,
                    upstream_version=upstream.version,
                    revision=upstream.revision,
                    fetch=FetchSpec(url=upstream.url, sha256=upstream.sha256),
                    extract_path=upstream.extract_path,
                    repackage=repackage,
                ),
                provenance=attrs.evolve(provenance, upstream_index=index),
                e2e_commands=e2e_commands,
                allow_ubuntu_package_override=allow_ubuntu_package_override,
                override_reason=override_reason,
            )
        )
    return tuple(intents)


def build_single_binary(
    *,
    template_id: str,
    spec: BuildSpec,
    context: BuildContext,
) -> TemplateBuildResult:
    if not isinstance(spec, SingleBinaryBuildSpec) or spec.template_id != template_id:
        raise TypeError(f"{template_id} cannot build {type(spec).__name__}")
    context.report(
        "fetch_start",
        f"Fetching {context.deb_key.package} {spec.upstream_version} "
        f"{context.deb_key.arch}",
        spec.fetch.url,
    )
    download = context.fetcher.fetch(
        url=spec.fetch.url,
        sha256=spec.fetch.sha256,
        destination=context.work_dir / "upstream",
        root=context.root,
    )

    archive_copyright = (
        spec.repackage.copyright
        if isinstance(spec.repackage.copyright, ArchiveCopyrightSpec)
        else None
    )
    extracted: Mapping[str, Path] = {}
    if spec.extract_path is not None:
        paths = (spec.extract_path,)
        if archive_copyright is not None:
            paths += (archive_copyright.path,)
        context.report("extract_start", ", ".join(paths), None)
        extracted = context.archive_extractor.extract_regular_files(
            archive=download.path,
            strip_components=0,
            paths=paths,
            destination=context.work_dir / "extract",
        )
        binary = extracted[spec.extract_path]
    else:
        binary = download.path

    copyright_file: Path | None = None
    if archive_copyright is not None:
        copyright_file = extracted[archive_copyright.path]
    elif isinstance(spec.repackage.copyright, FetchCopyrightSpec):
        copyright_fetch = spec.repackage.copyright.fetch
        context.report(
            "copyright_fetch_start",
            f"Fetching copyright for {context.deb_key.package}",
            copyright_fetch.url,
        )
        copyright_file = context.fetcher.fetch(
            url=copyright_fetch.url,
            sha256=copyright_fetch.sha256,
            destination=context.work_dir / "copyright-upstream",
            root=context.root,
        ).path

    payload_root = context.work_dir / "payload"
    if payload_root.exists():
        shutil.rmtree(payload_root)
    target = payload_root / spec.repackage.install_path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, target)
    target.chmod(0o755)
    if copyright_file is not None:
        copyright_target = (
            payload_root
            / "usr"
            / "share"
            / "doc"
            / context.deb_key.package
            / "copyright"
        )
        copyright_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(copyright_file, copyright_target)
        copyright_target.chmod(0o644)

    candidate = context.work_dir / (
        f"{context.deb_key.package}_{context.deb_key.deb_version}_"
        f"{context.deb_key.arch}.deb"
    )
    context.report("build_start", f"Building {candidate.name}", None)
    context.deb_tools.build_package(
        payload_root=payload_root,
        deb_key=context.deb_key,
        description=spec.repackage.metadata.description,
        homepage=spec.repackage.metadata.homepage,
        section=spec.repackage.metadata.section,
        multi_arch=spec.repackage.metadata.multi_arch,
        depends=spec.repackage.metadata.depends,
        output=candidate,
        work_dir=context.work_dir,
    )
    return TemplateBuildResult(
        candidate_deb=candidate,
        upstream_version=spec.upstream_version,
        revision=spec.revision,
        upstream=download.fact,
    )


def validate_archive_member_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        value == ""
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"path must be normalized and relative: {value}")
    return value
