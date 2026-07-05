from __future__ import annotations

import os
import shutil
from pathlib import Path

from edgeapt.errors import ValidationError
from edgeapt.models import DebControlFact, RepackageConfig
from edgeapt.util import run


def build_binary_deb(
    *,
    binary: Path,
    package: str,
    version: str,
    arch: str,
    repackage: RepackageConfig,
    output: Path,
    work_dir: Path,
) -> None:
    source_date_epoch = 1704067200
    root = work_dir / "pkgroot"
    if root.exists():
        shutil.rmtree(root)
    debian_dir = root / "DEBIAN"
    debian_dir.mkdir(parents=True, exist_ok=True)

    install_path = repackage.install_path.lstrip("/")
    target = root / install_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, target)
    target.chmod(0o755)

    control_lines = [
        f"Package: {package}",
        f"Version: {version}",
        "Section: utils",
        "Priority: optional",
        f"Architecture: {arch}",
        "Maintainer: EdgeAPT <edgeapt@example.invalid>",
        f"Description: {repackage.metadata.description}",
    ]
    if repackage.metadata.homepage is not None:
        control_lines.append(f"Homepage: {repackage.metadata.homepage}")
    (debian_dir / "control").write_text("\n".join(control_lines) + "\n", encoding="utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    for path in root.rglob("*"):
        os.utime(path, (source_date_epoch, source_date_epoch))
    os.utime(root, (source_date_epoch, source_date_epoch))
    run(
        ["dpkg-deb", "--build", "--root-owner-group", root, output],
        env={"SOURCE_DATE_EPOCH": str(source_date_epoch)},
    )


def read_deb_control(path: Path) -> DebControlFact:
    package = _dpkg_field(path, "Package")
    version = _dpkg_field(path, "Version")
    architecture = _dpkg_field(path, "Architecture")
    return DebControlFact(package=package, version=version, architecture=architecture)


def validate_deb_control(
    *,
    control: DebControlFact,
    package: str,
    version: str,
    arch: str,
) -> None:
    if control.package != package:
        raise ValidationError(f"Package mismatch: expected {package}, got {control.package}")
    if control.version != version:
        raise ValidationError(f"Version mismatch: expected {version}, got {control.version}")
    if control.architecture != arch:
        raise ValidationError(
            f"Architecture mismatch: expected {arch}, got {control.architecture}"
        )


def _dpkg_field(path: Path, field: str) -> str:
    result = run(["dpkg-deb", "-f", path, field])
    return result.stdout.strip()
