from __future__ import annotations

from edgeapt.constants import LOCK_SCHEMA
from edgeapt.models import ArtifactFact, LockFile, SourceLock, UpstreamFact


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
                artifacts=(artifact,),
            )
        },
    )
    assert list(lock.to_json()["sources"]) == ["hello"]
    assert lock.to_json()["sources"]["hello"]["artifacts"][0]["path"] == (
        "packages/hello/edgeapt-hello_0.1.0-1_amd64.deb"
    )
