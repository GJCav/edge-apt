from __future__ import annotations

from pathlib import Path
import json
from typing import Any, cast

import pytest

from edgeapt.infrastructure.signing import ensure_test_key
from edgeapt.errors import ValidationError
from edgeapt.util import run
from edgeapt.workflows.generate import (
    check_static_asset_size_limit,
    generate_repository,
)
from edgeapt.workflows.repackage import repackage_project
from tests.factories import make_project
from tests.factories import write_hello_source


@pytest.mark.integration
def test_generate_repo_writes_signed_metadata(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    paths = project.paths
    write_hello_source(tmp_path)
    ensure_test_key()
    repackage_project(project=project)
    result = generate_repository(profile="test", project=project)
    assert result.output_dir == paths.test_public_dir

    inrelease = paths.test_public_dir / "dists" / "noble" / "InRelease"
    release = paths.test_public_dir / "dists" / "noble" / "Release"
    release_gpg = paths.test_public_dir / "dists" / "noble" / "Release.gpg"
    index_html = paths.test_public_dir / "index.html"
    public_ascii = paths.test_public_dir / "edgeapt.asc"
    public_keyring = paths.test_public_dir / "edgeapt.gpg"
    package_manifest = paths.test_public_dir / "packages.json"
    vue_bundle = paths.test_public_dir / "assets" / "vue.global.prod.js"
    explorer_script = paths.test_public_dir / "assets" / "explorer.js"
    explorer_styles = paths.test_public_dir / "assets" / "explorer.css"
    vue_license = paths.test_public_dir / "assets" / "vue.LICENSE.txt"

    assert inrelease.exists()
    assert release.exists()
    assert release_gpg.exists()
    assert result.index_html == index_html
    assert result.package_manifest == package_manifest
    assert result.chunked_assets == ()
    assert index_html.exists()
    assert public_ascii.exists()
    assert public_keyring.exists()
    assert package_manifest.exists()
    assert vue_bundle.exists()
    assert explorer_script.exists()
    assert explorer_styles.exists()
    assert vue_license.exists()
    assert not paths.public_dir.exists()
    html = index_html.read_text(encoding="utf-8")
    assert "deployed at the edge and powered by serverless functions" in html
    assert "SIGNED APT REPOSITORY" not in html
    assert "Package explorer" in html
    assert "Repository Setup" in html
    assert "paginatedPackages" in html
    assert "Page {{ currentPage }} of {{ pageCount }}" in html
    assert '@click="copyText(item.sha256)"' in html
    assert 'title="Copy SHA256"' in html
    assert "Copy SHA256 for ${item.package}" in html
    assert "./assets/vue.global.prod.js" in html
    assert "cdn" not in html.lower()
    manifest = cast(
        dict[str, Any],
        json.loads(package_manifest.read_text(encoding="utf-8")),
    )
    packages = cast(list[dict[str, Any]], manifest["packages"])
    assert manifest["schema"] == "edgeapt.packages/v1"
    assert packages[0]["package"] == "edgeapt-hello"
    assert packages[0]["suite"] == "noble"
    assert (paths.test_public_dir / packages[0]["filename"]).is_file()
    run(["gpg", "--verify", inrelease])


def test_static_asset_size_limit_allows_exact_limit(tmp_path: Path) -> None:
    path = tmp_path / "asset.deb"
    path.write_bytes(b"0" * 10)
    check_static_asset_size_limit(tmp_path, limit_bytes=10)


def test_static_asset_size_limit_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "asset.deb"
    path.write_bytes(b"0" * 11)

    with pytest.raises(ValidationError, match="asset.deb"):
        check_static_asset_size_limit(tmp_path, limit_bytes=10)


def test_static_asset_size_limit_reports_nested_multiple_files(tmp_path: Path) -> None:
    nested = tmp_path / "pool" / "main"
    nested.mkdir(parents=True)
    first = nested / "first.deb"
    second = tmp_path / "Release"
    first.write_bytes(b"0" * 11)
    second.write_bytes(b"0" * 12)

    with pytest.raises(ValidationError) as exc_info:
        check_static_asset_size_limit(tmp_path, limit_bytes=10)

    message = str(exc_info.value)
    assert "first.deb" in message
    assert "Release" in message


def test_generate_rejects_source_scope_for_production(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="only supported for the test profile"):
        generate_repository(
            profile="prod",
            source_ids=("hello",),
            project=make_project(tmp_path),
        )
