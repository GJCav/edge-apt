from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import attrs

from edgeapt.constants import LOCK_PATH, LOCK_SCHEMA, PACKAGES_DIR, ROOT, TMP_DIR
from edgeapt.deb import build_binary_deb, read_deb_control, validate_deb_control
from edgeapt.fetch import fetch_upstream, prepare_single_binary
from edgeapt.lockfile import load_lock, write_lock
from edgeapt.models import ArtifactFact, LockFile, SourceConfig, SourceLock, UpstreamConfig
from edgeapt.sources import artifact_path, artifact_version, load_sources
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
) -> LockFile:
    sources = load_sources()
    previous_lock = load_lock(LOCK_PATH)
    source_locks: dict[str, SourceLock] = {}
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _emit(
        on_event,
        kind="sources_loaded",
        message=(
            f"Loaded {len(sources)} source(s), "
            f"{sum(len(source.upstream) for source in sources)} artifact(s)"
        ),
    )

    for source in sources:
        _emit(
            on_event,
            kind="source_start",
            source_id=source.id,
            template=source.template,
            package=source.package,
            message=f"Processing {source.id}",
        )
        source_path = ROOT / source.source_file
        source_work_dir = TMP_DIR / "repackage" / source.id
        if source_work_dir.exists():
            shutil.rmtree(source_work_dir)
        source_work_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[ArtifactFact] = []

        for index, upstream in enumerate(source.upstream):
            version = artifact_version(source, upstream)
            artifact_rel = artifact_path(source, upstream)
            artifact_abs = ROOT / artifact_rel
            _emit(
                on_event,
                kind="upstream_start",
                source_id=source.id,
                template=source.template,
                package=source.package,
                version=version,
                arch=upstream.arch,
                path=artifact_rel.as_posix(),
                url=upstream.url,
                message=f"Repackaging {source.package} {version} {upstream.arch}",
            )
            cache_result = _find_cached_artifact(
                previous_lock=previous_lock,
                source=source,
                upstream=upstream,
                version=version,
                artifact_rel=artifact_rel,
            )
            if cache_result.artifact is not None:
                _emit(
                    on_event,
                    kind="cache_hit",
                    source_id=source.id,
                    template=source.template,
                    package=source.package,
                    version=version,
                    arch=upstream.arch,
                    path=cache_result.artifact.path,
                    url=upstream.url,
                    size=cache_result.artifact.size,
                    sha256=cache_result.artifact.sha256,
                    message="hit",
                )
                artifacts.append(cache_result.artifact)
                _emit(
                    on_event,
                    kind="artifact_done",
                    source_id=source.id,
                    template=source.template,
                    package=source.package,
                    version=version,
                    arch=upstream.arch,
                    path=cache_result.artifact.path,
                    url=upstream.url,
                    size=cache_result.artifact.size,
                    sha256=cache_result.artifact.sha256,
                    message=f"Finished {cache_result.artifact.path}",
                )
                continue

            _emit(
                on_event,
                kind="cache_miss",
                source_id=source.id,
                template=source.template,
                package=source.package,
                version=version,
                arch=upstream.arch,
                path=artifact_rel.as_posix(),
                url=upstream.url,
                message=f"miss - {cache_result.reason}",
            )
            item_work_dir = source_work_dir / f"{index}-{upstream.arch}"
            item_work_dir.mkdir(parents=True, exist_ok=True)
            download_path = item_work_dir / "upstream"
            _emit(
                on_event,
                kind="fetch_start",
                source_id=source.id,
                template=source.template,
                package=source.package,
                version=version,
                arch=upstream.arch,
                url=upstream.url,
                message=f"Fetching {source.package} {upstream.version} {upstream.arch}",
            )
            download = fetch_upstream(upstream, download_path)

            deb_control = None
            if source.template == "edgeapt.single_binary/v1":
                if source.repackage is None:
                    raise ValueError(f"{source.id}: missing repackage config")
                if upstream.extract_path is not None:
                    _emit(
                        on_event,
                        kind="extract_start",
                        source_id=source.id,
                        template=source.template,
                        package=source.package,
                        version=version,
                        arch=upstream.arch,
                        message=(
                            f"{upstream.extract_path}"
                        ),
                    )
                binary = prepare_single_binary(download.path, upstream, item_work_dir)
                candidate = item_work_dir / artifact_abs.name
                _emit(
                    on_event,
                    kind="build_start",
                    source_id=source.id,
                    template=source.template,
                    package=source.package,
                    version=version,
                    arch=upstream.arch,
                    path=artifact_rel.as_posix(),
                    message=f"Building {artifact_abs.name}",
                )
                build_binary_deb(
                    binary=binary,
                    package=source.package,
                    version=version,
                    arch=upstream.arch,
                    repackage=source.repackage,
                    output=candidate,
                    work_dir=item_work_dir,
                )
                _emit(
                    on_event,
                    kind="install_start",
                    source_id=source.id,
                    template=source.template,
                    package=source.package,
                    version=version,
                    arch=upstream.arch,
                    path=artifact_rel.as_posix(),
                    message=f"Installing {artifact_rel.as_posix()}",
                )
                _install_artifact(candidate, artifact_abs)
            else:
                _emit(
                    on_event,
                    kind="inspect_deb_start",
                    source_id=source.id,
                    template=source.template,
                    package=source.package,
                    version=version,
                    arch=upstream.arch,
                    message=f"Inspecting upstream deb for {source.package}",
                )
                deb_control = read_deb_control(download.path)
                validate_deb_control(
                    control=deb_control,
                    package=source.package,
                    version=version,
                    arch=upstream.arch,
                )
                _emit(
                    on_event,
                    kind="install_start",
                    source_id=source.id,
                    template=source.template,
                    package=source.package,
                    version=version,
                    arch=upstream.arch,
                    path=artifact_rel.as_posix(),
                    message=f"Installing {artifact_rel.as_posix()}",
                )
                _install_artifact(download.path, artifact_abs)

            artifact_sha256 = sha256_file(artifact_abs)
            artifact_size = file_size(artifact_abs)
            artifacts.append(
                ArtifactFact(
                    package=source.package,
                    version=version,
                    upstream_version=upstream.version,
                    revision=upstream.revision,
                    arch=upstream.arch,
                    suites=tuple(sorted(upstream.suites)),
                    path=artifact_rel.as_posix(),
                    sha256=artifact_sha256,
                    size=artifact_size,
                    upstream=download.fact,
                    deb_control=deb_control,
                    created_at=now,
                )
            )
            _emit(
                on_event,
                kind="artifact_done",
                source_id=source.id,
                template=source.template,
                package=source.package,
                version=version,
                arch=upstream.arch,
                path=artifact_rel.as_posix(),
                url=upstream.url,
                size=artifact_size,
                sha256=artifact_sha256,
                message=f"Finished {artifact_rel.as_posix()}",
            )

        source_locks[source.id] = SourceLock(
            source_file=source.source_file,
            source_sha256=sha256_file(source_path),
            template=source.template,
            package=source.package,
            e2e_command=source.e2e_command,
            artifacts=tuple(
                sorted(artifacts, key=lambda item: (item.package, item.version, item.arch))
            ),
        )

    lock = LockFile(schema=LOCK_SCHEMA, generated_at=now, sources=source_locks)
    _emit(on_event, kind="lock_write_start", message=f"Writing {LOCK_PATH}")
    write_lock(lock, LOCK_PATH)
    return lock


def prune_packages(
    lock: LockFile,
    *,
    dry_run: bool,
    packages_dir: Path = PACKAGES_DIR,
) -> PruneResult:
    referenced = tuple(sorted(_referenced_artifact_paths(lock)))
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


def _find_cached_artifact(
    *,
    previous_lock: LockFile | None,
    source: SourceConfig,
    upstream: UpstreamConfig,
    version: str,
    artifact_rel: Path,
) -> CacheLookupResult:
    if previous_lock is None:
        return CacheLookupResult(artifact=None, reason="no previous lock")
    previous_source = previous_lock.sources.get(source.id)
    if previous_source is None:
        return CacheLookupResult(artifact=None, reason="source not in previous lock")
    if previous_source.template != source.template:
        return CacheLookupResult(artifact=None, reason="template changed")
    if previous_source.package != source.package:
        return CacheLookupResult(artifact=None, reason="package changed")

    artifact_path_str = artifact_rel.as_posix()
    for artifact in previous_source.artifacts:
        if _artifact_matches_config(
            artifact=artifact,
            source=source,
            upstream=upstream,
            version=version,
            artifact_path_str=artifact_path_str,
        ):
            artifact_abs = ROOT / artifact.path
            if not artifact_abs.exists():
                return CacheLookupResult(artifact=None, reason="artifact missing")
            digest = sha256_file(artifact_abs)
            if digest != artifact.sha256:
                return CacheLookupResult(artifact=None, reason="artifact digest changed")
            return CacheLookupResult(
                artifact=attrs.evolve(artifact, suites=tuple(sorted(upstream.suites))),
                reason="hit",
            )
    return CacheLookupResult(artifact=None, reason="artifact not in previous lock")


def _referenced_artifact_paths(lock: LockFile) -> set[Path]:
    paths: set[Path] = set()
    for source_lock in lock.sources.values():
        for artifact in source_lock.artifacts:
            paths.add((ROOT / artifact.path).resolve())
    return paths


def _remove_empty_package_dirs(packages_dir: Path) -> None:
    if not packages_dir.exists():
        return
    for path in sorted(packages_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass


def _artifact_matches_config(
    *,
    artifact: ArtifactFact,
    source: SourceConfig,
    upstream: UpstreamConfig,
    version: str,
    artifact_path_str: str,
) -> bool:
    if artifact.path != artifact_path_str:
        return False
    if artifact.package != source.package:
        return False
    if artifact.version != version:
        return False
    if artifact.arch != upstream.arch:
        return False
    if artifact.upstream_version != upstream.version:
        return False
    if artifact.revision != upstream.revision:
        return False
    if artifact.upstream.url != upstream.url:
        return False
    if upstream.sha256 is not None and artifact.upstream.sha256 != upstream.sha256:
        return False
    return True


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


def _install_artifact(candidate: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        existing = sha256_file(destination)
        incoming = sha256_file(candidate)
        if existing != incoming:
            rel = relative_to_root(destination, ROOT)
            raise ValueError(f"artifact already exists with different content: {rel}")
        return
    shutil.copy2(candidate, destination)
