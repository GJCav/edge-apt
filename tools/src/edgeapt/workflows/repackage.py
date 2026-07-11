from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import attrs

from edgeapt.constants import (
    LOCK_SCHEMA,
    PACKAGES_DIR,
    ROOT,
)
from edgeapt.domain.artifacts import ArtifactFact, DebControlFact
from edgeapt.domain.lock import LockedPublication, LockFile
from edgeapt.domain.planning import BuildUnit
from edgeapt.errors import ValidationError
from edgeapt.infrastructure.lock_store import load_lock, write_lock
from edgeapt.project import EdgeAptProject, ProjectPaths, create_project
from edgeapt.templates.base import BuildContext
from edgeapt.util import file_size, relative_to_root, sha256_file
from edgeapt.workflows.planning import compile_project_plan

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
    source_count: int | None = None
    build_count: int | None = None
    publication_count: int | None = None
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


@attrs.define(kw_only=True, frozen=True)
class RepackageResult:
    lock: LockFile
    source_count: int
    build_count: int
    publication_count: int


def repackage_project(
    on_event: Callable[[RepackageEvent], None] | None = None,
    *,
    project: EdgeAptProject | None = None,
) -> RepackageResult:
    active_project = project or create_project(ROOT)
    paths = active_project.paths
    planning = compile_project_plan(active_project)
    plan = planning.plan
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
        source_count=planning.source_count,
        build_count=len(plan.builds),
        publication_count=len(plan.publications),
        message=(
            f"Loaded {planning.source_count} source(s), "
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
                project=active_project,
            )
        )

    publications = tuple(
        LockedPublication(
            key=publication.key,
            artifact=publication.deb_key,
            e2e_claims=publication.e2e_claims,
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
    return RepackageResult(
        lock=lock,
        source_count=planning.source_count,
        build_count=len(plan.builds),
        publication_count=len(plan.publications),
    )


def _execute_build(
    *,
    build: BuildUnit,
    previous: ArtifactFact | None,
    now: str,
    on_event: Callable[[RepackageEvent], None] | None,
    project: EdgeAptProject,
) -> ArtifactFact:
    paths = project.paths
    key = build.deb_key
    spec = build.build_spec
    provenance = build.provenance[0]
    artifact_abs = _artifact_path(build, paths)
    artifact_rel = relative_to_root(artifact_abs, paths.root)
    template = spec.template_id
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
            url=artifact.upstream.url,
            size=artifact.size,
            sha256=artifact.sha256,
            message="hit",
        )
        _emit_done(
            on_event,
            build=build,
            artifact=artifact,
            url=artifact.upstream.url,
        )
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
        message=f"miss - {cache.reason}",
    )
    work_dir = (
        paths.tmp_dir / "repackage" / key.package / f"{key.deb_version}-{key.arch}"
    )
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    def report(kind: str, message: str, url: str | None) -> None:
        _emit(
            on_event,
            kind=kind,
            source_id=provenance.source_id,
            template=template,
            package=key.package,
            version=key.deb_version,
            arch=key.arch,
            path=artifact_rel if kind == "build_start" else None,
            url=url,
            message=message,
        )

    template_type = project.templates.resolve(spec.template_id)
    result = template_type.build(
        spec,
        BuildContext(
            deb_key=key,
            root=paths.root,
            work_dir=work_dir,
            fetcher=project.fetcher,
            archive_extractor=project.archive_extractor,
            deb_tools=project.deb_tools,
            report=report,
        ),
    )
    report(
        "inspect_deb_start",
        f"Inspecting candidate deb for {key.package}",
        None,
    )
    actual_control = project.deb_tools.read_control(result.candidate_deb)
    _validate_candidate_control(build=build, control=actual_control)
    _install_artifact(result.candidate_deb, artifact_abs, root=paths.root)

    artifact = ArtifactFact(
        deb_key=key,
        build_plan_digest=build.plan_digest,
        upstream_version=result.upstream_version,
        revision=result.revision,
        path=artifact_rel,
        sha256=sha256_file(artifact_abs),
        size=file_size(artifact_abs),
        upstream=result.upstream,
        deb_control=actual_control,
        created_at=now,
    )
    _emit_done(
        on_event,
        build=build,
        artifact=artifact,
        url=result.upstream.url,
    )
    return artifact


def _validate_candidate_control(
    *,
    build: BuildUnit,
    control: DebControlFact,
) -> None:
    key = build.deb_key
    if control.package != key.package:
        raise ValidationError(
            f"Package mismatch: expected {key.package}, got {control.package}"
        )
    if control.version != key.deb_version:
        raise ValidationError(
            f"Version mismatch: expected {key.deb_version}, got {control.version}"
        )
    if control.architecture != key.arch:
        raise ValidationError(
            f"Architecture mismatch: expected {key.arch}, got {control.architecture}"
        )


def prune_packages(
    lock: LockFile,
    *,
    dry_run: bool,
    packages_dir: Path = PACKAGES_DIR,
    root: Path = ROOT,
) -> PruneResult:
    referenced = tuple(
        sorted((root / artifact.path).resolve() for artifact in lock.artifacts)
    )
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
        template=build.build_spec.template_id,
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
    source_count: int | None = None,
    build_count: int | None = None,
    publication_count: int | None = None,
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
            source_count=source_count,
            build_count=build_count,
            publication_count=publication_count,
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
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(candidate, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
