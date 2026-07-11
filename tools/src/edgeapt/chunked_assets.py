from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import attrs

from edgeapt.constants import STATIC_ASSET_SIZE_LIMIT_BYTES
from edgeapt.errors import ValidationError
from edgeapt.util import sha256_file, write_json

CHUNKED_ASSET_SCHEMA = "edgeapt.chunked-asset/v1"
DEFAULT_ASSET_LIMIT = STATIC_ASSET_SIZE_LIMIT_BYTES
DEFAULT_CHUNK_SIZE = 24 * 1024 * 1024
DEFAULT_MAX_CHUNKS = 32
SIDECAR_SUFFIX = ".edgeapt-chunks.json"


@attrs.define(kw_only=True, frozen=True, order=True)
class ChunkFact:
    path: str
    offset: int
    size: int
    sha256: str

    def to_json(self) -> dict[str, Any]:
        return attrs.asdict(self)


@attrs.define(kw_only=True, frozen=True, order=True)
class ChunkedAssetFact:
    path: str
    sidecar_path: str
    size: int
    sha256: str
    chunks: tuple[ChunkFact, ...]

    def to_sidecar_json(self) -> dict[str, Any]:
        return {
            "schema": CHUNKED_ASSET_SCHEMA,
            "path": f"/{self.path}",
            "size": self.size,
            "sha256": self.sha256,
            "content_type": "application/vnd.debian.binary-package",
            "chunks": [chunk.to_json() for chunk in self.chunks],
        }


def split_oversized_debs(
    *,
    output_dir: Path,
    staging_dir: Path,
    asset_limit: int = DEFAULT_ASSET_LIMIT,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
) -> tuple[ChunkedAssetFact, ...]:
    _validate_limits(
        asset_limit=asset_limit,
        chunk_size=chunk_size,
        max_chunks=max_chunks,
    )
    staging_dir.mkdir(parents=True, exist_ok=True)
    results: list[ChunkedAssetFact] = []
    for artifact in sorted(output_dir.glob("pool/**/*.deb")):
        if artifact.stat().st_size <= asset_limit:
            continue
        results.append(
            _split_artifact(
                artifact=artifact,
                output_dir=output_dir,
                staging_dir=staging_dir,
                chunk_size=chunk_size,
                max_chunks=max_chunks,
            )
        )
    return tuple(results)


def _split_artifact(
    *,
    artifact: Path,
    output_dir: Path,
    staging_dir: Path,
    chunk_size: int,
    max_chunks: int,
) -> ChunkedAssetFact:
    relative = artifact.relative_to(output_dir).as_posix()
    artifact_size = artifact.stat().st_size
    artifact_sha256 = sha256_file(artifact)
    digest = artifact_sha256.removeprefix("sha256:")
    with tempfile.TemporaryDirectory(
        prefix="edgeapt-chunks-",
        dir=staging_dir,
    ) as temporary:
        temporary_dir = Path(temporary)
        staged_chunks: list[tuple[Path, ChunkFact]] = []
        offset = 0
        with artifact.open("rb") as source:
            for index in range(max_chunks + 1):
                content = source.read(chunk_size)
                if not content:
                    break
                if index == max_chunks:
                    raise ValidationError(
                        f"artifact requires more than {max_chunks} chunks: {relative}"
                    )
                chunk_relative = (
                    PurePosixPath("__edgeapt")
                    / "chunks"
                    / "sha256"
                    / digest
                    / f"{index:04d}.part"
                )
                staged_path = temporary_dir / f"{index:04d}.part"
                staged_path.write_bytes(content)
                chunk = ChunkFact(
                    path=f"/{chunk_relative.as_posix()}",
                    offset=offset,
                    size=len(content),
                    sha256=sha256_file(staged_path),
                )
                staged_chunks.append((staged_path, chunk))
                offset += len(content)

        _verify_staged_chunks(
            staged_chunks=staged_chunks,
            expected_size=artifact_size,
            expected_sha256=artifact_sha256,
            artifact=relative,
        )
        sidecar_relative = f"{relative}{SIDECAR_SUFFIX}"
        result = ChunkedAssetFact(
            path=relative,
            sidecar_path=sidecar_relative,
            size=artifact_size,
            sha256=artifact_sha256,
            chunks=tuple(chunk for _, chunk in staged_chunks),
        )
        for staged_path, chunk in staged_chunks:
            destination = output_dir.joinpath(
                *PurePosixPath(chunk.path.lstrip("/")).parts
            )
            _install_chunk(staged_path, destination, expected=chunk)
        staged_sidecar = temporary_dir / "sidecar.json"
        write_json(staged_sidecar, result.to_sidecar_json())
        sidecar = output_dir.joinpath(*PurePosixPath(sidecar_relative).parts)
        _atomic_copy(staged_sidecar, sidecar)
        artifact.unlink()
        return result


def _verify_staged_chunks(
    *,
    staged_chunks: list[tuple[Path, ChunkFact]],
    expected_size: int,
    expected_sha256: str,
    artifact: str,
) -> None:
    digest = hashlib.sha256()
    size = 0
    expected_offset = 0
    for path, chunk in staged_chunks:
        if chunk.offset != expected_offset:
            raise ValidationError(f"non-contiguous chunk offsets for {artifact}")
        if path.stat().st_size != chunk.size or sha256_file(path) != chunk.sha256:
            raise ValidationError(f"staged chunk verification failed for {artifact}")
        with path.open("rb") as source:
            for content in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(content)
                size += len(content)
        expected_offset += chunk.size
    actual_sha256 = f"sha256:{digest.hexdigest()}"
    if size != expected_size or actual_sha256 != expected_sha256:
        raise ValidationError(
            f"reassembled artifact verification failed for {artifact}: "
            f"size={size}, sha256={actual_sha256}"
        )


def _install_chunk(source: Path, destination: Path, *, expected: ChunkFact) -> None:
    if destination.exists():
        if (
            destination.stat().st_size != expected.size
            or sha256_file(destination) != expected.sha256
        ):
            raise ValidationError(
                f"content-addressed chunk collision: {destination}"
            )
        return
    _atomic_copy(source, destination)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    shutil.copyfile(source, temporary)
    temporary.replace(destination)


def _validate_limits(*, asset_limit: int, chunk_size: int, max_chunks: int) -> None:
    if asset_limit < 1:
        raise ValidationError("asset_limit must be positive")
    if chunk_size < 1 or chunk_size > asset_limit:
        raise ValidationError("chunk_size must be positive and no larger than asset_limit")
    if max_chunks < 1:
        raise ValidationError("max_chunks must be positive")
