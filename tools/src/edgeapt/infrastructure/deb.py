from __future__ import annotations

import os
import shutil
from pathlib import Path

from edgeapt.domain.artifacts import DebControlFact
from edgeapt.domain.keys import DebKey
from edgeapt.util import run


class DefaultDebTools:
    def build_package(
        self,
        *,
        payload_root: Path,
        deb_key: DebKey,
        description: str,
        homepage: str | None,
        output: Path,
        work_dir: Path,
    ) -> None:
        source_date_epoch = 1704067200
        root = work_dir / "pkgroot"
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(payload_root, root)
        debian_dir = root / "DEBIAN"
        debian_dir.mkdir(parents=True, exist_ok=True)

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
        control_path = debian_dir / "control"
        control_path.write_text(
            "\n".join(control_lines) + "\n", encoding="utf-8"
        )
        control_path.chmod(0o644)

        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()
        for path in root.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
            os.utime(path, (source_date_epoch, source_date_epoch))
        root.chmod(0o755)
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
