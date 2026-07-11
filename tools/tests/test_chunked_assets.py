from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from edgeapt.chunked_assets import split_oversized_debs
from edgeapt.errors import ValidationError
from edgeapt.util import sha256_file


def test_keeps_assets_at_or_below_limit(tmp_path: Path) -> None:
    below = _write_deb(tmp_path, "below", b"1" * 9)
    exact = _write_deb(tmp_path, "exact", b"2" * 10)

    results = split_oversized_debs(
        output_dir=tmp_path,
        staging_dir=tmp_path / "staging",
        asset_limit=10,
        chunk_size=8,
    )

    assert results == ()
    assert below.is_file()
    assert exact.is_file()


def test_splits_and_verifies_oversized_deb(tmp_path: Path) -> None:
    content = b"0123456789abcdefghij!"
    artifact = _write_deb(tmp_path, "tool", content)
    expected_sha256 = sha256_file(artifact)

    result = split_oversized_debs(
        output_dir=tmp_path,
        staging_dir=tmp_path / "staging",
        asset_limit=10,
        chunk_size=8,
    )[0]

    assert not artifact.exists()
    assert result.path == "pool/main/t/tool/tool.deb"
    assert result.size == len(content)
    assert result.sha256 == expected_sha256
    assert [chunk.offset for chunk in result.chunks] == [0, 8, 16]
    assert [chunk.size for chunk in result.chunks] == [8, 8, 5]
    reconstructed = b"".join(
        (tmp_path / chunk.path.lstrip("/")).read_bytes()
        for chunk in result.chunks
    )
    assert reconstructed == content
    sidecar = cast(
        dict[str, Any],
        json.loads((tmp_path / result.sidecar_path).read_text(encoding="utf-8")),
    )
    assert sidecar["schema"] == "edgeapt.chunked-asset/v1"
    assert sidecar["path"] == "/pool/main/t/tool/tool.deb"
    assert sidecar["sha256"] == expected_sha256


def test_reuses_content_addressed_chunks(tmp_path: Path) -> None:
    content = b"same artifact content"
    first = _write_deb(tmp_path, "first", content)
    second = _write_deb(tmp_path, "second", content)

    results = split_oversized_debs(
        output_dir=tmp_path,
        staging_dir=tmp_path / "staging",
        asset_limit=10,
        chunk_size=8,
    )

    assert not first.exists()
    assert not second.exists()
    assert results[0].chunks == results[1].chunks
    chunk_files = tuple((tmp_path / "__edgeapt/chunks").rglob("*.part"))
    assert len(chunk_files) == 3


def test_chunk_limit_failure_preserves_original_deb(tmp_path: Path) -> None:
    artifact = _write_deb(tmp_path, "large", b"x" * 17)

    with pytest.raises(ValidationError, match="more than 2 chunks"):
        split_oversized_debs(
            output_dir=tmp_path,
            staging_dir=tmp_path / "staging",
            asset_limit=10,
            chunk_size=8,
            max_chunks=2,
        )

    assert artifact.read_bytes() == b"x" * 17
    assert not tuple((tmp_path / "__edgeapt").rglob("*.part"))


def _write_deb(root: Path, package: str, content: bytes) -> Path:
    path = root / f"pool/main/{package[0]}/{package}/{package}.deb"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path
