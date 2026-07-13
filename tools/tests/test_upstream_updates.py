from __future__ import annotations

from pathlib import Path

import yaml

from edgeapt.upstream_updates import (
    DefaultUpstreamMetadataClient,
    GitHubRelease,
    ReleaseAsset,
    UpstreamMetadataError,
    check_upstream_updates,
)
from edgeapt.templates.base import SourceTemplate
from tests.factories import make_document, make_project, make_source


def test_github_release_is_inferred_from_existing_upstream_url(
    tmp_path: Path,
) -> None:
    source = make_source(
        source_id="tool",
        url=(
            "https://github.com/example/tool/releases/download/v1.0.0/"
            "tool_1.0.0_amd64.deb"
        ),
    )
    _write_source(tmp_path, source)
    asset = ReleaseAsset(
        name="tool_2.0.0_amd64.deb",
        url=(
            "https://github.com/example/tool/releases/download/v2.0.0/"
            "tool_2.0.0_amd64.deb"
        ),
        digest="sha256:new",
    )
    client = _FakeMetadataClient(
        latest_release=_release("v2.0.0", (asset,)),
    )

    result = check_upstream_updates(project=make_project(tmp_path), client=client)

    assert result.update_count == 1
    assert result.error_count == 0
    assert result.items[0].provider == "github_release"
    assert result.items[0].current == ("v1.0.0",)
    assert result.items[0].latest == "v2.0.0"
    assert result.items[0].asset == asset


def test_inferred_asset_pattern_preserves_debian_revision_and_architecture(
    tmp_path: Path,
) -> None:
    source = make_source(
        source_id="helix",
        url=(
            "https://github.com/helix-editor/helix/releases/download/25.07.1/"
            "helix_25.7.1-1_amd64.deb"
        ),
    )
    _write_source(tmp_path, source)
    desired = ReleaseAsset(
        name="helix_25.8.1-1_amd64.deb",
        url="https://example.invalid/helix.deb",
        digest=None,
    )
    wrong_arch = ReleaseAsset(
        name="helix_25.8.1-1_arm64.deb",
        url="https://example.invalid/helix-arm64.deb",
        digest=None,
    )

    result = check_upstream_updates(
        project=make_project(tmp_path),
        client=_FakeMetadataClient(
            latest_release=_release("25.08.1", (desired, wrong_arch)),
        ),
    )

    assert result.items[0].status == "update_available"
    assert result.items[0].asset == desired


def test_github_release_handles_current_and_missing_assets(tmp_path: Path) -> None:
    for index, (latest, expected) in enumerate(
        (
            ("v1.0.0", "up_to_date"),
            ("v2.0.0", "waiting_for_artifact"),
        )
    ):
        root = tmp_path / str(index)
        source = make_source(
            source_id="tool",
            url=(
                "https://github.com/example/tool/releases/download/v1.0.0/"
                "tool-linux-amd64.tar.gz"
            ),
        )
        _write_source(root, source)

        result = check_upstream_updates(
            project=make_project(root),
            client=_FakeMetadataClient(latest_release=_release(latest)),
        )

        assert result.items[0].status == expected


def test_quickinstall_crate_and_target_are_inferred_from_url(
    tmp_path: Path,
) -> None:
    source = make_source(
        source_id="tool-musl",
        package="tool-musl",
        url=(
            "https://github.com/cargo-bins/cargo-quickinstall/releases/download/"
            "tool-1.0.0/tool-1.0.0-x86_64-unknown-linux-musl.tar.gz"
        ),
    )
    _write_source(tmp_path, source)
    asset = ReleaseAsset(
        name="tool-2.0.0-x86_64-unknown-linux-musl.tar.gz",
        url="https://example.invalid/tool.tar.gz",
        digest=None,
    )
    client = _FakeMetadataClient(
        crate_version="2.0.0",
        tagged_release=_release("tool-2.0.0", (asset,)),
    )

    result = check_upstream_updates(project=make_project(tmp_path), client=client)

    assert result.update_count == 1
    assert result.items[0].provider == "cargo_quickinstall"
    assert result.items[0].current == ("1.0.0",)
    assert result.items[0].asset == asset
    assert client.requested_tag == (
        "cargo-bins/cargo-quickinstall",
        "tool-2.0.0",
    )


def test_unrecognized_upstream_is_skipped_without_error(tmp_path: Path) -> None:
    _write_source(
        tmp_path,
        make_source(source_id="local", url="tests/fixtures/hello-world"),
    )

    result = check_upstream_updates(
        project=make_project(tmp_path),
        client=_FakeMetadataClient(),
    )

    assert result.items == ()
    assert result.skipped == ("local",)
    assert result.error_count == 0


def test_checker_isolates_provider_errors(tmp_path: Path) -> None:
    source = make_source(
        source_id="tool",
        url="https://github.com/example/tool/releases/download/v1/tool.tar.gz",
    )
    _write_source(tmp_path, source)

    result = check_upstream_updates(
        project=make_project(tmp_path),
        client=_FakeMetadataClient(error=UpstreamMetadataError("API unavailable")),
    )

    assert result.error_count == 1
    assert result.items[0].message == "API unavailable"


def test_crates_index_selects_latest_stable_non_yanked_version() -> None:
    client = _IndexMetadataClient(
        "\n".join(
            (
                '{"vers":"1.0.0","yanked":false}',
                '{"vers":"2.0.0-alpha.1","yanked":false}',
                '{"vers":"1.5.0","yanked":true}',
                '{"vers":"1.4.0","yanked":false}',
            )
        )
    )

    assert client.latest_crate_version("tool") == "1.4.0"
    assert client.requested_url.endswith("/to/ol/tool")


class _FakeMetadataClient:
    def __init__(
        self,
        *,
        latest_release: GitHubRelease | None = None,
        tagged_release: GitHubRelease | None = None,
        crate_version: str = "1.0.0",
        error: UpstreamMetadataError | None = None,
    ) -> None:
        self.latest_release = latest_release or _release("v1.0.0")
        self.tagged_release = tagged_release
        self.crate_version = crate_version
        self.error = error
        self.requested_tag: tuple[str, str] | None = None

    def latest_github_release(self, repository: str) -> GitHubRelease:
        if self.error is not None:
            raise self.error
        return self.latest_release

    def github_release_by_tag(
        self,
        repository: str,
        tag: str,
    ) -> GitHubRelease | None:
        if self.error is not None:
            raise self.error
        self.requested_tag = (repository, tag)
        return self.tagged_release

    def latest_crate_version(self, crate: str) -> str:
        if self.error is not None:
            raise self.error
        return self.crate_version


class _IndexMetadataClient(DefaultUpstreamMetadataClient):
    def __init__(self, response: str) -> None:
        super().__init__()
        self.response = response
        self.requested_url = ""

    def _get_text(self, url: str, *, github: bool = False) -> str:
        self.requested_url = url
        return self.response


def _release(
    tag: str,
    assets: tuple[ReleaseAsset, ...] = (),
) -> GitHubRelease:
    return GitHubRelease(
        tag=tag,
        url=f"https://github.com/example/tool/releases/tag/{tag}",
        assets=assets,
    )


def _write_source(root: Path, source: SourceTemplate) -> None:
    document = make_document(source)
    path = root / document.source_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(source.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
