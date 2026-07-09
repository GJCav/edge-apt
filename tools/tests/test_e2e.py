from __future__ import annotations

import pytest

from edgeapt.constants import LOCK_SCHEMA
from edgeapt.e2e import build_e2e_test_cases
from edgeapt.e2e import docker_e2e_command_args
from edgeapt.e2e import docker_install_args
from edgeapt.e2e import E2ETestCase
from edgeapt.e2e import group_e2e_test_cases
from edgeapt.errors import ValidationError
from edgeapt.models import ArtifactFact
from edgeapt.models import LockFile
from edgeapt.models import SourceLock
from edgeapt.models import UpstreamFact


def test_e2e_matrix_groups_by_suite_then_arch_then_source() -> None:
    lock = LockFile(
        schema=LOCK_SCHEMA,
        generated_at="2026-07-05T00:00:00Z",
        sources={
            "doggo": _source_lock(
                source_id="doggo",
                package="doggo",
                command=("doggo", "--version"),
                artifact=_artifact(
                    package="doggo",
                    version="1.1.7-1",
                    suites=("resolute", "focal"),
                ),
            ),
            "hello": _source_lock(
                source_id="hello",
                package="edgeapt-hello",
                command=("edgeapt-hello",),
                artifact=_artifact(
                    package="edgeapt-hello",
                    version="0.1.0-1",
                    suites=("jammy",),
                ),
            ),
        },
    )

    groups = group_e2e_test_cases(build_e2e_test_cases(lock))

    assert [(group.suite, group.arch) for group in groups] == [
        ("focal", "amd64"),
        ("jammy", "amd64"),
        ("resolute", "amd64"),
    ]
    assert groups[0].cases[0].source_id == "doggo"
    assert groups[1].cases[0].source_id == "hello"


def test_e2e_matrix_filters_by_suite_source_and_package() -> None:
    lock = LockFile(
        schema=LOCK_SCHEMA,
        generated_at="2026-07-05T00:00:00Z",
        sources={
            "doggo": _source_lock(
                source_id="doggo",
                package="doggo",
                command=("doggo", "--version"),
                artifact=_artifact(
                    package="doggo",
                    version="1.1.7-1",
                    suites=("focal", "noble"),
                ),
            ),
            "hello": _source_lock(
                source_id="hello",
                package="edgeapt-hello",
                command=("edgeapt-hello",),
                artifact=_artifact(
                    package="edgeapt-hello",
                    version="0.1.0-1",
                    suites=("noble",),
                ),
            ),
        },
    )

    cases = build_e2e_test_cases(
        lock,
        suite_filter="noble",
        source_filter="doggo",
        package_filter="doggo",
    )

    assert [(case.suite, case.source_id, case.package) for case in cases] == [
        ("noble", "doggo", "doggo")
    ]


def test_e2e_install_uses_package_version_pin() -> None:
    case = E2ETestCase(
        suite="noble",
        arch="amd64",
        source_id="doggo",
        package="doggo",
        version="1.1.7-1",
        command=("doggo", "--version"),
    )

    assert docker_install_args("container-id", case) == (
        "docker",
        "exec",
        "container-id",
        "env",
        "DEBIAN_FRONTEND=noninteractive",
        "apt-get",
        "install",
        "-y",
        "doggo=1.1.7-1",
    )


def test_e2e_command_uses_argv() -> None:
    case = E2ETestCase(
        suite="noble",
        arch="amd64",
        source_id="doggo",
        package="doggo",
        version="1.1.7-1",
        command=("doggo", "--version"),
    )

    assert docker_e2e_command_args("container-id", case) == (
        "docker",
        "exec",
        "container-id",
        "doggo",
        "--version",
    )


def test_e2e_rejects_unknown_suite_filter() -> None:
    lock = LockFile(schema=LOCK_SCHEMA, generated_at="2026-07-05T00:00:00Z", sources={})

    with pytest.raises(ValidationError, match="unsupported e2e suite"):
        build_e2e_test_cases(lock, suite_filter="unknown")


def _source_lock(
    *,
    source_id: str,
    package: str,
    command: tuple[str, ...],
    artifact: ArtifactFact,
) -> SourceLock:
    return SourceLock(
        source_file=f"sources/{source_id}.yaml",
        source_sha256="sha256:source",
        template="edgeapt.single_binary/v1",
        package=package,
        e2e_command=command,
        artifacts=(artifact,),
    )


def _artifact(
    *,
    package: str,
    version: str,
    suites: tuple[str, ...],
) -> ArtifactFact:
    return ArtifactFact(
        package=package,
        version=version,
        upstream_version=version,
        revision=1,
        arch="amd64",
        suites=suites,
        path=f"packages/{package}/{package}_{version}_amd64.deb",
        sha256="sha256:artifact",
        size=10,
        upstream=UpstreamFact(
            url="https://example.invalid/package.deb",
            sha256="sha256:upstream",
            size=10,
        ),
        created_at="2026-07-05T00:00:00Z",
    )
