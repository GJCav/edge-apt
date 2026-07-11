from __future__ import annotations

import gzip
import shutil
import stat
import tarfile
import zipfile
import zlib
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath

from edgeapt.errors import ValidationError

MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_MEMBER_SIZE = 512 * 1024 * 1024
MAX_SELECTED_TOTAL_SIZE = 1024 * 1024 * 1024


class DefaultArchiveExtractor:
    def extract_regular_files(
        self,
        *,
        archive: Path,
        strip_components: int,
        paths: tuple[str, ...],
        destination: Path,
    ) -> Mapping[str, Path]:
        if strip_components < 0:
            raise ValidationError("strip_components must be non-negative")
        requested = tuple(_validate_requested_path(path) for path in paths)
        if len(set(requested)) != len(requested):
            raise ValidationError("archive paths must be unique")
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)

        if tarfile.is_tarfile(archive):
            with tarfile.open(archive, mode="r:*") as opened:
                return _extract_tar(
                    opened,
                    strip_components=strip_components,
                    requested=requested,
                    destination=destination,
                )
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as opened:
                return _extract_zip(
                    opened,
                    strip_components=strip_components,
                    requested=requested,
                    destination=destination,
                )
        if _is_gzip(archive):
            return _extract_gzip(
                archive,
                strip_components=strip_components,
                requested=requested,
                destination=destination,
            )
        raise ValidationError("unsupported archive format")


def _extract_tar(
    archive: tarfile.TarFile,
    *,
    strip_components: int,
    requested: tuple[str, ...],
    destination: Path,
) -> Mapping[str, Path]:
    members = archive.getmembers()
    _validate_member_count(len(members))
    index: dict[str, tarfile.TarInfo] = {}
    for member in members:
        stripped = _stripped_member_path(member.name, strip_components)
        if member.isdir():
            continue
        if not member.isfile():
            raise ValidationError(
                f"archive member is not a regular file: {member.name}"
            )
        _validate_member_size(member.name, member.size)
        if stripped is not None:
            _add_to_index(index, stripped, member, original=member.name)

    def copy_member(name: str, target: Path) -> None:
        source = archive.extractfile(index[name])
        if source is None:
            raise ValidationError(f"failed to read archive member: {name}")
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output)

    sizes = {name: member.size for name, member in index.items()}
    return _materialize(
        requested=requested,
        destination=destination,
        sizes=sizes,
        copy_member=copy_member,
    )


def _extract_zip(
    archive: zipfile.ZipFile,
    *,
    strip_components: int,
    requested: tuple[str, ...],
    destination: Path,
) -> Mapping[str, Path]:
    members = archive.infolist()
    _validate_member_count(len(members))
    index: dict[str, zipfile.ZipInfo] = {}
    for member in members:
        stripped = _stripped_member_path(member.filename, strip_components)
        if member.is_dir():
            continue
        if member.flag_bits & 0x1:
            raise ValidationError(
                f"encrypted archive member is unsupported: {member.filename}"
            )
        mode = (member.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG}:
            raise ValidationError(
                f"archive member is not a regular file: {member.filename}"
            )
        _validate_member_size(member.filename, member.file_size)
        if stripped is not None:
            _add_to_index(index, stripped, member, original=member.filename)

    def copy_member(name: str, target: Path) -> None:
        with archive.open(index[name]) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)

    sizes = {name: member.file_size for name, member in index.items()}
    return _materialize(
        requested=requested,
        destination=destination,
        sizes=sizes,
        copy_member=copy_member,
    )


def _is_gzip(archive: Path) -> bool:
    with archive.open("rb") as source:
        return source.read(2) == b"\x1f\x8b"


def _extract_gzip(
    archive: Path,
    *,
    strip_components: int,
    requested: tuple[str, ...],
    destination: Path,
) -> Mapping[str, Path]:
    if strip_components != 0:
        raise ValidationError(
            "strip_components is unsupported for single-file gzip archives"
        )
    if len(requested) != 1:
        raise ValidationError(
            "single-file gzip archives require exactly one requested path"
        )

    path = requested[0]
    target = destination.joinpath(*PurePosixPath(path).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    size_limit = min(MAX_ARCHIVE_MEMBER_SIZE, MAX_SELECTED_TOTAL_SIZE)
    size = 0
    try:
        with gzip.open(archive, "rb") as source, target.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > size_limit:
                    raise ValidationError(
                        "gzip payload exceeds archive size limit"
                    )
                output.write(chunk)
    except (gzip.BadGzipFile, EOFError, zlib.error) as error:
        target.unlink(missing_ok=True)
        raise ValidationError("invalid gzip archive") from error
    except ValidationError:
        target.unlink(missing_ok=True)
        raise

    return {path: target}


def _materialize(
    *,
    requested: tuple[str, ...],
    destination: Path,
    sizes: Mapping[str, int],
    copy_member: Callable[[str, Path], None],
) -> Mapping[str, Path]:
    missing = tuple(path for path in requested if path not in sizes)
    if missing:
        raise ValidationError(
            f"archive member(s) not found after strip: {', '.join(missing)}"
        )
    total_size = sum(sizes[path] for path in requested)
    if total_size > MAX_SELECTED_TOTAL_SIZE:
        raise ValidationError("selected archive files exceed total size limit")

    extracted: dict[str, Path] = {}
    for path in requested:
        target = destination.joinpath(*PurePosixPath(path).parts)
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValidationError(f"archive path escapes destination: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_member(path, target)
        extracted[path] = target
    return extracted


def _stripped_member_path(name: str, strip_components: int) -> str | None:
    if name.startswith("/"):
        raise ValidationError(f"absolute archive member path: {name}")
    raw_parts = name.split("/")
    if any(part == ".." for part in raw_parts):
        raise ValidationError(f"archive member path contains '..': {name}")
    parts = tuple(part for part in raw_parts if part not in {"", "."})
    if not parts:
        return None
    if len(parts) <= strip_components:
        return None
    return "/".join(parts[strip_components:])


def _validate_requested_path(path: str) -> str:
    normalized = _stripped_member_path(path, 0)
    if normalized is None or normalized != path:
        raise ValidationError(f"archive path must be normalized and relative: {path}")
    return normalized


def _validate_member_count(count: int) -> None:
    if count > MAX_ARCHIVE_MEMBERS:
        raise ValidationError("archive member count exceeds limit")


def _validate_member_size(name: str, size: int) -> None:
    if size < 0 or size > MAX_ARCHIVE_MEMBER_SIZE:
        raise ValidationError(f"archive member exceeds size limit: {name}")


def _add_to_index[T](
    index: dict[str, T],
    path: str,
    member: T,
    *,
    original: str,
) -> None:
    if path in index:
        raise ValidationError(f"duplicate archive path after strip: {path}")
    index[path] = member
