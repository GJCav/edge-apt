from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from edgeapt.constants import LOCK_PATH, LOCK_SCHEMA, ROOT, TMP_DIR
from edgeapt.deb import build_binary_deb, read_deb_control, validate_deb_control
from edgeapt.fetch import fetch_upstream, prepare_single_binary
from edgeapt.lockfile import write_lock
from edgeapt.models import ArtifactFact, LockFile, SourceLock
from edgeapt.sources import artifact_path, artifact_version, load_sources
from edgeapt.util import file_size, relative_to_root, sha256_file


def repackage_all() -> LockFile:
    sources = load_sources()
    source_locks: dict[str, SourceLock] = {}
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    for source in sources:
        source_path = ROOT / source.source_file
        source_work_dir = TMP_DIR / "repackage" / source.id
        if source_work_dir.exists():
            shutil.rmtree(source_work_dir)
        source_work_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[ArtifactFact] = []

        for index, upstream in enumerate(source.upstream):
            item_work_dir = source_work_dir / f"{index}-{upstream.arch}"
            item_work_dir.mkdir(parents=True, exist_ok=True)
            download_path = item_work_dir / "upstream"
            download = fetch_upstream(upstream, download_path)
            version = artifact_version(source, upstream)
            artifact_rel = artifact_path(source, upstream)
            artifact_abs = ROOT / artifact_rel

            deb_control = None
            if source.template == "edgeapt.single_binary/v1":
                if source.repackage is None:
                    raise ValueError(f"{source.id}: missing repackage config")
                binary = prepare_single_binary(download.path, upstream, item_work_dir)
                candidate = item_work_dir / artifact_abs.name
                build_binary_deb(
                    binary=binary,
                    package=source.package,
                    version=version,
                    arch=upstream.arch,
                    repackage=source.repackage,
                    output=candidate,
                    work_dir=item_work_dir,
                )
                _install_artifact(candidate, artifact_abs)
            else:
                deb_control = read_deb_control(download.path)
                validate_deb_control(
                    control=deb_control,
                    package=source.package,
                    version=version,
                    arch=upstream.arch,
                )
                _install_artifact(download.path, artifact_abs)

            artifacts.append(
                ArtifactFact(
                    package=source.package,
                    version=version,
                    upstream_version=upstream.version,
                    revision=upstream.revision,
                    arch=upstream.arch,
                    suites=tuple(sorted(upstream.suites)),
                    path=artifact_rel.as_posix(),
                    sha256=sha256_file(artifact_abs),
                    size=file_size(artifact_abs),
                    upstream=download.fact,
                    deb_control=deb_control,
                    created_at=now,
                )
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
    write_lock(lock, LOCK_PATH)
    return lock


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
