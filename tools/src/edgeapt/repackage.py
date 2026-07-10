from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import attrs

from edgeapt.constants import (
    LOCK_SCHEMA,
    PACKAGES_DIR,
    PROJECT_PATHS,
    ROOT,
    ProjectPaths,
)
from edgeapt.deb import build_binary_deb, read_deb_control, validate_deb_control
from edgeapt.errors import ValidationError
from edgeapt.fetch import fetch_upstream, prepare_single_binary
from edgeapt.lockfile import load_lock, write_lock
from edgeapt.models import (
    ArtifactFact,
    BuildUnit,
    LockedPublication,
    LockFile,
    SingleBinaryBuildSpec,
)
from edgeapt.planner import build_repo_plan
from edgeapt.sources import load_sources
from edgeapt.util import file_size, relative_to_root, sha256_file


@attrs.define(kw_only=True, frozen=True)
class RepackageEvent:
    kind: str
    source_id: str | None = None
    template: str | None = None
    package: str | None = None
    version: str | None = None
    arch: str | None = None
    path: str | None = None
    url: str | None = None
    size: int | None = None
    sha256: str | None = None
    message: str


@attrs.define(kw_only=True, frozen=True)
class CacheLookupResult:
    artifact: ArtifactFact | None
    reason: str


@attrs.define(kw_only=True, frozen=True)
class PruneResult:
    referenced: tuple[Path, ...]
    orphans: tuple[Path, ...]
    deleted: tuple[Path, ...]
    dry_run: bool


def repackage_all(
    on_event: Callable[[RepackageEvent], None] | None = None,
    *,
    paths: ProjectPaths = PROJECT_PATHS,
) -> LockFile:
    sources = load_sources(paths.sources_dir, root=paths.root)
    plan = build_repo_plan(sources)
    previous_lock = load_lock(paths.lock_path)
    previous_artifacts = (
        {artifact.deb_key: artifact for artifact in previous_lock.artifacts}
        if previous_lock is not None
        else {}
    )
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _emit(
        on_event,
        kind="sources_loaded",
        message=(
            f"Loaded {len(sources)} source(s), "
            f"{len(plan.builds)} build(s), "
            f"{len(plan.publications)} publication(s)"
        ),
    )

    artifacts: list[ArtifactFact] = []
    for build in plan.builds:
        artifacts.append(
            _execute_build(
                build=build,
                previous=previous_artifacts.get(build.deb_key),
                now=now,
                on_event=on_event,
                paths=paths,
            )
        )

    publications = tuple(
        LockedPublication(
            key=publication.key,
            artifact=publication.deb_key,
            provenance=publication.provenance,
            e2e_commands=publication.e2e_commands,
        )
        for publication in plan.publications
    )
    lock = LockFile(
        schema=LOCK_SCHEMA,
        generated_at=now,
        plan_digest=plan.plan_digest,
        artifacts=tuple(sorted(artifacts, key=lambda item: item.deb_key)),
        publications=publications,
    )
    _emit(on_event, kind="lock_write_start", message=f"Writing {paths.lock_path}")
    write_lock(lock, paths.lock_path)
    return lock


def _execute_build(
    *,
    build: BuildUnit,
    previous: ArtifactFact | None,
    now: str,
    on_event: Callable[[RepackageEvent], None] | None,
    paths: ProjectPaths,
) -> ArtifactFact:
    key = build.deb_key
    spec = build.build_spec
    provenance = build.provenance[0]
    artifact_abs = _artifact_path(build, paths)
    artifact_rel = relative_to_root(artifact_abs, paths.root)
    template = spec.template
    url = spec.fetch.url
    _emit(
        on_event,
        kind="source_start",
        source_id=provenance.source_id,
        template=template,
        package=key.package,
        message=f"Processing {key.package}",
    )
    _emit(
        on_event,
        kind="upstream_start",
        source_id=provenance.source_id,
        template=template,
        package=key.package,
        version=key.deb_version,
        arch=key.arch,
        path=artifact_rel,
        url=url,
        message=f"Repackaging {key.package} {key.deb_version} {key.arch}",
    )
    cache = _find_cached_artifact(
        previous=previous,
        build=build,
        path=artifact_abs,
        root=paths.root,
    )
    if cache.artifact is not None:
        artifact = cache.artifact
        _emit(
            on_event,
            kind="cache_hit",
            source_id=provenance.source_id,
            template=template,
            package=key.package,
            version=key.deb_version,
            arch=key.arch,
            path=artifact.path,
            url=url,
            size=artifact.size,
            sha256=artifact.sha256,
            message="hit",
        )
        _emit_done(on_event, build=build, artifact=artifact, url=url)
        return artifact

    _emit(
        on_event,
        kind="cache_miss",
        source_id=provenance.source_id,
        template=template,
        package=key.package,
        version=key.deb_version,
        arch=key.arch,
        path=artifact_rel,
        url=url,
        message=f"miss - {cache.reason}",
    )
    work_dir = (
        paths.tmp_dir / "repackage" / key.package / f"{key.deb_version}-{key.arch}"
    )
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    download_path = work_dir / "upstream"
    _emit(
        on_event,
        kind="fetch_start",
        source_id=provenance.source_id,
        template=template,
        package=key.package,
        version=key.deb_version,
        arch=key.arch,
        url=url,
        message=f"Fetching {key.package} {spec.upstream_version} {key.arch}",
    )
    download = fetch_upstream(spec.fetch, download_path, root=paths.root)

    deb_control = None
    revision = None
    if isinstance(spec, SingleBinaryBuildSpec):
        revision = spec.revision
        if spec.extract_path is not None:
            _emit(
                on_event,
                kind="extract_start",
                source_id=provenance.source_id,
                template=template,
                package=key.package,
                version=key.deb_version,
                arch=key.arch,
                message=spec.extract_path,
            )
        binary = prepare_single_binary(download.path, spec.extract_path, work_dir)
        candidate = work_dir / artifact_abs.name
        _emit(
            on_event,
            kind="build_start",
            source_id=provenance.source_id,
            template=template,
            package=key.package,
            version=key.deb_version,
            arch=key.arch,
            path=artifact_rel,
            message=f"Building {artifact_abs.name}",
        )
        build_binary_deb(
            binary=binary,
            package=key.package,
            version=key.deb_version,
            arch=key.arch,
            repackage=spec.repackage,
            output=candidate,
            work_dir=work_dir,
        )
        _install_artifact(candidate, artifact_abs, root=paths.root)
    else:
        _emit(
            on_event,
            kind="inspect_deb_start",
            source_id=provenance.source_id,
            template=template,
            package=key.package,
            version=key.deb_version,
            arch=key.arch,
            message=f"Inspecting upstream deb for {key.package}",
        )
        deb_control = read_deb_control(download.path)
        validate_deb_control(
            control=deb_control,
            package=key.package,
            version=key.deb_version,
            arch=key.arch,
        )
        _install_artifact(download.path, artifact_abs, root=paths.root)

    artifact = ArtifactFact(
        deb_key=key,
        build_plan_digest=build.plan_digest,
        upstream_version=spec.upstream_version,
        revision=revision,
        path=artifact_rel,
        sha256=sha256_file(artifact_abs),
        size=file_size(artifact_abs),
        upstream=download.fact,
        deb_control=deb_control,
        created_at=now,
    )
    _emit_done(on_event, build=build, artifact=artifact, url=url)
    return artifact


def prune_packages(
    lock: LockFile,
    *,
    dry_run: bool,
    packages_dir: Path = PACKAGES_DIR,
) -> PruneResult:
    referenced = tuple(sorted((ROOT / artifact.path).resolve() for artifact in lock.artifacts))
    if not packages_dir.exists():
        return PruneResult(referenced=referenced, orphans=(), deleted=(), dry_run=dry_run)

    all_debs = tuple(sorted(path.resolve() for path in packages_dir.rglob("*.deb")))
    referenced_set = set(referenced)
    orphans = tuple(path for path in all_debs if path not in referenced_set)
    deleted: list[Path] = []
    if not dry_run:
        for orphan in orphans:
            orphan.unlink()
            deleted.append(orphan)
        _remove_empty_package_dirs(packages_dir)
    return PruneResult(
        referenced=referenced,
        orphans=orphans,
        deleted=tuple(deleted),
        dry_run=dry_run,
    )


def _artifact_path(build: BuildUnit, paths: ProjectPaths) -> Path:
    key = build.deb_key
    return (
        paths.packages_dir
        / key.package
        / f"{key.package}_{key.deb_version}_{key.arch}.deb"
    )


def _find_cached_artifact(
    *,
    previous: ArtifactFact | None,
    build: BuildUnit,
    path: Path,
    root: Path,
) -> CacheLookupResult:
    if previous is None:
        return CacheLookupResult(artifact=None, reason="artifact not in previous lock")
    if previous.build_plan_digest != build.plan_digest:
        key = build.deb_key
        raise ValidationError(
            f"build plan changed for DebKey ({key.package}, {key.deb_version}, "
            f"{key.arch}); use a different deb_version"
        )
    expected_path = relative_to_root(path, root)
    if previous.path != expected_path:
        return CacheLookupResult(artifact=None, reason="artifact path changed")
    if not path.exists():
        return CacheLookupResult(artifact=None, reason="artifact missing")
    if sha256_file(path) != previous.sha256:
        return CacheLookupResult(artifact=None, reason="artifact digest changed")
    return CacheLookupResult(artifact=previous, reason="hit")


def _remove_empty_package_dirs(packages_dir: Path) -> None:
    if not packages_dir.exists():
        return
    for path in sorted(packages_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass


def _emit_done(
    on_event: Callable[[RepackageEvent], None] | None,
    *,
    build: BuildUnit,
    artifact: ArtifactFact,
    url: str,
) -> None:
    provenance = build.provenance[0]
    _emit(
        on_event,
        kind="artifact_done",
        source_id=provenance.source_id,
        template=build.build_spec.template,
        package=build.deb_key.package,
        version=build.deb_key.deb_version,
        arch=build.deb_key.arch,
        path=artifact.path,
        url=url,
        size=artifact.size,
        sha256=artifact.sha256,
        message=f"Finished {artifact.path}",
    )


def _emit(
    on_event: Callable[[RepackageEvent], None] | None,
    *,
    kind: str,
    message: str,
    source_id: str | None = None,
    template: str | None = None,
    package: str | None = None,
    version: str | None = None,
    arch: str | None = None,
    path: str | None = None,
    url: str | None = None,
    size: int | None = None,
    sha256: str | None = None,
) -> None:
    if on_event is None:
        return
    on_event(
        RepackageEvent(
            kind=kind,
            source_id=source_id,
            template=template,
            package=package,
            version=version,
            arch=arch,
            path=path,
            url=url,
            size=size,
            sha256=sha256,
            message=message,
        )
    )


def _install_artifact(candidate: Path, destination: Path, *, root: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing = sha256_file(destination)
        incoming = sha256_file(candidate)
        if existing != incoming:
            rel = relative_to_root(destination, root)
            raise ValidationError(
                f"artifact already exists with different content: {rel}; "
                "use a different deb_version"
            )
        return
    shutil.copy2(candidate, destination)
