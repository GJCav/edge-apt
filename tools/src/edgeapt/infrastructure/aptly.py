from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from edgeapt.domain.artifacts import ArtifactFact
from edgeapt.domain.lock import LockFile
from edgeapt.project import ProjectPaths
from edgeapt.util import run, write_json


def publish_with_aptly(
    *,
    lock: LockFile,
    paths: ProjectPaths,
    profile: str,
    output_dir: Path,
    signing_key_fingerprint: str,
) -> None:
    aptly_root = paths.tmp_dir / f"aptly-{profile}"
    aptly_config = paths.tmp_dir / f"aptly-{profile}.conf"
    if aptly_root.exists():
        shutil.rmtree(aptly_root)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    aptly_root.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    config: dict[str, Any] = {
        "rootDir": aptly_root.as_posix(),
        "logLevel": "warning",
        "architectures": ["amd64", "arm64"],
        "skipLegacyPool": True,
        "gpgProvider": "gpg",
        "gpgDisableSign": False,
        "gpgDisableVerify": False,
        "FileSystemPublishEndpoints": {
            "local": {
                "rootDir": output_dir.resolve().as_posix(),
                "linkMethod": "copy",
                "verifyMethod": "md5",
            }
        },
    }
    write_json(aptly_config, config)

    artifacts_by_target: dict[tuple[str, str], list[ArtifactFact]] = defaultdict(
        list
    )
    for publication in lock.publications:
        target = (publication.key.suite, publication.key.component)
        artifacts_by_target[target].append(lock.artifact_for(publication.artifact))

    for suite, component in sorted(artifacts_by_target):
        repo_name = f"edgeapt-{suite}-{component}"
        snapshot_name = f"{repo_name}-snapshot"
        _aptly(
            aptly_config,
            "repo",
            "create",
            f"-distribution={suite}",
            f"-component={component}",
            "-architectures=amd64,arm64",
            repo_name,
        )
        package_paths = sorted(
            {
                str((paths.root / artifact.path).resolve())
                for artifact in artifacts_by_target[(suite, component)]
            }
        )
        if package_paths:
            _aptly(aptly_config, "repo", "add", repo_name, *package_paths)
        _aptly(
            aptly_config,
            "snapshot",
            "create",
            snapshot_name,
            "from",
            "repo",
            repo_name,
        )
        _aptly(
            aptly_config,
            "publish",
            "snapshot",
            "-batch",
            "-skip-contents",
            f"-gpg-key={signing_key_fingerprint}",
            "-architectures=amd64,arm64",
            f"-distribution={suite}",
            f"-component={component}",
            snapshot_name,
            "filesystem:local:",
        )


def _aptly(config: Path, *args: str) -> None:
    run(["aptly", f"-config={config}", *args])
