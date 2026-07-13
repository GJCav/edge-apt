from __future__ import annotations

import stat
import zipfile
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError as PydanticValidationError

from edgeapt.domain.artifacts import DebControlFact
from edgeapt.domain.keys import DebKey
from edgeapt.domain.planning import SourceProvenance
from edgeapt.infrastructure.archive import DefaultArchiveExtractor
from edgeapt.infrastructure.fetcher import DefaultFetcher
from edgeapt.templates.base import BuildContext
from edgeapt.templates.single_binary import SingleBinaryBuildSpec
from edgeapt.templates.single_binary_v1_1 import SingleBinaryV11
from edgeapt.util import sha256_file
from tests.factories import make_source


def test_v11_normalizes_dependency_groups_for_canonical_plan() -> None:
    source = cast(
        SingleBinaryV11,
        make_source(
            template="edgeapt.single_binary/v1.1",
            depends=(
                "libxcb1",
                "libc6(>=2.34)",
                "libgcc-s1 | libgcc1",
                "libxcb1",
            ),
        ),
    )

    spec = cast(SingleBinaryBuildSpec, source.plan(_provenance())[0].build_spec)

    assert spec.repackage.metadata.depends == (
        "libc6 (>= 2.34)",
        "libgcc-s1 | libgcc1",
        "libxcb1",
    )
    canonical = spec.to_canonical_data()
    repackage = cast(dict[str, Any], canonical["repackage"])
    metadata = cast(dict[str, Any], repackage["metadata"])
    assert metadata["depends"] == list(spec.repackage.metadata.depends)


@pytest.mark.parametrize(
    ("depends", "message"),
    [
        (("foo, bar",), "one comma-level group"),
        (("Foo!",), "invalid dependency package name"),
        (("foo (=> 1)",), "invalid dependency version operator"),
    ],
)
def test_v11_rejects_invalid_dependency_groups(
    depends: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(PydanticValidationError, match=message):
        make_source(template="edgeapt.single_binary/v1.1", depends=depends)


def test_v11_requires_exactly_one_complete_copyright_source() -> None:
    source = make_source(template="edgeapt.single_binary/v1.1")
    raw = source.model_dump()
    repackage = cast(dict[str, Any], raw["repackage"])

    del repackage["copyright"]
    with pytest.raises(PydanticValidationError, match="copyright"):
        SingleBinaryV11.model_validate(raw)

    repackage["copyright"] = {
        "path": "LICENSE",
        "url": "https://example.invalid/LICENSE",
        "sha256": f"sha256:{'b' * 64}",
    }
    with pytest.raises(PydanticValidationError, match="copyright"):
        SingleBinaryV11.model_validate(raw)


def test_v11_archive_copyright_requires_extract_path() -> None:
    with pytest.raises(PydanticValidationError, match="requires extract_path"):
        make_source(
            template="edgeapt.single_binary/v1.1",
            copyright_path="LICENSE",
        )


def test_v11_builder_fetches_and_installs_copyright(tmp_path: Path) -> None:
    binary = tmp_path / "tool"
    binary.write_bytes(b"#!/bin/sh\n")
    license_file = tmp_path / "LICENSE"
    license_file.write_bytes(b"Example license\n")
    source = cast(
        SingleBinaryV11,
        make_source(
            template="edgeapt.single_binary/v1.1",
            package="tool",
            url=binary.as_posix(),
            sha256=sha256_file(binary),
            copyright_url=license_file.as_posix(),
            copyright_sha256=sha256_file(license_file),
            depends=("libc6 (>= 2.34)",),
        ),
    )
    capture = _CaptureDebTools()

    _build(source, tmp_path / "build", capture)

    assert capture.files["usr/bin/foo"] == b"#!/bin/sh\n"
    assert capture.files["usr/share/doc/tool/copyright"] == b"Example license\n"
    assert capture.modes["usr/bin/foo"] == 0o755
    assert capture.modes["usr/share/doc/tool/copyright"] == 0o644
    assert capture.depends == ("libc6 (>= 2.34)",)


def test_v11_builder_extracts_copyright_from_main_archive(tmp_path: Path) -> None:
    archive = tmp_path / "tool.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("release/tool", b"#!/bin/sh\n")
        package.writestr("release/LICENSE", b"Archive license\n")
    source = cast(
        SingleBinaryV11,
        make_source(
            template="edgeapt.single_binary/v1.1",
            package="tool",
            url=archive.as_posix(),
            sha256=sha256_file(archive),
            extract_path="release/tool",
            copyright_path="release/LICENSE",
        ),
    )
    capture = _CaptureDebTools()

    _build(source, tmp_path / "build", capture)

    assert capture.files["usr/share/doc/tool/copyright"] == b"Archive license\n"


class _CaptureDebTools:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.modes: dict[str, int] = {}
        self.depends: tuple[str, ...] = ()

    def build_package(
        self,
        *,
        payload_root: Path,
        deb_key: DebKey,
        description: str,
        homepage: str | None,
        section: str,
        multi_arch: str | None,
        depends: tuple[str, ...],
        output: Path,
        work_dir: Path,
    ) -> None:
        self.depends = depends
        for path in payload_root.rglob("*"):
            if path.is_file():
                relative = path.relative_to(payload_root).as_posix()
                self.files[relative] = path.read_bytes()
                self.modes[relative] = stat.S_IMODE(path.stat().st_mode)
        output.write_bytes(b"candidate")

    def read_control(self, path: Path) -> DebControlFact:
        raise AssertionError("read_control is not used by direct template builds")


def _build(
    source: SingleBinaryV11,
    work_dir: Path,
    deb_tools: _CaptureDebTools,
) -> None:
    work_dir.mkdir(parents=True)
    spec = source.plan(_provenance())[0].build_spec
    SingleBinaryV11.build(
        spec,
        BuildContext(
            deb_key=DebKey(package="tool", deb_version="1.2.3-1", arch="amd64"),
            root=work_dir,
            work_dir=work_dir,
            fetcher=DefaultFetcher(),
            archive_extractor=DefaultArchiveExtractor(),
            deb_tools=deb_tools,
            report=lambda kind, message, url: None,
        ),
    )


def _provenance() -> SourceProvenance:
    return SourceProvenance(
        source_id="tool",
        source_file="sources/tool.yaml",
        upstream_index=0,
    )
