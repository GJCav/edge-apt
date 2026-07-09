from __future__ import annotations

import signal
import socket
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence

import attrs

from edgeapt.constants import LOCK_PATH, ROOT, SUPPORTED_E2E_ARCHES, TEST_PUBLIC_DIR
from edgeapt.errors import CommandError, ValidationError
from edgeapt.keyring import profile_public_keyring
from edgeapt.lockfile import load_lock
from edgeapt.models import ArtifactFact, LockFile
from edgeapt.util import require_executable

E2E_SUITE_IMAGES = {
    "focal": "ubuntu:20.04",
    "jammy": "ubuntu:22.04",
    "noble": "ubuntu:24.04",
    "resolute": "ubuntu:26.04",
}


@attrs.define(kw_only=True, frozen=True)
class E2ETestCase:
    suite: str
    arch: str
    source_id: str
    package: str
    version: str
    command: tuple[str, ...]


@attrs.define(kw_only=True, frozen=True)
class E2EGroup:
    suite: str
    arch: str
    image: str
    cases: tuple[E2ETestCase, ...]


@attrs.define(kw_only=True, frozen=True)
class E2EEvent:
    kind: str
    suite: str | None = None
    arch: str | None = None
    image: str | None = None
    source_id: str | None = None
    package: str | None = None
    version: str | None = None
    command: tuple[str, ...] = ()
    message: str = ""


@attrs.define(kw_only=True, frozen=True)
class E2ERunResult:
    groups: int
    tested: int
    skipped: int


@attrs.define(kw_only=True, frozen=True)
class E2ECommandContext:
    stage: str
    suite: str | None = None
    arch: str | None = None
    source_id: str | None = None
    package: str | None = None
    version: str | None = None
    command: tuple[str, ...] = ()


def run_e2e(
    *,
    suite: str | None = None,
    source: str | None = None,
    package: str | None = None,
    on_event: Callable[[E2EEvent], None] | None = None,
) -> E2ERunResult:
    require_executable("docker")
    test_keyring = profile_public_keyring("test")
    if not test_keyring.exists():
        raise ValidationError(f"missing test keyring: {test_keyring}")
    if not TEST_PUBLIC_DIR.exists():
        raise ValidationError(f"missing test repository output: {TEST_PUBLIC_DIR}")
    lock = load_lock(LOCK_PATH)
    if lock is None:
        raise ValidationError("lock.json does not exist; run `uv run repackage` first")

    cases = build_e2e_test_cases(
        lock,
        suite_filter=suite,
        source_filter=source,
        package_filter=package,
    )
    if not cases:
        raise ValidationError("no e2e test cases matched the selected filters")

    skipped = 0
    supported_cases: list[E2ETestCase] = []
    for case in cases:
        if case.arch not in SUPPORTED_E2E_ARCHES:
            skipped += 1
            _emit(
                on_event,
                kind="test_skip",
                suite=case.suite,
                arch=case.arch,
                source_id=case.source_id,
                package=case.package,
                version=case.version,
                command=case.command,
                message=f"Skipping unsupported e2e architecture: {case.arch}",
            )
        else:
            supported_cases.append(case)
    groups = group_e2e_test_cases(supported_cases)
    if not groups:
        raise ValidationError("no supported e2e test cases matched the selected filters")

    port = _free_port()
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
            "--directory",
            str(TEST_PUBLIC_DIR),
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    tested = 0
    try:
        time.sleep(1)
        if server.poll() is not None:
            raise CommandError("local HTTP server failed to start")
        for group in groups:
            _run_group(
                group=group,
                port=port,
                test_keyring=test_keyring.resolve().as_posix(),
                on_event=on_event,
            )
            tested += len(group.cases)
    finally:
        _terminate(server)
    return E2ERunResult(groups=len(groups), tested=tested, skipped=skipped)


def build_e2e_test_cases(
    lock: LockFile,
    *,
    suite_filter: str | None = None,
    source_filter: str | None = None,
    package_filter: str | None = None,
) -> tuple[E2ETestCase, ...]:
    if suite_filter is not None and suite_filter not in E2E_SUITE_IMAGES:
        raise ValidationError(f"unsupported e2e suite: {suite_filter}")
    cases: list[E2ETestCase] = []
    for source_id in sorted(lock.sources):
        if source_filter is not None and source_id != source_filter:
            continue
        source_lock = lock.sources[source_id]
        if not source_lock.e2e_command:
            raise ValidationError(f"{source_id}: missing e2e_command in lock.json")
        artifacts = sorted(
            source_lock.artifacts,
            key=lambda artifact: (artifact.package, artifact.version, artifact.arch),
        )
        for artifact in artifacts:
            if package_filter is not None and artifact.package != package_filter:
                continue
            for suite in _sort_suites(artifact.suites):
                if suite_filter is not None and suite != suite_filter:
                    continue
                _validate_suite_has_image(suite)
                cases.append(
                    _case_from_artifact(source_id, source_lock.e2e_command, suite, artifact)
                )
    return tuple(cases)


def group_e2e_test_cases(cases: Iterable[E2ETestCase]) -> tuple[E2EGroup, ...]:
    grouped: dict[tuple[str, str], list[E2ETestCase]] = defaultdict(list)
    for case in cases:
        _validate_suite_has_image(case.suite)
        grouped[(case.suite, case.arch)].append(case)
    groups: list[E2EGroup] = []
    for suite, arch in sorted(grouped, key=lambda item: (_suite_rank(item[0]), item[1])):
        cases_for_group = tuple(
            sorted(
                grouped[(suite, arch)],
                key=lambda case: (case.source_id, case.package, case.version),
            )
        )
        groups.append(
            E2EGroup(
                suite=suite,
                arch=arch,
                image=E2E_SUITE_IMAGES[suite],
                cases=cases_for_group,
            )
        )
    return tuple(groups)


def docker_install_args(container_id: str, case: E2ETestCase) -> tuple[str, ...]:
    return (
        "docker",
        "exec",
        container_id,
        "env",
        "DEBIAN_FRONTEND=noninteractive",
        "apt-get",
        "install",
        "-y",
        f"{case.package}={case.version}",
    )


def docker_e2e_command_args(container_id: str, case: E2ETestCase) -> tuple[str, ...]:
    return ("docker", "exec", container_id, *case.command)


def _run_group(
    *,
    group: E2EGroup,
    port: int,
    test_keyring: str,
    on_event: Callable[[E2EEvent], None] | None,
) -> None:
    _emit(
        on_event,
        kind="group_start",
        suite=group.suite,
        arch=group.arch,
        image=group.image,
        message=f"Starting {group.suite}/{group.arch}",
    )
    context = E2ECommandContext(stage="docker-run", suite=group.suite, arch=group.arch)
    container_name = f"edgeapt-e2e-{group.suite}-{group.arch}-{uuid.uuid4().hex[:12]}"
    container = _run_checked(
        (
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container_name,
            "--network",
            "host",
            "-v",
            f"{test_keyring}:/edgeapt-key.gpg:ro",
            group.image,
            "sleep",
            "infinity",
        ),
        context,
    ).stdout.strip()
    try:
        _run_checked(
            (
                "docker",
                "exec",
                container,
                "bash",
                "-lc",
                _setup_script(suite=group.suite, arch=group.arch, port=port),
            ),
            E2ECommandContext(stage="setup", suite=group.suite, arch=group.arch),
        )
        for case in group.cases:
            _emit(
                on_event,
                kind="test_start",
                suite=case.suite,
                arch=case.arch,
                image=group.image,
                source_id=case.source_id,
                package=case.package,
                version=case.version,
                command=case.command,
                message=f"Testing {case.package} {case.version}",
            )
            _run_checked(
                docker_install_args(container, case),
                _context_for_case(stage="install", case=case),
            )
            _run_checked(
                docker_e2e_command_args(container, case),
                _context_for_case(stage="e2e-command", case=case),
            )
            _emit(
                on_event,
                kind="test_pass",
                suite=case.suite,
                arch=case.arch,
                image=group.image,
                source_id=case.source_id,
                package=case.package,
                version=case.version,
                command=case.command,
                message=f"Passed {case.package} {case.version}",
            )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container],
            check=False,
            capture_output=True,
            text=True,
        )


def _run_checked(
    args: Sequence[str],
    context: E2ECommandContext,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CommandError(_format_failure(args=args, context=context, result=result))
    return result


def _setup_script(*, suite: str, arch: str, port: int) -> str:
    return f"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates
install -d -m 0755 /etc/apt/keyrings
cp /edgeapt-key.gpg /etc/apt/keyrings/edgeapt-test-archive-keyring.gpg
cat > /etc/apt/sources.list.d/edgeapt.list <<'EOF'
deb [arch={arch} signed-by=/etc/apt/keyrings/edgeapt-test-archive-keyring.gpg] http://127.0.0.1:{port} {suite} main
EOF
apt-get update
"""


def _case_from_artifact(
    source_id: str,
    command: tuple[str, ...],
    suite: str,
    artifact: ArtifactFact,
) -> E2ETestCase:
    return E2ETestCase(
        suite=suite,
        arch=artifact.arch,
        source_id=source_id,
        package=artifact.package,
        version=artifact.version,
        command=command,
    )


def _context_for_case(*, stage: str, case: E2ETestCase) -> E2ECommandContext:
    return E2ECommandContext(
        stage=stage,
        suite=case.suite,
        arch=case.arch,
        source_id=case.source_id,
        package=case.package,
        version=case.version,
        command=case.command,
    )


def _format_failure(
    *,
    args: Sequence[str],
    context: E2ECommandContext,
    result: subprocess.CompletedProcess[str],
) -> str:
    lines = [
        f"E2E failed during {context.stage}",
        f"command: {' '.join(args)}",
        f"exit code: {result.returncode}",
    ]
    for label, value in (
        ("suite", context.suite),
        ("arch", context.arch),
        ("source", context.source_id),
        ("package", context.package),
        ("version", context.version),
    ):
        if value is not None:
            lines.append(f"{label}: {value}")
    if context.command:
        lines.append(f"e2e_command: {' '.join(context.command)}")
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if stdout:
        lines.extend(["stdout:", stdout])
    if stderr:
        lines.extend(["stderr:", stderr])
    return "\n".join(lines)


def _sort_suites(suites: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(suites, key=_suite_rank))


def _suite_rank(suite: str) -> int:
    try:
        return tuple(E2E_SUITE_IMAGES).index(suite)
    except ValueError:
        return len(E2E_SUITE_IMAGES)


def _validate_suite_has_image(suite: str) -> None:
    if suite not in E2E_SUITE_IMAGES:
        raise ValidationError(f"no e2e Ubuntu image configured for suite: {suite}")


def _emit(
    on_event: Callable[[E2EEvent], None] | None,
    *,
    kind: str,
    message: str,
    suite: str | None = None,
    arch: str | None = None,
    image: str | None = None,
    source_id: str | None = None,
    package: str | None = None,
    version: str | None = None,
    command: tuple[str, ...] = (),
) -> None:
    if on_event is None:
        return
    on_event(
        E2EEvent(
            kind=kind,
            suite=suite,
            arch=arch,
            image=image,
            source_id=source_id,
            package=package,
            version=version,
            command=command,
            message=message,
        )
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
