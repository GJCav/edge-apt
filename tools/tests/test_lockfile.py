from __future__ import annotations

from pathlib import Path

from edgeapt.constants import LOCK_SCHEMA
from edgeapt.lockfile import load_lock
from edgeapt.models import ArtifactFact, LockFile, SourceLock, UpstreamFact
from edgeapt.util import write_json


def test_lock_json_is_deterministic() -> None:
    artifact = ArtifactFact(
        package="edgeapt-hello",
        version="0.1.0-1",
        upstream_version="v0.1.0",
        revision=1,
        arch="amd64",
        suites=("jammy", "noble"),
        path="packages/hello/edgeapt-hello_0.1.0-1_amd64.deb",
        sha256="sha256:artifact",
        size=10,
        upstream=UpstreamFact(
            url="tests/fixtures/hello-world",
            sha256="sha256:upstream",
            size=10,
        ),
        created_at="2026-07-05T00:00:00Z",
    )
    lock = LockFile(
        schema=LOCK_SCHEMA,
        generated_at="2026-07-05T00:00:00Z",
        sources={
            "hello": SourceLock(
                source_file="sources/hello.yaml",
                source_sha256="sha256:source",
                template="edgeapt.single_binary/v1",
                package="edgeapt-hello",
                e2e_command=("edgeapt-hello",),
                artifacts=(artifact,),
            )
        },
    )
    assert list(lock.to_json()["sources"]) == ["hello"]
    assert lock.to_json()["sources"]["hello"]["e2e_command"] == ["edgeapt-hello"]
    assert lock.to_json()["sources"]["hello"]["artifacts"][0]["path"] == (
        "packages/hello/edgeapt-hello_0.1.0-1_amd64.deb"
    )


def test_lock_json_loads_e2e_command(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    write_json(
        path,
        {
            "schema": LOCK_SCHEMA,
            "generated_at": "2026-07-05T00:00:00Z",
            "sources": {
                "hello": {
                    "source_file": "sources/hello.yaml",
                    "source_sha256": "sha256:source",
                    "template": "edgeapt.single_binary/v1",
                    "package": "edgeapt-hello",
                    "e2e_command": ["edgeapt-hello"],
                    "artifacts": [
                        {
                            "package": "edgeapt-hello",
                            "version": "0.1.0-1",
                            "upstream_version": "v0.1.0",
                            "revision": 1,
                            "arch": "amd64",
                            "suites": ["noble"],
                            "path": "packages/hello/edgeapt-hello_0.1.0-1_amd64.deb",
                            "sha256": "sha256:artifact",
                            "size": 10,
                            "upstream": {
                                "url": "tests/fixtures/hello-world",
                                "sha256": "sha256:upstream",
                                "size": 10,
                            },
                            "created_at": "2026-07-05T00:00:00Z",
                        }
                    ],
                }
            },
        },
    )

    lock = load_lock(path)

    assert lock is not None
    assert lock.sources["hello"].e2e_command == ("edgeapt-hello",)
