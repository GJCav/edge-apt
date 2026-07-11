from __future__ import annotations

import os
import shutil
from pathlib import Path

from edgeapt.domain.artifacts import DebControlFact
from edgeapt.domain.keys import DebKey
from edgeapt.util import run


class DefaultDebTools:
    def build_single_binary(
        self,
        *,
        binary: Path,
        deb_key: DebKey,
        install_path: str,
        description: str,
        homepage: str | None,
        output: Path,
        work_dir: Path,
    ) -> None:
        source_date_epoch = 1704067200
        root = work_dir / "pkgroot"
        if root.exists():
            shutil.rmtree(root)
        debian_dir = root / "DEBIAN"
        debian_dir.mkdir(parents=True, exist_ok=True)

        target = root / install_path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binary, target)
        target.chmod(0o755)

        control_lines = [
            f"Package: {deb_key.package}",
            f"Version: {deb_key.deb_version}",
            "Section: utils",
            "Priority: optional",
            f"Architecture: {deb_key.arch}",
            "Maintainer: EdgeAPT <edgeapt@example.invalid>",
            f"Description: {description}",
        ]
        if homepage is not None:
            control_lines.append(f"Homepage: {homepage}")
        (debian_dir / "control").write_text(
            "\n".join(control_lines) + "\n", encoding="utf-8"
        )

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

    def read_control(self, path: Path) -> DebControlFact:
        return DebControlFact(
            package=_dpkg_field(path, "Package"),
            version=_dpkg_field(path, "Version"),
            architecture=_dpkg_field(path, "Architecture"),
        )


def _dpkg_field(path: Path, field: str) -> str:
    result = run(["dpkg-deb", "-f", path, field])
    return result.stdout.strip()
