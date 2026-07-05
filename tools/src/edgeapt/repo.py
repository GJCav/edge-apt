from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

import attrs

from edgeapt.constants import COMPONENT, LOCK_PATH, PUBLIC_DIR, ROOT, TEST_PUBLIC_DIR, TMP_DIR
from edgeapt.errors import ValidationError
from edgeapt.keyring import is_test_key_fingerprint, load_test_signing_key
from edgeapt.lockfile import load_lock
from edgeapt.models import ArtifactFact
from edgeapt.util import require_executable, run, write_json


@attrs.define(kw_only=True, frozen=True)
class RepoGenerationResult:
    output_dir: Path
    signing_key_fingerprint: str
    profile: str


def generate_repo(
    *,
    profile: str,
    signing_key_fingerprint: str | None = None,
) -> RepoGenerationResult:
    require_executable("aptly")
    require_executable("gpg")
    output_dir, fingerprint = _resolve_profile(
        profile=profile,
        signing_key_fingerprint=signing_key_fingerprint,
    )
    lock = load_lock(LOCK_PATH)
    if lock is None:
        raise ValueError("lock.json does not exist; run repackage first")

    aptly_root = TMP_DIR / f"aptly-{profile}"
    aptly_config = TMP_DIR / f"aptly-{profile}.conf"
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

    artifacts_by_suite: dict[str, list[ArtifactFact]] = defaultdict(list)
    for source_lock in lock.sources.values():
        for artifact in source_lock.artifacts:
            for suite in artifact.suites:
                artifacts_by_suite[suite].append(artifact)

    for suite in sorted(artifacts_by_suite):
        repo_name = f"edgeapt-{suite}"
        snapshot_name = f"{repo_name}-snapshot"
        _aptly(
            aptly_config,
            "repo",
            "create",
            f"-distribution={suite}",
            f"-component={COMPONENT}",
            "-architectures=amd64,arm64",
            repo_name,
        )
        package_paths = sorted(
            {str((ROOT / artifact.path).resolve()) for artifact in artifacts_by_suite[suite]}
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
            f"-gpg-key={fingerprint}",
            "-architectures=amd64,arm64",
            f"-distribution={suite}",
            f"-component={COMPONENT}",
            snapshot_name,
            "filesystem:local:",
        )
    return RepoGenerationResult(
        output_dir=output_dir,
        signing_key_fingerprint=fingerprint,
        profile=profile,
    )


def _resolve_profile(
    *,
    profile: str,
    signing_key_fingerprint: str | None,
) -> tuple[Path, str]:
    if profile == "test":
        if signing_key_fingerprint is not None:
            raise ValidationError("test profile uses the test signing key automatically")
        return TEST_PUBLIC_DIR, load_test_signing_key().fingerprint
    if profile == "prod":
        if signing_key_fingerprint is None or signing_key_fingerprint == "":
            raise ValidationError(
                "prod profile requires --signing-key-fingerprint with a real key"
            )
        if is_test_key_fingerprint(signing_key_fingerprint):
            raise ValidationError("prod profile cannot use the test signing key")
        return PUBLIC_DIR, signing_key_fingerprint
    raise ValidationError("profile must be either test or prod")


def _aptly(config: Path, *args: str) -> None:
    run(["aptly", f"-config={config}", *args])
