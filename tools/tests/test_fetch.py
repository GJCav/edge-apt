from __future__ import annotations

import io
import urllib.request
from email.message import Message
import zipfile
from pathlib import Path

import pytest

from edgeapt.errors import ValidationError
from edgeapt.infrastructure.archive import DefaultArchiveExtractor
from edgeapt.infrastructure.fetcher import DefaultFetcher


def test_http_fetch_uses_explicit_user_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[urllib.request.Request] = []

    class Response(io.BytesIO):
        headers = Message()

    def open_request(request: urllib.request.Request) -> Response:
        captured.append(request)
        return Response(b"payload")

    monkeypatch.setattr(urllib.request, "urlopen", open_request)

    result = DefaultFetcher().fetch(
        url="https://example.invalid/tool",
        sha256=None,
        destination=tmp_path / "tool",
        root=tmp_path,
    )

    assert result.path.read_bytes() == b"payload"
    assert captured[0].get_header("User-agent") == "EdgeAPT/0.1"


def test_archive_extractor_extracts_zip_member(tmp_path: Path) -> None:
    archive = tmp_path / "jless.zip"
    with zipfile.ZipFile(archive, "w") as zip_archive:
        zip_archive.writestr("jless", b"#!/bin/sh\n")

    extracted = DefaultArchiveExtractor().extract_regular_files(
        archive=archive,
        strip_components=0,
        paths=("jless",),
        destination=tmp_path / "work",
    )
    binary = extracted["jless"]

    assert binary.read_bytes() == b"#!/bin/sh\n"
    assert binary == tmp_path / "work" / "jless"


def test_archive_extractor_rejects_missing_zip_member(tmp_path: Path) -> None:
    archive = tmp_path / "jless.zip"
    with zipfile.ZipFile(archive, "w") as zip_archive:
        zip_archive.writestr("other", b"content")

    with pytest.raises(ValidationError, match="not found after strip"):
        DefaultArchiveExtractor().extract_regular_files(
            archive=archive,
            strip_components=0,
            paths=("jless",),
            destination=tmp_path / "work",
        )


def test_archive_extractor_rejects_escaping_member(tmp_path: Path) -> None:
    archive = tmp_path / "jless.zip"
    with zipfile.ZipFile(archive, "w") as zip_archive:
        zip_archive.writestr("../jless", b"content")

    with pytest.raises(ValidationError, match=r"contains '\.\.'"):
        DefaultArchiveExtractor().extract_regular_files(
            archive=archive,
            strip_components=0,
            paths=("jless",),
            destination=tmp_path / "work",
        )
