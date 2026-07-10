from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from edgeapt.errors import ValidationError
from edgeapt.fetch import prepare_single_binary


def test_prepare_single_binary_extracts_zip_member(tmp_path: Path) -> None:
    archive = tmp_path / "jless.zip"
    with zipfile.ZipFile(archive, "w") as zip_archive:
        zip_archive.writestr("jless", b"#!/bin/sh\n")

    binary = prepare_single_binary(
        archive,
        "jless",
        tmp_path / "work",
    )

    assert binary.read_bytes() == b"#!/bin/sh\n"
    assert binary == tmp_path / "work" / "extract" / "jless"


def test_prepare_single_binary_rejects_missing_zip_member(tmp_path: Path) -> None:
    archive = tmp_path / "jless.zip"
    with zipfile.ZipFile(archive, "w") as zip_archive:
        zip_archive.writestr("other", b"content")

    with pytest.raises(ValidationError, match="extract_path not found"):
        prepare_single_binary(archive, "jless", tmp_path / "work")


def test_prepare_single_binary_rejects_escaping_extract_path(tmp_path: Path) -> None:
    archive = tmp_path / "jless.zip"
    with zipfile.ZipFile(archive, "w") as zip_archive:
        zip_archive.writestr("../jless", b"content")

    with pytest.raises(ValidationError, match="escapes"):
        prepare_single_binary(archive, "../jless", tmp_path / "work")
