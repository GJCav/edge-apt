from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.constants import ROOT, SOURCES_DIR
from edgeapt.errors import ValidationError
from edgeapt.infrastructure.source_loader import (
    load_source_document,
    load_source_documents,
)
from edgeapt.templates.common import (
    normalize_debian_version,
    validate_arch,
    validate_id,
)


def test_normalize_debian_version_strips_v_prefix() -> None:
    assert normalize_debian_version("v1.2.3") == "1.2.3"
    assert normalize_debian_version("V1.2.3") == "1.2.3"
    assert normalize_debian_version("1.2.3-rc1") == "1.2.3-rc1"


def test_validate_id_and_arch() -> None:
    validate_id("hello-world")
    validate_arch("amd64")
    with pytest.raises(ValueError):
        validate_id("Hello")
    with pytest.raises(ValueError):
        validate_arch("i386")


def test_load_repo_sources() -> None:
    documents = load_source_documents(SOURCES_DIR, root=ROOT)

    assert {document.source.id for document in documents} >= {
        "hello",
        "doggo",
        "fd",
        "bat",
    }
    assert all(document.source_file.startswith("sources/") for document in documents)


def test_override_reason_required(tmp_path: Path) -> None:
    source_path = _write_source(
        tmp_path,
        """
template: edgeapt.single_binary/v1
id: bad
package: bad
e2e_command: [bad, --version]
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
    )

    with pytest.raises(ValidationError, match="override_reason"):
        load_source_document(source_path, root=tmp_path)


def test_override_fields_are_loaded(tmp_path: Path) -> None:
    source_path = _write_source(
        tmp_path,
        """
template: edgeapt.deb_upstream/v1
id: fd
package: fd
e2e_command: [fd, --version]
allow_ubuntu_package_override: true
override_reason: Use upstream release.

upstream:
  - version: 10.4.1
    arch: amd64
    suites: [focal, jammy, noble, resolute]
    url: https://example.invalid/fd.deb
""",
    )

    source = load_source_document(source_path, root=tmp_path).source

    assert source.allow_ubuntu_package_override is True
    assert source.override_reason == "Use upstream release."
    assert source.e2e_command == ("fd", "--version")


def test_e2e_command_is_required(tmp_path: Path) -> None:
    source_path = _write_source(
        tmp_path,
        """
template: edgeapt.deb_upstream/v1
id: fd
package: fd

upstream:
  - version: 10.4.1
    arch: amd64
    suites: [noble]
    url: https://example.invalid/fd.deb
""",
    )

    with pytest.raises(ValidationError, match="e2e_command"):
        load_source_document(source_path, root=tmp_path)


def test_unknown_template_is_rejected(tmp_path: Path) -> None:
    source_path = _write_source(
        tmp_path,
        """
template: edgeapt.unknown/v1
id: unknown
package: unknown
e2e_command: [unknown]
""",
    )

    with pytest.raises(ValidationError, match="unsupported template"):
        load_source_document(source_path, root=tmp_path)


def _write_source(root: Path, content: str) -> Path:
    path = root / "sources" / "source.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.lstrip(), encoding="utf-8")
    return path
