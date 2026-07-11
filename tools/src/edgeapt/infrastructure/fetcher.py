from __future__ import annotations

import shutil
import tarfile
import urllib.request
import zipfile
from email.message import Message
from pathlib import Path
from urllib.parse import urlparse

from edgeapt.domain.artifacts import UpstreamFact
from edgeapt.errors import ValidationError
from edgeapt.templates.base import FetchResult
from edgeapt.util import file_size, sha256_file


class DefaultFetcher:
    def fetch(
        self,
        *,
        url: str,
        sha256: str | None,
        destination: Path,
        root: Path,
    ) -> FetchResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        headers = Message()
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"}:
            with urllib.request.urlopen(url) as response:
                destination.write_bytes(response.read())
                headers = response.headers
        elif parsed.scheme == "file":
            shutil.copy2(Path(parsed.path), destination)
        elif parsed.scheme == "":
            source_path = Path(url)
            if not source_path.is_absolute():
                source_path = root / source_path
            shutil.copy2(source_path, destination)
        else:
            raise ValidationError(f"unsupported URL scheme: {url}")

        digest = sha256_file(destination)
        if sha256 is not None and sha256 != digest:
            raise ValidationError(
                f"sha256 mismatch for {url}: expected {sha256}, got {digest}"
            )

        return FetchResult(
            path=destination,
            fact=UpstreamFact(
                url=url,
                sha256=digest,
                size=file_size(destination),
                etag=headers.get("ETag"),
                last_modified=headers.get("Last-Modified"),
            ),
        )

    def prepare_single_binary(
        self,
        *,
        downloaded: Path,
        extract_path: str | None,
        work_dir: Path,
    ) -> Path:
        if extract_path is None:
            return downloaded

        extract_dir = work_dir / "extract"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)
        candidate = _safe_extract_path(extract_dir, extract_path)
        if tarfile.is_tarfile(downloaded):
            with tarfile.open(downloaded) as archive:
                archive.extractall(extract_dir, filter="data")
        elif zipfile.is_zipfile(downloaded):
            with zipfile.ZipFile(downloaded) as archive:
                try:
                    info = archive.getinfo(extract_path)
                except KeyError as exc:
                    raise ValidationError(
                        f"extract_path not found in archive: {extract_path}"
                    ) from exc
                if info.is_dir():
                    raise ValidationError(
                        f"extract_path is a directory: {extract_path}"
                    )
                candidate.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, candidate.open("wb") as target:
                    shutil.copyfileobj(source, target)
        else:
            raise ValidationError(
                "extract_path is only supported for tar or zip archives"
            )

        if not candidate.exists() or not candidate.is_file():
            raise ValidationError(
                f"extract_path not found in archive: {extract_path}"
            )
        return candidate


def _safe_extract_path(extract_dir: Path, member: str) -> Path:
    candidate = extract_dir / member
    if not candidate.resolve().is_relative_to(extract_dir.resolve()):
        raise ValidationError(
            f"extract_path escapes archive extraction directory: {member}"
        )
    return candidate
