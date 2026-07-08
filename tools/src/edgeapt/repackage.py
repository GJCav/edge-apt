from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import attrs

from edgeapt.constants import LOCK_PATH, LOCK_SCHEMA, ROOT, TMP_DIR
from edgeapt.deb import build_binary_deb, read_deb_control, validate_deb_control
from edgeapt.fetch import fetch_upstream, prepare_single_binary
from edgeapt.lockfile import write_lock
from edgeapt.models import ArtifactFact, LockFile, SourceLock
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


def repackage_all(
    on_event: Callable[[RepackageEvent], None] | None = None,
) -> LockFile:
    sources = load_sources()
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
            artifacts=tuple(
                sorted(artifacts, key=lambda item: (item.package, item.version, item.arch))
            ),
        )

    lock = LockFile(schema=LOCK_SCHEMA, generated_at=now, sources=source_locks)
    _emit(on_event, kind="lock_write_start", message=f"Writing {LOCK_PATH}")
    write_lock(lock, LOCK_PATH)
    return lock


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
