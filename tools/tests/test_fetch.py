from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from edgeapt.errors import ValidationError
from edgeapt.infrastructure.fetcher import DefaultFetcher


def test_prepare_single_binary_extracts_zip_member(tmp_path: Path) -> None:
    archive = tmp_path / "jless.zip"
    with zipfile.ZipFile(archive, "w") as zip_archive:
        zip_archive.writestr("jless", b"#!/bin/sh\n")

    binary = DefaultFetcher().prepare_single_binary(
        downloaded=archive,
        extract_path="jless",
        work_dir=tmp_path / "work",
    )

    assert binary.read_bytes() == b"#!/bin/sh\n"
    assert binary == tmp_path / "work" / "extract" / "jless"


def test_prepare_single_binary_rejects_missing_zip_member(tmp_path: Path) -> None:
    archive = tmp_path / "jless.zip"
    with zipfile.ZipFile(archive, "w") as zip_archive:
        zip_archive.writestr("other", b"content")

    with pytest.raises(ValidationError, match="extract_path not found"):
        DefaultFetcher().prepare_single_binary(
            downloaded=archive,
            extract_path="jless",
            work_dir=tmp_path / "work",
        )


def test_prepare_single_binary_rejects_escaping_extract_path(tmp_path: Path) -> None:
    archive = tmp_path / "jless.zip"
    with zipfile.ZipFile(archive, "w") as zip_archive:
        zip_archive.writestr("../jless", b"content")

    with pytest.raises(ValidationError, match="escapes"):
        DefaultFetcher().prepare_single_binary(
            downloaded=archive,
            extract_path="../jless",
            work_dir=tmp_path / "work",
        )
