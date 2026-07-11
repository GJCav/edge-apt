from __future__ import annotations

import gzip
import io
import stat
import tarfile
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
from edgeapt.templates.prebuilt_archive_v1 import (
    PrebuiltArchiveBuildSpec,
    PrebuiltArchiveV1,
)
from edgeapt.util import sha256_file
from edgeapt.workflows.planning import build_spec_digest


def test_planning_resolves_path_overrides_and_sorts_files() -> None:
    source = _source(
        path_overrides={"executable": "bin/tool-1.2.3"},
    )

    intent = source.plan(_provenance())[0]
    spec = cast(PrebuiltArchiveBuildSpec, intent.build_spec)

    assert intent.deb_key == DebKey(
        package="tool",
        deb_version="1.2.3-1",
        arch="amd64",
    )
    assert [item.destination for item in spec.files] == [
        "/usr/bin/tool",
        "/usr/share/man/man1/tool.1.gz",
    ]
    assert spec.files[0].path == "bin/tool-1.2.3"
    assert intent.e2e_commands == (("tool", "--version"),)


def test_equivalent_resolved_files_have_same_digest() -> None:
    direct = _source(executable_path="bin/tool-1.2.3")
    overridden = _source(
        executable_path="bin/tool",
        path_overrides={"executable": "bin/tool-1.2.3"},
    )

    direct_spec = direct.plan(_provenance())[0].build_spec
    overridden_spec = overridden.plan(_provenance())[0].build_spec

    assert build_spec_digest(direct_spec) == build_spec_digest(overridden_spec)


def test_file_declaration_order_does_not_change_digest() -> None:
    raw = _raw_source()
    reversed_raw = _raw_source()
    cast(list[dict[str, Any]], reversed_raw["files"]).reverse()

    direct = PrebuiltArchiveV1.model_validate(raw).plan(_provenance())[0]
    reversed_files = PrebuiltArchiveV1.model_validate(reversed_raw).plan(
        _provenance()
    )[0]

    assert build_spec_digest(direct.build_spec) == build_spec_digest(
        reversed_files.build_spec
    )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("unknown-override", "unknown id"),
        ("parent-conflict", "parent conflict"),
        ("outside-usr", "under /usr"),
        ("gzip-suffix", "end with .gz"),
    ],
)
def test_schema_rejects_invalid_file_layout(
    case: str,
    message: str,
) -> None:
    raw = _raw_source(path_overrides={})
    files = cast(list[dict[str, Any]], raw["files"])
    upstream = cast(list[dict[str, Any]], raw["upstream"])[0]
    if case == "unknown-override":
        overrides = cast(dict[str, str], upstream["path_overrides"])
        overrides["missing"] = "x"
    elif case == "parent-conflict":
        files[1]["destination"] = "/usr/bin/tool/child.gz"
    elif case == "outside-usr":
        files[1]["destination"] = "/opt/tool.1.gz"
    else:
        files[1]["destination"] = "/usr/share/man/tool.1"

    with pytest.raises(PydanticValidationError, match=message):
        PrebuiltArchiveV1.model_validate(raw)


def test_builder_materializes_payload_and_reproducible_gzip(tmp_path: Path) -> None:
    archive = _tar_archive(
        tmp_path / "tool.tar.gz",
        {
            "release/bin/tool": b"#!/bin/sh\n",
            "release/man/tool.1": b"manual page\n",
        },
    )
    source = _source(
        url=archive.as_posix(),
        sha256=sha256_file(archive),
    )
    spec = cast(
        PrebuiltArchiveBuildSpec,
        source.plan(_provenance())[0].build_spec,
    )
    first = _CaptureDebTools()
    second = _CaptureDebTools()

    _build(spec, tmp_path / "first", first)
    _build(spec, tmp_path / "second", second)

    assert first.files["usr/bin/tool"] == b"#!/bin/sh\n"
    compressed = first.files["usr/share/man/man1/tool.1.gz"]
    assert gzip.decompress(compressed) == b"manual page\n"
    assert compressed == second.files["usr/share/man/man1/tool.1.gz"]
    assert compressed[3] == 0
    assert compressed[4:8] == b"\0\0\0\0"
    assert compressed[9] == 255
    assert first.modes["usr/bin/tool"] == 0o755
    assert first.modes["usr/share/man/man1/tool.1.gz"] == 0o644


class _CaptureDebTools:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.modes: dict[str, int] = {}

    def build_package(
        self,
        *,
        payload_root: Path,
        deb_key: DebKey,
        description: str,
        homepage: str | None,
        output: Path,
        work_dir: Path,
    ) -> None:
        for path in payload_root.rglob("*"):
            if path.is_file():
                relative = path.relative_to(payload_root).as_posix()
                self.files[relative] = path.read_bytes()
                self.modes[relative] = stat.S_IMODE(path.stat().st_mode)
        output.write_bytes(b"candidate")

    def read_control(self, path: Path) -> DebControlFact:
        raise AssertionError("read_control is not used by direct template builds")


def _build(
    spec: PrebuiltArchiveBuildSpec,
    work_dir: Path,
    deb_tools: _CaptureDebTools,
) -> None:
    work_dir.mkdir(parents=True)
    PrebuiltArchiveV1.build(
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


def _source(
    *,
    executable_path: str = "bin/tool",
    path_overrides: dict[str, str] | None = None,
    url: str = "https://example.invalid/tool.tar.gz",
    sha256: str = f"sha256:{'a' * 64}",
) -> PrebuiltArchiveV1:
    return PrebuiltArchiveV1.model_validate(
        _raw_source(
            executable_path=executable_path,
            path_overrides=path_overrides,
            url=url,
            sha256=sha256,
        )
    )


def _raw_source(
    *,
    executable_path: str = "bin/tool",
    path_overrides: dict[str, str] | None = None,
    url: str = "https://example.invalid/tool.tar.gz",
    sha256: str = f"sha256:{'a' * 64}",
) -> dict[str, Any]:
    return {
        "template": "edgeapt.prebuilt_archive/v1",
        "id": "tool",
        "package": "tool",
        "e2e_commands": [["tool", "--version"]],
        "metadata": {"description": "Tool"},
        "files": [
            {
                "id": "executable",
                "path": executable_path,
                "destination": "/usr/bin/tool",
                "mode": "0755",
            },
            {
                "id": "man-page",
                "path": "man/tool.1",
                "destination": "/usr/share/man/man1/tool.1.gz",
                "mode": "0644",
                "transform": "gzip",
            },
        ],
        "upstream": [
            {
                "version": "1.2.3",
                "revision": 1,
                "arch": "amd64",
                "suites": ["noble"],
                "url": url,
                "sha256": sha256,
                "strip_components": 1,
                "path_overrides": path_overrides or {},
            }
        ],
    }


def _provenance() -> SourceProvenance:
    return SourceProvenance(
        source_id="tool",
        source_file="sources/tool.yaml",
        upstream_index=0,
    )


def _tar_archive(path: Path, files: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as output:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            output.addfile(info, io.BytesIO(content))
    return path
