from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.errors import ValidationError
from edgeapt.models import SourceConfig, UpstreamConfig
from edgeapt.sources import artifact_path, artifact_version, load_sources
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
    assert {source.id for source in sources} == {"hello", "doggo", "fd"}
