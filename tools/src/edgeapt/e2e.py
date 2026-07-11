from __future__ import annotations

import fcntl
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from collections.abc import Callable, Generator, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

import attrs

from edgeapt.constants import (
    E2E_APT_CACHE_DIR,
    LOCK_PATH,
    ROOT,
    SUPPORTED_E2E_ARCHES,
    TEST_PUBLIC_DIR,
)
from edgeapt.errors import CommandError, ValidationError
from edgeapt.domain.artifacts import ArtifactFact
from edgeapt.domain.lock import LockFile
from edgeapt.infrastructure.lock_store import load_lock
from edgeapt.infrastructure.signing import profile_public_keyring
from edgeapt.package_manifest import PACKAGE_MANIFEST_SCHEMA
from edgeapt.util import read_json, require_executable

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
    source_ids: tuple[str, ...]
    package: str
    version: str
    commands: tuple[tuple[str, ...], ...]

    @property
    def source_id(self) -> str:
        return self.source_ids[0]


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
    jobs: int = 4,
    apt_cache: bool = True,
    clear_apt_cache: bool = False,
    on_event: Callable[[E2EEvent], None] | None = None,
) -> E2ERunResult:
    if jobs < 1:
        raise ValidationError("jobs must be a positive integer")
    if clear_apt_cache and not apt_cache:
        raise ValidationError("clear_apt_cache cannot be used with apt_cache disabled")
    require_executable("docker")
    require_executable("pnpm")
    test_keyring = profile_public_keyring("test")
    if not test_keyring.exists():
        raise ValidationError(f"missing test keyring: {test_keyring}")
    if not TEST_PUBLIC_DIR.exists():
        raise ValidationError(f"missing test repository output: {TEST_PUBLIC_DIR}")
    lock = load_lock(LOCK_PATH)
    if lock is None:
        raise ValidationError("lock.json does not exist; run `uv run repackage` first")
    validate_e2e_repository(lock, TEST_PUBLIC_DIR / "packages.json")

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
                command=case.commands[0],
                message=f"Skipping unsupported e2e architecture: {case.arch}",
            )
        else:
            supported_cases.append(case)
    groups = group_e2e_test_cases(supported_cases)
    if not groups:
        raise ValidationError("no supported e2e test cases matched the selected filters")

    port = _free_port()
    if clear_apt_cache:
        for group in groups:
            clear_e2e_apt_cache(group.suite, group.arch)

    tested = 0
    with _wrangler_server(port):
        callback_lock = threading.Lock()

        def synchronized_event(event: E2EEvent) -> None:
            if on_event is None:
                return
            with callback_lock:
                on_event(event)

        failures: list[tuple[E2EGroup, Exception]] = []
        with ThreadPoolExecutor(max_workers=min(jobs, len(groups))) as executor:
            futures = {
                executor.submit(
                    _run_group,
                    group=group,
                    port=port,
                    test_keyring=test_keyring.resolve().as_posix(),
                    apt_cache=apt_cache,
                    on_event=synchronized_event,
                ): group
                for group in groups
            }
            for future in as_completed(futures):
                group = futures[future]
                try:
                    future.result()
                    tested += len(group.cases)
                except Exception as exc:
                    failures.append((group, exc))
        if failures:
            lines = ["E2E group failure(s):"]
            for group, exc in sorted(
                failures,
                key=lambda item: (_suite_rank(item[0].suite), item[0].arch),
            ):
                lines.append(f"\n[{group.suite}/{group.arch}]\n{exc}")
            raise CommandError("\n".join(lines))
    return E2ERunResult(groups=len(groups), tested=tested, skipped=skipped)


def validate_e2e_repository(lock: LockFile, manifest_path: Path) -> None:
    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError) as error:
        raise ValidationError(
            "invalid test repository output; run `uv run generate --profile test`"
        ) from error
    if (
        manifest.get("schema") != PACKAGE_MANIFEST_SCHEMA
        or manifest.get("profile") != "test"
        or manifest.get("generated_at") != lock.generated_at
    ):
        raise ValidationError(
            "test repository is stale; run `uv run generate --profile test`"
        )


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
    for publication in lock.publications:
        claims = tuple(
            claim
            for claim in publication.e2e_claims
            if source_filter is None or claim.provenance.source_id == source_filter
        )
        if not claims:
            continue
        if package_filter is not None and publication.key.package != package_filter:
            continue
        if suite_filter is not None and publication.key.suite != suite_filter:
            continue
        _validate_suite_has_image(publication.key.suite)
        artifact = lock.artifact_for(publication.artifact)
        source_ids = tuple(
            sorted({claim.provenance.source_id for claim in claims})
        )
        commands = tuple(
            sorted({command for claim in claims for command in claim.commands})
        )
        cases.append(
            _case_from_artifact(
                source_ids,
                commands,
                publication.key.suite,
                artifact,
            )
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
    return ("docker", "exec", container_id, *case.commands[0])


def docker_remove_args(container_id: str, case: E2ETestCase) -> tuple[str, ...]:
    return (
        "docker",
        "exec",
        container_id,
        "env",
        "DEBIAN_FRONTEND=noninteractive",
        "apt-get",
        "remove",
        "-y",
        case.package,
    )


def _run_group(
    *,
    group: E2EGroup,
    port: int,
    test_keyring: str,
    apt_cache: bool,
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
    with _apt_cache_lock(group.suite, group.arch, enabled=apt_cache) as cache_dir:
        run_args = [
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
        ]
        if cache_dir is not None:
            run_args.extend(
                ["-v", f"{cache_dir.resolve()}:/var/cache/apt/archives"]
            )
        run_args.extend([group.image, "sleep", "infinity"])
        container = _run_checked(tuple(run_args), context).stdout.strip()
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
                installed = False
                try:
                    _run_checked(
                        docker_install_args(container, case),
                        _context_for_case(stage="install", case=case),
                    )
                    installed = True
                    for command in case.commands:
                        _emit(
                            on_event,
                            kind="test_start",
                            suite=case.suite,
                            arch=case.arch,
                            image=group.image,
                            source_id=case.source_id,
                            package=case.package,
                            version=case.version,
                            command=command,
                            message=f"Testing {case.package} {case.version}",
                        )
                        _run_checked(
                            ("docker", "exec", container, *command),
                            _context_for_case(
                                stage="e2e-command",
                                case=case,
                                command=command,
                            ),
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
                            command=command,
                            message=f"Passed {case.package} {case.version}",
                        )
                finally:
                    if installed:
                        _run_checked(
                            docker_remove_args(container, case),
                            _context_for_case(stage="remove", case=case),
                        )
        finally:
            if cache_dir is not None:
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        container,
                        "chmod",
                        "-R",
                        "a+rwX",
                        "/var/cache/apt/archives",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
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
rm -f /etc/apt/apt.conf.d/docker-clean
cat > /etc/apt/apt.conf.d/99edgeapt-keep-cache <<'EOF'
Binary::apt::APT::Keep-Downloaded-Packages "true";
Binary::apt-get::APT::Keep-Downloaded-Packages "true";
EOF
install -d -m 0755 /var/cache/apt/archives/partial
install -d -m 0755 /etc/apt/keyrings
cp /edgeapt-key.gpg /etc/apt/keyrings/edgeapt-test-archive-keyring.gpg
cat > /etc/apt/sources.list.d/edgeapt.list <<'EOF'
deb [arch={arch} signed-by=/etc/apt/keyrings/edgeapt-test-archive-keyring.gpg] http://127.0.0.1:{port} {suite} main
EOF
apt-get update
"""


def _case_from_artifact(
    source_ids: tuple[str, ...],
    commands: tuple[tuple[str, ...], ...],
    suite: str,
    artifact: ArtifactFact,
) -> E2ETestCase:
    return E2ETestCase(
        suite=suite,
        arch=artifact.arch,
        source_ids=source_ids,
        package=artifact.package,
        version=artifact.version,
        commands=commands,
    )


def _context_for_case(
    *,
    stage: str,
    case: E2ETestCase,
    command: tuple[str, ...] = (),
) -> E2ECommandContext:
    return E2ECommandContext(
        stage=stage,
        suite=case.suite,
        arch=case.arch,
        source_id=case.source_id,
        package=case.package,
        version=case.version,
        command=command,
    )


def clear_e2e_apt_cache(suite: str, arch: str) -> None:
    with _apt_cache_lock(suite, arch, enabled=True) as cache_dir:
        if cache_dir is None:
            return
        shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)


@contextmanager
def _apt_cache_lock(
    suite: str,
    arch: str,
    *,
    enabled: bool,
) -> Generator[Path | None, None, None]:
    if not enabled:
        yield None
        return
    group_dir = E2E_APT_CACHE_DIR / f"{suite}-{arch}"
    cache_dir = group_dir / "archives"
    group_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = group_dir / ".lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield cache_dir
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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


@contextmanager
def _wrangler_server(port: int) -> Generator[None, None, None]:
    log_path = ROOT / "tmp" / "e2e-wrangler.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                "pnpm",
                "exec",
                "wrangler",
                "dev",
                "--config",
                "wrangler.test.jsonc",
                "--ip",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warn",
            ],
            cwd=ROOT / "cloudflare",
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        try:
            _wait_for_wrangler(process, port=port, log=log)
            yield
        finally:
            _terminate(process)


def _wait_for_wrangler(
    process: subprocess.Popen[str],
    *,
    port: int,
    log: TextIO,
) -> None:
    deadline = time.monotonic() + 30
    url = f"http://127.0.0.1:{port}/packages.json"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CommandError(_wrangler_failure("exited before startup", log))
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.1)
    raise CommandError(_wrangler_failure("did not become ready", log))


def _wrangler_failure(message: str, log: TextIO) -> str:
    log.flush()
    log.seek(0)
    output = log.read().strip()
    return f"Wrangler {message}" + (f":\n{output}" if output else "")


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
