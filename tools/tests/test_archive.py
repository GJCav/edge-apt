from __future__ import annotations

import gzip
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from edgeapt.errors import ValidationError
from edgeapt.infrastructure import archive as archive_module
from edgeapt.infrastructure.archive import DefaultArchiveExtractor


@pytest.mark.parametrize("archive_kind", ["tar", "tar.gz", "tar.xz", "zip"])
def test_extracts_same_payload_from_supported_archives(
    tmp_path: Path,
    archive_kind: str,
) -> None:
    archive = _write_archive(
        tmp_path / f"fixture.{archive_kind}",
        archive_kind,
        {"root/bin/tool": b"payload"},
    )

    extracted = DefaultArchiveExtractor().extract_regular_files(
        archive=archive,
        strip_components=1,
        paths=("bin/tool",),
        destination=tmp_path / "output",
    )

    assert extracted["bin/tool"].read_bytes() == b"payload"


def test_extracts_single_file_gzip_payload(tmp_path: Path) -> None:
    archive = tmp_path / "tool.gz"
    archive.write_bytes(gzip.compress(b"payload"))

    extracted = DefaultArchiveExtractor().extract_regular_files(
        archive=archive,
        strip_components=0,
        paths=("bin/tool",),
        destination=tmp_path / "output",
    )

    assert extracted["bin/tool"].read_bytes() == b"payload"


def test_single_file_gzip_rejects_strip_components(tmp_path: Path) -> None:
    archive = tmp_path / "tool.gz"
    archive.write_bytes(gzip.compress(b"payload"))

    with pytest.raises(ValidationError, match="strip_components"):
        DefaultArchiveExtractor().extract_regular_files(
            archive=archive,
            strip_components=1,
            paths=("tool",),
            destination=tmp_path / "output",
        )


def test_single_file_gzip_enforces_size_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "tool.gz"
    archive.write_bytes(gzip.compress(b"payload"))
    monkeypatch.setattr(archive_module, "MAX_ARCHIVE_MEMBER_SIZE", 1)

    with pytest.raises(ValidationError, match="size limit"):
        DefaultArchiveExtractor().extract_regular_files(
            archive=archive,
            strip_components=0,
            paths=("tool",),
            destination=tmp_path / "output",
        )

    assert not (tmp_path / "output" / "tool").exists()


def test_rejects_duplicate_path_after_strip(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.tar"
    with tarfile.open(archive, "w") as output:
        _add_tar_file(output, "first/tool", b"first")
        _add_tar_file(output, "second/tool", b"second")

    with pytest.raises(ValidationError, match="duplicate archive path"):
        DefaultArchiveExtractor().extract_regular_files(
            archive=archive,
            strip_components=1,
            paths=("tool",),
            destination=tmp_path / "output",
        )


def test_rejects_symlink_even_when_not_selected(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.tar"
    with tarfile.open(archive, "w") as output:
        _add_tar_file(output, "root/tool", b"payload")
        link = tarfile.TarInfo("root/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "tool"
        output.addfile(link)

    with pytest.raises(ValidationError, match="not a regular file"):
        DefaultArchiveExtractor().extract_regular_files(
            archive=archive,
            strip_components=1,
            paths=("tool",),
            destination=tmp_path / "output",
        )


def test_rejects_hardlink_even_when_not_selected(tmp_path: Path) -> None:
    archive = tmp_path / "hardlink.tar"
    with tarfile.open(archive, "w") as output:
        _add_tar_file(output, "root/tool", b"payload")
        link = tarfile.TarInfo("root/link")
        link.type = tarfile.LNKTYPE
        link.linkname = "root/tool"
        output.addfile(link)

    with pytest.raises(ValidationError, match="not a regular file"):
        DefaultArchiveExtractor().extract_regular_files(
            archive=archive,
            strip_components=1,
            paths=("tool",),
            destination=tmp_path / "output",
        )


def test_rejects_zip_symlink_even_when_not_selected(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("root/tool", b"payload")
        link = zipfile.ZipInfo("root/link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        output.writestr(link, "tool")

    with pytest.raises(ValidationError, match="not a regular file"):
        DefaultArchiveExtractor().extract_regular_files(
            archive=archive,
            strip_components=1,
            paths=("tool",),
            destination=tmp_path / "output",
        )


def test_enforces_member_count_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _write_archive(
        tmp_path / "members.zip",
        "zip",
        {"one": b"1", "two": b"2"},
    )
    monkeypatch.setattr(archive_module, "MAX_ARCHIVE_MEMBERS", 1)

    with pytest.raises(ValidationError, match="member count"):
        DefaultArchiveExtractor().extract_regular_files(
            archive=archive,
            strip_components=0,
            paths=("one",),
            destination=tmp_path / "output",
        )


def test_enforces_individual_member_size_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _write_archive(
        tmp_path / "member-size.zip",
        "zip",
        {"tool": b"12"},
    )
    monkeypatch.setattr(archive_module, "MAX_ARCHIVE_MEMBER_SIZE", 1)

    with pytest.raises(ValidationError, match="member exceeds size limit"):
        DefaultArchiveExtractor().extract_regular_files(
            archive=archive,
            strip_components=0,
            paths=("tool",),
            destination=tmp_path / "output",
        )


def test_enforces_selected_total_size_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = _write_archive(
        tmp_path / "size.zip",
        "zip",
        {"one": b"12", "two": b"34"},
    )
    monkeypatch.setattr(archive_module, "MAX_SELECTED_TOTAL_SIZE", 3)

    with pytest.raises(ValidationError, match="total size"):
        DefaultArchiveExtractor().extract_regular_files(
            archive=archive,
            strip_components=0,
            paths=("one", "two"),
            destination=tmp_path / "output",
        )


def _write_archive(
    path: Path,
    kind: str,
    files: dict[str, bytes],
) -> Path:
    if kind == "zip":
        with zipfile.ZipFile(path, "w") as output:
            for name, content in files.items():
                output.writestr(name, content)
        return path
    if kind == "tar":
        output = tarfile.open(path, "w")
    elif kind == "tar.gz":
        output = tarfile.open(path, "w:gz")
    else:
        output = tarfile.open(path, "w:xz")
    with output:
        for name, content in files.items():
            _add_tar_file(output, name, content)
    return path


def _add_tar_file(output: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    output.addfile(info, io.BytesIO(content))
