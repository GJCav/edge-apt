from __future__ import annotations

from pathlib import Path

import attrs

from edgeapt.chunked_assets import ChunkedAssetFact, split_oversized_debs
from edgeapt.constants import (
    ROOT,
    STATIC_ASSET_SIZE_LIMIT_BYTES,
)
from edgeapt.errors import ValidationError
from edgeapt.infrastructure.aptly import publish_with_aptly
from edgeapt.infrastructure.lock_store import load_lock
from edgeapt.infrastructure.signing import load_signing_key, SigningKey
from edgeapt.install_page import write_install_page
from edgeapt.package_manifest import write_package_manifest
from edgeapt.project import EdgeAptProject, ProjectPaths, create_project
from edgeapt.util import require_executable
from edgeapt.workflows.planning import compile_project_plan


@attrs.define(kw_only=True, frozen=True)
class GenerateResult:
    output_dir: Path
    signing_key_fingerprint: str
    profile: str
    index_html: Path
    package_manifest: Path
    chunked_assets: tuple[ChunkedAssetFact, ...]


def generate_repository(
    *,
    profile: str,
    project: EdgeAptProject | None = None,
) -> GenerateResult:
    active_project = project or create_project(ROOT)
    paths = active_project.paths
    require_executable("aptly")
    require_executable("gpg")
    output_dir, signing_key = _resolve_profile(profile=profile, paths=paths)
    lock = load_lock(paths.lock_path)
    if lock is None:
        raise ValueError("lock.json does not exist; run repackage first")
    current_plan = compile_project_plan(active_project).plan
    if current_plan.plan_digest != lock.plan_digest:
        raise ValidationError("sources changed since lock.json; run repackage first")

    publish_with_aptly(
        lock=lock,
        paths=paths,
        profile=profile,
        output_dir=output_dir,
        signing_key_fingerprint=signing_key.fingerprint,
    )
    package_manifest = write_package_manifest(
        output_dir=output_dir,
        profile=profile,
        lock=lock,
    )
    install_page = write_install_page(
        output_dir=output_dir,
        signing_key=signing_key,
    )
    chunked_assets = split_oversized_debs(
        output_dir=output_dir,
        staging_dir=paths.tmp_dir / "chunked-assets",
    )
    check_static_asset_size_limit(output_dir)
    return GenerateResult(
        output_dir=output_dir,
        signing_key_fingerprint=signing_key.fingerprint,
        profile=profile,
        index_html=install_page.index_html,
        package_manifest=package_manifest,
        chunked_assets=chunked_assets,
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


def _format_mib(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.2f}"
