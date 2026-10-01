from __future__ import annotations

import subprocess
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path

import pytest

from edgeapt.e2e import build_e2e_test_cases
from edgeapt.e2e import clear_e2e_apt_cache
from edgeapt.e2e import docker_install_args
from edgeapt.e2e import docker_remove_args
from edgeapt.e2e import E2EEvent
from edgeapt.e2e import E2EGroup
from edgeapt.e2e import E2ETestCase
from edgeapt.e2e import E2ECommandContext
from edgeapt.e2e import group_e2e_test_cases
from edgeapt.e2e import run_e2e
from edgeapt.e2e import validate_e2e_repository
from edgeapt.e2e import validate_e2e_scope
from edgeapt.e2e import _run_checked  # pyright: ignore[reportPrivateUsage]
from edgeapt.e2e import _run_group  # pyright: ignore[reportPrivateUsage]
from edgeapt.errors import CommandError
from edgeapt.errors import ValidationError
from edgeapt.package_manifest import PACKAGE_MANIFEST_SCHEMA
from edgeapt.util import write_json
from tests.factories import make_artifact
from tests.factories import make_deb_key
from tests.factories import make_lock
from tests.factories import make_publication


def test_e2e_matrix_groups_by_suite_then_arch_then_source() -> None:
    doggo = make_deb_key(package="doggo", deb_version="1.1.7-1")
    hello = make_deb_key(package="edgeapt-hello", deb_version="0.1.0-1")
    lock = make_lock(
        artifacts=(make_artifact(deb_key=doggo), make_artifact(deb_key=hello)),
        publications=(
            make_publication(deb_key=doggo, suite="resolute", source_id="doggo"),
            make_publication(deb_key=doggo, suite="focal", source_id="doggo"),
            make_publication(
                deb_key=hello,
                suite="jammy",
                source_id="hello",
                commands=(("edgeapt-hello",),),
            ),
        ),
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
    doggo = make_deb_key(package="doggo", deb_version="1.1.7-1")
    hello = make_deb_key(package="edgeapt-hello", deb_version="0.1.0-1")
    lock = make_lock(
        artifacts=(make_artifact(deb_key=doggo), make_artifact(deb_key=hello)),
        publications=(
            make_publication(deb_key=doggo, suite="focal", source_id="doggo"),
            make_publication(deb_key=doggo, suite="noble", source_id="doggo"),
            make_publication(deb_key=hello, suite="noble", source_id="hello"),
        ),
    )

    cases = build_e2e_test_cases(
        lock,
        suite_filter="noble",
        source_filters=("doggo",),
        package_filter="doggo",
    )

    assert [(case.suite, case.source_id, case.package) for case in cases] == [
        ("noble", "doggo", "doggo")
    ]


def test_e2e_install_uses_package_version_pin() -> None:
    case = _case()

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


@pytest.mark.parametrize(
    "failure_stage",
    [None, "setup", "install", "second-command", "remove"],
)
def test_run_group_preserves_argv_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str | None,
) -> None:
    command_one = ("doggo", "--format", "literal value; $(not-a-command)")
    command_two = ("doggo", "--version")
    install = (
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
    first = ("docker", "exec", "container-id", *command_one)
    second = ("docker", "exec", "container-id", *command_two)
    remove = (
        "docker",
        "exec",
        "container-id",
        "env",
        "DEBIAN_FRONTEND=noninteractive",
        "apt-get",
        "remove",
        "-y",
        "doggo",
    )
    cleanup = ("docker", "rm", "-f", "container-id")
    calls: list[tuple[str, ...]] = []
    events: list[E2EEvent] = []

    def fake_run(
        args: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert not isinstance(args, str)
        assert kwargs.get("shell", False) is False
        assert kwargs.get("check") is False
        assert kwargs.get("capture_output") is True
        assert kwargs.get("text") is True
        argv = tuple(args)
        calls.append(argv)
        failed = (
            (
                failure_stage == "setup"
                and argv[:5] == ("docker", "exec", "container-id", "bash", "-lc")
            )
            or (failure_stage == "install" and argv == install)
            or (failure_stage == "second-command" and argv == second)
            or (failure_stage == "remove" and argv == remove)
        )
        return subprocess.CompletedProcess(
            args=list(args),
            returncode=1 if failed else 0,
            stdout="container-id\n" if argv[:2] == ("docker", "run") else "",
            stderr="simulated failure" if failed else "",
        )

    monkeypatch.setattr("edgeapt.e2e.subprocess.run", fake_run)
    case = E2ETestCase(
        suite="noble",
        arch="amd64",
        source_ids=("doggo",),
        package="doggo",
        version="1.1.7-1",
        commands=(command_one, command_two),
    )
    group = E2EGroup(
        suite="noble",
        arch="amd64",
        image="ubuntu:24.04",
        cases=(case,),
    )
    failure_context = "e2e-command" if failure_stage == "second-command" else failure_stage
    error_context = (
        pytest.raises(CommandError, match=f"E2E failed during {failure_context}")
        if failure_stage is not None
        else nullcontext()
    )
    with error_context:
        _run_group(
            group=group,
            port=12345,
            test_keyring="/test keyring.gpg",
            apt_cache=False,
            on_event=events.append,
        )

    assert calls[0][:4] == ("docker", "run", "--detach", "--rm")
    assert calls[0][-3:] == ("ubuntu:24.04", "sleep", "infinity")
    assert calls[1][:5] == ("docker", "exec", "container-id", "bash", "-lc")
    if failure_stage == "setup":
        expected_tail = [cleanup]
    elif failure_stage == "install":
        expected_tail = [install, cleanup]
    else:
        expected_tail = [install, first, second, remove, cleanup]
    assert calls[2:] == expected_tail
    started = [event.command for event in events if event.kind == "test_start"]
    assert started == (
        [] if failure_stage in {"setup", "install"} else [command_one, command_two]
    )


def test_e2e_removes_package_after_validation() -> None:
    case = _case()

    assert docker_remove_args("container-id", case) == (
        "docker",
        "exec",
        "container-id",
        "env",
        "DEBIAN_FRONTEND=noninteractive",
        "apt-get",
        "remove",
        "-y",
        "doggo",
    )


def test_e2e_rejects_unknown_suite_filter() -> None:
    with pytest.raises(ValidationError, match="unsupported e2e suite"):
        build_e2e_test_cases(make_lock(), suite_filter="unknown")


def test_e2e_keeps_multiple_commands_in_one_install_case() -> None:
    key = make_deb_key(package="foo")
    lock = make_lock(
        artifacts=(make_artifact(deb_key=key),),
        publications=(
            make_publication(
                deb_key=key,
                commands=(("foo", "--version"), ("foo", "--help")),
            ),
        ),
    )

    cases = build_e2e_test_cases(lock)

    assert len(cases) == 1
    assert cases[0].commands == (("foo", "--help"), ("foo", "--version"))


def test_e2e_source_filter_keeps_only_matching_claim_commands() -> None:
    key = make_deb_key(package="foo")
    lock = make_lock(
        artifacts=(make_artifact(deb_key=key),),
        publications=(
            make_publication(
                deb_key=key,
                source_commands=(
                    ("foo-old", (("foo", "--version"),)),
                    (
                        "foo-new",
                        (("foo", "--version"), ("foo-extra", "--version")),
                    ),
                ),
            ),
        ),
    )

    cases = build_e2e_test_cases(lock, source_filters=("foo-old",))

    assert len(cases) == 1
    assert cases[0].source_ids == ("foo-old",)
    assert cases[0].commands == (("foo", "--version"),)


def test_e2e_runs_arch_all_package_on_amd64() -> None:
    key = make_deb_key(package="example-font", arch="all")
    lock = make_lock(
        artifacts=(make_artifact(deb_key=key),),
        publications=(make_publication(deb_key=key),),
    )

    cases = build_e2e_test_cases(lock)

    assert len(cases) == 1
    assert cases[0].arch == "amd64"


def test_e2e_rejects_invalid_jobs_before_starting_docker() -> None:
    with pytest.raises(ValidationError, match="jobs must be a positive integer"):
        run_e2e(jobs=0)


def test_e2e_rejects_clear_cache_when_cache_is_disabled() -> None:
    with pytest.raises(ValidationError, match="clear_apt_cache"):
        run_e2e(apt_cache=False, clear_apt_cache=True)


def test_e2e_rejects_unsupported_architecture_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = subprocess.CompletedProcess(
        args=["apt-get", "update"],
        returncode=0,
        stdout="",
        stderr=(
            "N: Skipping acquire of configured file 'main/binary-all/Packages' "
            "as repository 'https://example.test jammy InRelease' doesn't support "
            "architecture 'all'\n"
        ),
    )

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return result

    monkeypatch.setattr("edgeapt.e2e.subprocess.run", run)

    with pytest.raises(CommandError, match="doesn't support architecture"):
        _run_checked(
            ("apt-get", "update"),
            E2ECommandContext(stage="setup", suite="jammy", arch="all"),
        )


def test_clear_e2e_apt_cache_removes_archives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("edgeapt.e2e.E2E_APT_CACHE_DIR", tmp_path)
    archive = tmp_path / "noble-amd64" / "archives" / "package.deb"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"package")

    clear_e2e_apt_cache("noble", "amd64")

    assert not archive.exists()
    assert archive.parent.exists()


def test_e2e_rejects_stale_generated_repository(tmp_path: Path) -> None:
    lock = make_lock()
    manifest = tmp_path / "packages.json"
    write_json(
        manifest,
        {
            "schema": PACKAGE_MANIFEST_SCHEMA,
            "profile": "test",
            "generated_at": "2026-07-04T00:00:00Z",
            "packages": [],
        },
    )

    with pytest.raises(ValidationError, match="test repository is stale"):
        validate_e2e_repository(lock, manifest)


def test_e2e_accepts_repository_generated_from_current_lock(tmp_path: Path) -> None:
    lock = make_lock()
    manifest = tmp_path / "packages.json"
    write_json(
        manifest,
        {
            "schema": PACKAGE_MANIFEST_SCHEMA,
            "profile": "test",
            "generated_at": lock.generated_at,
            "packages": [],
        },
    )

    assert validate_e2e_repository(lock, manifest) is None


def test_e2e_reads_scoped_repository_manifest(tmp_path: Path) -> None:
    lock = make_lock()
    manifest = tmp_path / "packages.json"
    write_json(
        manifest,
        {
            "schema": PACKAGE_MANIFEST_SCHEMA,
            "profile": "test",
            "generated_at": lock.generated_at,
            "scope": {"sources": ["bar", "foo"]},
            "packages": [],
        },
    )

    assert validate_e2e_repository(lock, manifest) == ("bar", "foo")


def test_e2e_scoped_repository_requires_matching_source_filter() -> None:
    with pytest.raises(ValidationError, match="requires at least one --source"):
        validate_e2e_scope(("foo",), ())
    with pytest.raises(ValidationError, match="outside the generated"):
        validate_e2e_scope(("foo",), ("bar",))

    validate_e2e_scope(("bar", "foo"), ("foo",))


def _case() -> E2ETestCase:
    return E2ETestCase(
        suite="noble",
        arch="amd64",
        source_ids=("doggo",),
        package="doggo",
        version="1.1.7-1",
        commands=(("doggo", "--version"),),
    )
