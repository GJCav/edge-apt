from __future__ import annotations

import os
from pathlib import Path

from edgeapt.domain.keys import DebKey
from edgeapt.infrastructure.deb import DefaultDebTools


def test_build_package_is_reproducible_across_umasks(tmp_path: Path) -> None:
    outputs: list[bytes] = []

    for index, mask in enumerate((0o022, 0o002)):
        case = tmp_path / str(index)
        previous_umask = os.umask(mask)
        try:
            payload = case / "payload"
            binary = payload / "usr" / "bin" / "example"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"#!/bin/sh\n")
            binary.chmod(0o755)
        finally:
            os.umask(previous_umask)

        output = case / "example.deb"
        DefaultDebTools().build_package(
            payload_root=payload,
            deb_key=DebKey(package="example", deb_version="1.0-1", arch="amd64"),
            description="Example package",
            homepage=None,
            output=output,
            work_dir=case / "work",
        )
        outputs.append(output.read_bytes())

    assert outputs[0] == outputs[1]
