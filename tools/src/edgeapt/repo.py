from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import attrs

from edgeapt.constants import (
    PROJECT_PATHS,
    ProjectPaths,
    STATIC_ASSET_SIZE_LIMIT_BYTES,
)
from edgeapt.errors import ValidationError
from edgeapt.install_page import write_install_page
from edgeapt.keyring import load_signing_key, SigningKey
from edgeapt.lockfile import load_lock
from edgeapt.models import ArtifactFact
from edgeapt.planner import build_repo_plan
from edgeapt.sources import load_sources
from edgeapt.util import require_executable, run, write_json


@attrs.define(kw_only=True, frozen=True)
class RepoGenerationResult:
    output_dir: Path
    signing_key_fingerprint: str
    profile: str
    index_html: Path


def generate_repo(
    *,
    profile: str,
    paths: ProjectPaths = PROJECT_PATHS,
) -> RepoGenerationResult:
    require_executable("aptly")
    require_executable("gpg")
    output_dir, signing_key = _resolve_profile(profile=profile, paths=paths)
    lock = load_lock(paths.lock_path)
    if lock is None:
        raise ValueError("lock.json does not exist; run repackage first")
    current_plan = build_repo_plan(load_sources(paths.sources_dir, root=paths.root))
    if current_plan.plan_digest != lock.plan_digest:
        raise ValidationError("sources changed since lock.json; run repackage first")

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

    artifacts_by_target: dict[tuple[str, str], list[ArtifactFact]] = defaultdict(list)
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
        _aptly(aptly_config, "snapshot", "create", snapshot_name, "from", "repo", repo_name)
        _aptly(
            aptly_config,
            "publish",
            "snapshot",
            "-batch",
            "-skip-contents",
            f"-gpg-key={signing_key.fingerprint}",
            "-architectures=amd64,arm64",
            f"-distribution={suite}",
            f"-component={component}",
            snapshot_name,
            "filesystem:local:",
        )
    install_page = write_install_page(
        output_dir=output_dir,
        profile=profile,
        lock=lock,
        signing_key=signing_key,
    )
    check_static_asset_size_limit(output_dir)
    return RepoGenerationResult(
        output_dir=output_dir,
        signing_key_fingerprint=signing_key.fingerprint,
        profile=profile,
        index_html=install_page.index_html,
    )


def check_static_asset_size_limit(
    output_dir: Path,
    *,
    limit_bytes: int = STATIC_ASSET_SIZE_LIMIT_BYTES,
) -> None:
    oversized: list[tuple[Path, int]] = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file():
            size = path.stat().st_size
            if size > limit_bytes:
                oversized.append((path, size))
    if not oversized:
        return

    limit_mib = _format_mib(limit_bytes)
    lines = [
        "Static asset size limit exceeded:",
        *[
            f"- {path}: {_format_mib(size)} MiB > {limit_mib} MiB"
            for path, size in oversized
        ],
    ]
    raise ValidationError("\n".join(lines))


def _resolve_profile(
    *,
    profile: str,
    paths: ProjectPaths,
) -> tuple[Path, SigningKey]:
    if profile == "test":
        return paths.test_public_dir, load_signing_key(profile)
    if profile == "prod":
        return paths.public_dir, load_signing_key(profile)
    raise ValidationError("profile must be either test or prod")


def _aptly(config: Path, *args: str) -> None:
    run(["aptly", f"-config={config}", *args])


def _format_mib(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.2f}"
