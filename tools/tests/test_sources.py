from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.constants import ROOT
from edgeapt.errors import ValidationError
from edgeapt.models import SourceConfig, UpstreamConfig
from edgeapt.sources import artifact_path, artifact_version, load_source, load_sources
from edgeapt.sources import normalize_debian_version, validate_arch, validate_id


def test_normalize_debian_version_strips_v_prefix() -> None:
    assert normalize_debian_version("v1.2.3") == "1.2.3"
    assert normalize_debian_version("V1.2.3") == "1.2.3"
    assert normalize_debian_version("1.2.3-rc1") == "1.2.3-rc1"


def test_validate_id_and_arch() -> None:
    validate_id("hello-world")
    validate_arch("amd64")
    with pytest.raises(ValidationError):
        validate_id("Hello")
    with pytest.raises(ValidationError):
        validate_arch("i386")


def test_artifact_path_for_single_binary() -> None:
    source = SourceConfig(
        template="edgeapt.single_binary/v1",
        id="hello",
        package="edgeapt-hello",
        source_file="sources/hello.yaml",
        repackage=None,
        upstream=(),
    )
    upstream = UpstreamConfig(
        version="v0.1.0",
        revision=1,
        arch="amd64",
        suites=("jammy", "noble"),
        url="tests/fixtures/hello-world",
    )
    assert artifact_version(source, upstream) == "0.1.0-1"
    assert artifact_path(source, upstream) == Path(
        "packages/hello/edgeapt-hello_0.1.0-1_amd64.deb"
    )


def test_load_repo_sources() -> None:
    sources = load_sources()
    assert {source.id for source in sources} == {"hello", "doggo", "fd", "bat"}


def test_override_reason_required(tmp_path: Path) -> None:
    source_path = tmp_path / "bad.yaml"
    source_path.write_text(
        """
template: edgeapt.single_binary/v1
id: bad
package: bad
allow_ubuntu_package_override: true

repackage:
  type: nfpm
  install_path: /usr/bin/bad
  metadata:
    description: bad package

upstream:
  - version: v0.1.0
    revision: 1
    arch: amd64
    suites: [jammy]
    url: tests/fixtures/hello-world
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_source(source_path)


def test_override_fields_are_loaded() -> None:
    source_dir = ROOT / "tmp" / "pytest-sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / "override.yaml"
    source_path.write_text(
        """
template: edgeapt.deb_upstream/v1
id: fd
package: fd
allow_ubuntu_package_override: true
override_reason: Use upstream release.

upstream:
  - version: 10.4.1
    arch: amd64
    suites: [jammy, noble]
    url: https://example.invalid/fd.deb
""",
        encoding="utf-8",
    )

    source = load_source(source_path)
    assert source.allow_ubuntu_package_override is True
    assert source.override_reason == "Use upstream release."
