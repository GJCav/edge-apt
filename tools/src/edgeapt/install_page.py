from __future__ import annotations

import shutil
from pathlib import Path

import attrs

from edgeapt.infrastructure.signing import SigningKey

WEB_ROOT = Path(__file__).with_name("web")


@attrs.define(kw_only=True, frozen=True)
class InstallPageResult:
    index_html: Path
    public_ascii: Path
    public_keyring: Path


def write_install_page(
    *,
    output_dir: Path,
    signing_key: SigningKey,
) -> InstallPageResult:
    public_ascii = output_dir / "edgeapt.asc"
    public_keyring = output_dir / "edgeapt.gpg"
    shutil.copy2(signing_key.public_ascii, public_ascii)
    shutil.copy2(signing_key.public_keyring, public_keyring)

    index_html = output_dir / "index.html"
    shutil.copy2(WEB_ROOT / "index.html", index_html)
    shutil.copytree(WEB_ROOT / "assets", output_dir / "assets")
    return InstallPageResult(
        index_html=index_html,
        public_ascii=public_ascii,
        public_keyring=public_keyring,
    )
