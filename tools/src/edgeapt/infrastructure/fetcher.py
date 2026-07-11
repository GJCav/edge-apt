from __future__ import annotations

import shutil
import urllib.request
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
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "EdgeAPT/0.1"},
            )
            with urllib.request.urlopen(request) as response:
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
