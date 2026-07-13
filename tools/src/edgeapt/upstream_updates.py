from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any, Literal, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

import attrs
from semantic_version import Version  # pyright: ignore[reportMissingTypeStubs]

from edgeapt.infrastructure.source_loader import load_source_documents
from edgeapt.project import EdgeAptProject
from edgeapt.templates.base import SourceTemplate

type UpdateProvider = Literal["github_release", "cargo_quickinstall"]
type UpdateStatus = Literal[
    "up_to_date",
    "update_available",
    "waiting_for_artifact",
    "error",
]

_GITHUB_API = "https://api.github.com"
_CRATES_INDEX = "https://index.crates.io"
_USER_AGENT = "edgeapt-upstream-check/1"
_QUICKINSTALL_REPOSITORY = "cargo-bins/cargo-quickinstall"
_VERSION_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])v?[0-9]+(?:\.[0-9A-Za-z]+)+(?:-[0-9][0-9A-Za-z.+~]*)?"
)
_VERSION_ASSET_PATTERN = (
    r"v?[0-9]+(?:\.[0-9A-Za-z]+)+(?:-[0-9][0-9A-Za-z.+~]*)?"
)
_QUICKINSTALL_TAG_RE = re.compile(
    r"^(?P<crate>[A-Za-z][A-Za-z0-9_-]*)-"
    r"(?P<version>[0-9]+(?:\.[0-9A-Za-z]+)+(?:-[0-9A-Za-z.+~]+)?)$"
)


@attrs.define(kw_only=True, frozen=True)
class ReleaseAsset:
    name: str
    url: str
    digest: str | None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "url": self.url}
        if self.digest is not None:
            data["digest"] = self.digest
        return data


@attrs.define(kw_only=True, frozen=True)
class GitHubRelease:
    tag: str
    url: str
    assets: tuple[ReleaseAsset, ...]


@attrs.define(kw_only=True, frozen=True)
class UpdateCheckItem:
    source_id: str
    provider: UpdateProvider
    status: UpdateStatus
    current: tuple[str, ...]
    latest: str | None
    release_url: str | None = None
    asset: ReleaseAsset | None = None
    message: str | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "source_id": self.source_id,
            "provider": self.provider,
            "status": self.status,
            "current": list(self.current),
            "latest": self.latest,
        }
        if self.release_url is not None:
            data["release_url"] = self.release_url
        if self.asset is not None:
            data["asset"] = self.asset.to_json()
        if self.message is not None:
            data["message"] = self.message
        return data


@attrs.define(kw_only=True, frozen=True)
class UpdateCheckResult:
    source_count: int
    skipped: tuple[str, ...]
    items: tuple[UpdateCheckItem, ...]

    @property
    def update_count(self) -> int:
        return sum(item.status == "update_available" for item in self.items)

    @property
    def error_count(self) -> int:
        return sum(item.status == "error" for item in self.items)

    @property
    def waiting_count(self) -> int:
        return sum(item.status == "waiting_for_artifact" for item in self.items)

    def to_json(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            {
                "schema": 1,
                "source_count": self.source_count,
                "checked_count": len(self.items),
                "update_count": self.update_count,
                "error_count": self.error_count,
                "waiting_count": self.waiting_count,
                "skipped": list(self.skipped),
                "items": [item.to_json() for item in self.items],
            },
        )


class UpstreamMetadataError(Exception):
    """Raised when an upstream metadata service cannot be queried safely."""


class UpstreamMetadataClient(Protocol):
    def latest_github_release(self, repository: str) -> GitHubRelease: ...

    def github_release_by_tag(
        self,
        repository: str,
        tag: str,
    ) -> GitHubRelease | None: ...

    def latest_crate_version(self, crate: str) -> str: ...


class DefaultUpstreamMetadataClient:
    def __init__(self, github_token: str | None = None) -> None:
        self._github_token = (
            github_token
            or os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
        )
        self._latest_releases: dict[str, GitHubRelease] = {}
        self._tagged_releases: dict[tuple[str, str], GitHubRelease | None] = {}
        self._crate_versions: dict[str, str] = {}

    def latest_github_release(self, repository: str) -> GitHubRelease:
        cached = self._latest_releases.get(repository)
        if cached is not None:
            return cached
        data = self._get_json(
            f"{_GITHUB_API}/repos/{repository}/releases/latest",
            github=True,
        )
        release = _parse_github_release(data)
        self._latest_releases[repository] = release
        return release

    def github_release_by_tag(
        self,
        repository: str,
        tag: str,
    ) -> GitHubRelease | None:
        key = (repository, tag)
        if key in self._tagged_releases:
            return self._tagged_releases[key]
        try:
            data = self._get_json(
                f"{_GITHUB_API}/repos/{repository}/releases/tags/"
                f"{quote(tag, safe='')}",
                github=True,
            )
        except UpstreamMetadataError as exc:
            if _is_not_found(exc):
                self._tagged_releases[key] = None
                return None
            raise
        release = _parse_github_release(data)
        self._tagged_releases[key] = release
        return release

    def latest_crate_version(self, crate: str) -> str:
        cached = self._crate_versions.get(crate)
        if cached is not None:
            return cached
        text = self._get_text(f"{_CRATES_INDEX}/{_crate_index_path(crate)}")
        latest = _latest_stable_crate_version(crate, text)
        self._crate_versions[crate] = latest
        return latest

    def _get_json(self, url: str, *, github: bool) -> Mapping[str, Any]:
        text = self._get_text(url, github=github)
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise UpstreamMetadataError(f"invalid JSON from {url}: {exc}") from exc
        if not isinstance(raw, dict):
            raise UpstreamMetadataError(f"invalid JSON from {url}: expected object")
        return cast(dict[str, Any], raw)

    def _get_text(self, url: str, *, github: bool = False) -> str:
        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        if github:
            headers.update(
                {
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
            )
            if self._github_token is not None:
                headers["Authorization"] = f"Bearer {self._github_token}"
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            raise UpstreamMetadataError(
                f"HTTP {exc.code} while requesting {url}"
            ) from exc
        except (URLError, TimeoutError, UnicodeDecodeError) as exc:
            raise UpstreamMetadataError(f"request failed for {url}: {exc}") from exc


@attrs.define(kw_only=True, frozen=True)
class _GitHubReference:
    repository: str
    tag: str
    asset_name: str


@attrs.define(kw_only=True, frozen=True)
class _QuickInstallReference:
    crate: str
    version: str
    target: str


def check_upstream_updates(
    *,
    project: EdgeAptProject,
    client: UpstreamMetadataClient | None = None,
) -> UpdateCheckResult:
    documents = load_source_documents(
        project.paths.sources_dir,
        root=project.paths.root,
        templates=project.templates,
    )
    metadata = client or DefaultUpstreamMetadataClient()
    items: list[UpdateCheckItem] = []
    skipped: list[str] = []
    for document in documents:
        source = document.source
        urls = _upstream_urls(source)
        github_references = tuple(_parse_github_reference(url) for url in urls)
        if any(reference is None for reference in github_references):
            skipped.append(source.id)
            continue
        references = cast(tuple[_GitHubReference, ...], github_references)
        repositories = {reference.repository.lower() for reference in references}
        if len(repositories) != 1:
            skipped.append(source.id)
            continue
        provider: UpdateProvider = (
            "cargo_quickinstall"
            if next(iter(repositories)) == _QUICKINSTALL_REPOSITORY
            else "github_release"
        )
        try:
            item = (
                _check_quickinstall_source(source.id, references, metadata)
                if provider == "cargo_quickinstall"
                else _check_github_source(source.id, references, metadata)
            )
        except (UpstreamMetadataError, ValueError) as exc:
            item = UpdateCheckItem(
                source_id=source.id,
                provider=provider,
                status="error",
                current=tuple(sorted({reference.tag for reference in references})),
                latest=None,
                message=str(exc),
            )
        items.append(item)
    return UpdateCheckResult(
        source_count=len(documents),
        skipped=tuple(sorted(skipped)),
        items=tuple(sorted(items, key=lambda item: item.source_id)),
    )


def _check_github_source(
    source_id: str,
    references: tuple[_GitHubReference, ...],
    client: UpstreamMetadataClient,
) -> UpdateCheckItem:
    repository = references[0].repository
    current = tuple(sorted({reference.tag for reference in references}))
    release = client.latest_github_release(repository)
    if release.tag in current:
        return UpdateCheckItem(
            source_id=source_id,
            provider="github_release",
            status="up_to_date",
            current=current,
            latest=release.tag,
            release_url=release.url,
        )
    patterns = tuple(
        re.compile(pattern)
        for pattern in sorted(
            {_asset_pattern(reference.asset_name).pattern for reference in references}
        )
    )
    matches = tuple(
        asset
        for asset in release.assets
        if any(pattern.fullmatch(asset.name) for pattern in patterns)
    )
    if not matches:
        inferred = ", ".join(pattern.pattern for pattern in patterns)
        return UpdateCheckItem(
            source_id=source_id,
            provider="github_release",
            status="waiting_for_artifact",
            current=current,
            latest=release.tag,
            release_url=release.url,
            message=(
                "latest release has no asset matching inferred pattern(s): "
                f"{inferred}"
            ),
        )
    if len(matches) > 1:
        names = ", ".join(asset.name for asset in matches)
        raise ValueError(f"inferred asset patterns match multiple assets: {names}")
    return UpdateCheckItem(
        source_id=source_id,
        provider="github_release",
        status="update_available",
        current=current,
        latest=release.tag,
        release_url=release.url,
        asset=next(iter(matches)),
    )


def _check_quickinstall_source(
    source_id: str,
    references: tuple[_GitHubReference, ...],
    client: UpstreamMetadataClient,
) -> UpdateCheckItem:
    resolved = tuple(_parse_quickinstall_reference(item) for item in references)
    crates = {item.crate for item in resolved}
    targets = {item.target for item in resolved}
    if len(crates) != 1 or len(targets) != 1:
        raise ValueError("cargo-quickinstall upstreams do not share one crate and target")
    crate = next(iter(crates))
    target = next(iter(targets))
    current = tuple(sorted({item.version for item in resolved}))
    latest = client.latest_crate_version(crate)
    if latest in current:
        return UpdateCheckItem(
            source_id=source_id,
            provider="cargo_quickinstall",
            status="up_to_date",
            current=current,
            latest=latest,
        )
    tag = f"{crate}-{latest}"
    release = client.github_release_by_tag(_QUICKINSTALL_REPOSITORY, tag)
    expected_name = f"{crate}-{latest}-{target}.tar.gz"
    if release is None:
        return UpdateCheckItem(
            source_id=source_id,
            provider="cargo_quickinstall",
            status="waiting_for_artifact",
            current=current,
            latest=latest,
            message=f"quickinstall release is not available: {tag}",
        )
    matches = tuple(asset for asset in release.assets if asset.name == expected_name)
    if not matches:
        return UpdateCheckItem(
            source_id=source_id,
            provider="cargo_quickinstall",
            status="waiting_for_artifact",
            current=current,
            latest=latest,
            release_url=release.url,
            message=f"quickinstall asset is not available: {expected_name}",
        )
    if len(matches) > 1:
        raise ValueError(f"quickinstall release has duplicate asset: {expected_name}")
    return UpdateCheckItem(
        source_id=source_id,
        provider="cargo_quickinstall",
        status="update_available",
        current=current,
        latest=latest,
        release_url=release.url,
        asset=next(iter(matches)),
    )


def _parse_github_release(data: Mapping[str, Any]) -> GitHubRelease:
    tag = data.get("tag_name")
    url = data.get("html_url")
    raw_assets = data.get("assets")
    if (
        not isinstance(tag, str)
        or not isinstance(url, str)
        or not isinstance(raw_assets, list)
    ):
        raise UpstreamMetadataError("GitHub release response is missing required fields")
    assets: list[ReleaseAsset] = []
    for raw_asset in cast(list[Any], raw_assets):
        if not isinstance(raw_asset, dict):
            continue
        asset = cast(dict[str, Any], raw_asset)
        name = asset.get("name")
        download_url = asset.get("browser_download_url")
        digest = asset.get("digest")
        if not isinstance(name, str) or not isinstance(download_url, str):
            continue
        assets.append(
            ReleaseAsset(
                name=name,
                url=download_url,
                digest=digest if isinstance(digest, str) else None,
            )
        )
    return GitHubRelease(tag=tag, url=url, assets=tuple(assets))


def _latest_stable_crate_version(crate: str, text: str) -> str:
    candidates: list[Version] = []
    for line in text.splitlines():
        if line == "":
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise UpstreamMetadataError(
                f"invalid crates.io index entry for {crate}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise UpstreamMetadataError(
                f"invalid crates.io index entry for {crate}: expected object"
            )
        entry = cast(dict[str, Any], raw)
        version_text = entry.get("vers")
        if entry.get("yanked") is True or not isinstance(version_text, str):
            continue
        try:
            version = Version(version_text)
        except ValueError:
            continue
        if not version.prerelease:
            candidates.append(version)
    if not candidates:
        raise UpstreamMetadataError(
            f"crates.io has no stable non-yanked version for {crate}"
        )
    return str(max(candidates))


def _parse_github_reference(url: str) -> _GitHubReference | None:
    parsed = urlparse(url)
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if (
        parsed.hostname != "github.com"
        or len(parts) < 6
        or parts[2:4] != ["releases", "download"]
    ):
        return None
    return _GitHubReference(
        repository=f"{parts[0]}/{parts[1]}",
        tag=parts[4],
        asset_name=parts[-1],
    )


def _parse_quickinstall_reference(
    reference: _GitHubReference,
) -> _QuickInstallReference:
    tag_match = _QUICKINSTALL_TAG_RE.fullmatch(reference.tag)
    if tag_match is None:
        raise ValueError(f"cannot infer crate and version from tag: {reference.tag}")
    crate = tag_match.group("crate")
    version = tag_match.group("version")
    prefix = f"{reference.tag}-"
    suffix = ".tar.gz"
    if (
        not reference.asset_name.startswith(prefix)
        or not reference.asset_name.endswith(suffix)
    ):
        raise ValueError(
            f"cannot infer quickinstall target from asset: {reference.asset_name}"
        )
    target = reference.asset_name[len(prefix) : -len(suffix)]
    if target == "":
        raise ValueError("quickinstall target is empty")
    return _QuickInstallReference(crate=crate, version=version, target=target)


def _asset_pattern(asset_name: str) -> re.Pattern[str]:
    pieces: list[str] = []
    offset = 0
    for match in _VERSION_TOKEN_RE.finditer(asset_name):
        pieces.append(re.escape(asset_name[offset : match.start()]))
        pieces.append(_VERSION_ASSET_PATTERN)
        offset = match.end()
    pieces.append(re.escape(asset_name[offset:]))
    return re.compile("".join(pieces))


def _upstream_urls(source: SourceTemplate) -> tuple[str, ...]:
    data = source.model_dump(mode="python")
    raw_upstreams = data.get("upstream")
    if not isinstance(raw_upstreams, tuple):
        return ()
    urls: list[str] = []
    for raw_upstream in cast(tuple[Any, ...], raw_upstreams):
        if not isinstance(raw_upstream, dict):
            return ()
        upstream = cast(dict[str, Any], raw_upstream)
        url = upstream.get("url")
        if not isinstance(url, str):
            return ()
        urls.append(url)
    return tuple(urls)


def _crate_index_path(crate: str) -> str:
    normalized = crate.lower()
    length = len(normalized)
    if length == 1:
        return f"1/{normalized}"
    if length == 2:
        return f"2/{normalized}"
    if length == 3:
        return f"3/{normalized[0]}/{normalized}"
    return f"{normalized[:2]}/{normalized[2:4]}/{normalized}"


def _is_not_found(error: UpstreamMetadataError) -> bool:
    return str(error).startswith("HTTP 404 ")
