from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from edgeapt.errors import ValidationError
from edgeapt.package_manifest import (
    index_package_stanzas,
    parse_debian_control,
    write_package_manifest,
)
from edgeapt.util import sha256_file
from tests.factories import make_artifact, make_deb_key, make_lock, make_publication


def test_parse_debian_control_preserves_continuation_lines() -> None:
    stanzas = parse_debian_control(
        "Package: tool\n"
        "Version: 1.0\n"
        "Architecture: amd64\n"
        "Description: summary\n"
        " detail line\n"
        " .\n"
        " next paragraph\n"
    )

    assert stanzas == (
        {
            "Package": "tool",
            "Version": "1.0",
            "Architecture": "amd64",
            "Description": "summary\ndetail line\n.\nnext paragraph",
        },
    )


def test_index_rejects_duplicate_package_identity(tmp_path: Path) -> None:
    stanza = {
        "Package": "tool",
        "Version": "1.0",
        "Architecture": "amd64",
    }

    with pytest.raises(ValidationError, match="duplicate package identity"):
        index_package_stanzas((stanza, stanza), source=tmp_path / "Packages")


def test_manifest_keeps_one_entry_per_publication(tmp_path: Path) -> None:
    key = make_deb_key(package="tool", deb_version="1.0-1")
    pool_path = tmp_path / "pool/main/t/tool/tool_1.0-1_amd64.deb"
    pool_path.parent.mkdir(parents=True)
    pool_path.write_bytes(b"deb payload")
    artifact = make_artifact(
        deb_key=key,
        sha256=sha256_file(pool_path),
        size=pool_path.stat().st_size,
    )
    lock = make_lock(
        artifacts=(artifact,),
        publications=(
            make_publication(deb_key=key, suite="jammy"),
            make_publication(deb_key=key, suite="noble"),
        ),
    )
    for suite in ("jammy", "noble"):
        packages = tmp_path / f"dists/{suite}/main/binary-amd64/Packages"
        packages.parent.mkdir(parents=True)
        packages.write_text(_packages_stanza(pool_path, tmp_path), encoding="utf-8")

    manifest_path = write_package_manifest(
        output_dir=tmp_path,
        profile="test",
        lock=lock,
    )
    raw = cast(dict[str, Any], json.loads(manifest_path.read_text(encoding="utf-8")))
    packages = cast(list[dict[str, Any]], raw["packages"])

    assert raw["schema"] == "edgeapt.packages/v1"
    assert raw["profile"] == "test"
    assert [item["suite"] for item in packages] == ["jammy", "noble"]
    assert packages[0] == {
        "arch": "amd64",
        "component": "main",
        "description": "Tool summary\nLong description",
        "filename": "pool/main/t/tool/tool_1.0-1_amd64.deb",
        "homepage": "https://example.invalid/tool",
        "package": "tool",
        "sha256": sha256_file(pool_path),
        "size": pool_path.stat().st_size,
        "suite": "jammy",
        "version": "1.0-1",
    }


def test_manifest_rejects_filename_outside_repository(tmp_path: Path) -> None:
    key = make_deb_key(package="tool", deb_version="1.0-1")
    payload = b"deb payload"
    artifact = make_artifact(
        deb_key=key,
        sha256=sha256_file(_write_file(tmp_path / "payload.deb", payload)),
        size=len(payload),
    )
    packages = tmp_path / "dists/noble/main/binary-amd64/Packages"
    packages.parent.mkdir(parents=True)
    packages.write_text(
        _packages_stanza(
            tmp_path / "payload.deb",
            tmp_path,
            filename="../payload.deb",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="invalid package Filename"):
        write_package_manifest(
            output_dir=tmp_path,
            profile="test",
            lock=make_lock(artifacts=(artifact,), publications=(make_publication(deb_key=key),)),
        )


def test_manifest_omits_unsafe_homepage(tmp_path: Path) -> None:
    key = make_deb_key(package="tool", deb_version="1.0-1")
    pool_path = tmp_path / "pool/main/t/tool/tool_1.0-1_amd64.deb"
    pool_path.parent.mkdir(parents=True)
    pool_path.write_bytes(b"deb payload")
    artifact = make_artifact(
        deb_key=key,
        sha256=sha256_file(pool_path),
        size=pool_path.stat().st_size,
    )
    packages_path = tmp_path / "dists/noble/main/binary-amd64/Packages"
    packages_path.parent.mkdir(parents=True)
    packages_path.write_text(
        _packages_stanza(pool_path, tmp_path).replace(
            "https://example.invalid/tool",
            "javascript:alert(1)",
        ),
        encoding="utf-8",
    )

    manifest_path = write_package_manifest(
        output_dir=tmp_path,
        profile="test",
        lock=make_lock(
            artifacts=(artifact,),
            publications=(make_publication(deb_key=key),),
        ),
    )
    raw = cast(dict[str, Any], json.loads(manifest_path.read_text(encoding="utf-8")))
    entries = cast(list[dict[str, Any]], raw["packages"])

    assert entries[0]["homepage"] is None


def _packages_stanza(
    pool_path: Path,
    root: Path,
    *,
    filename: str | None = None,
) -> str:
    return "\n".join(
        [
            "Package: tool",
            "Version: 1.0-1",
            "Architecture: amd64",
            f"Filename: {filename or pool_path.relative_to(root).as_posix()}",
            f"Size: {pool_path.stat().st_size}",
            f"SHA256: {sha256_file(pool_path).removeprefix('sha256:')}",
            "Description: Tool summary",
            "  Long description",
            "Homepage: https://example.invalid/tool",
            "",
        ]
    )


def _write_file(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path
