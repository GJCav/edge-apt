from __future__ import annotations

from collections.abc import Callable

import cyclopts
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from edgeapt.constants import LOCK_PATH, SOURCES_DIR
from edgeapt.e2e import run_e2e
from edgeapt.errors import EdgeAptError
from edgeapt.keyring import check_signing_key, ensure_test_key
from edgeapt.repackage import repackage_all
from edgeapt.repo import generate_repo
from edgeapt.sources import load_sources
from edgeapt.ubuntu_index import ensure_no_ubuntu_package_conflicts
from edgeapt.ubuntu_index import refresh_ubuntu_indexes

console = Console()


def guide() -> None:
    """Show the recommended EdgeAPT workflow."""
    table = Table(title="EdgeAPT Workflow")
    table.add_column("Phase", style="bold")
    table.add_column("Command")
    table.add_row("Setup", "uv run init-test-key")
    table.add_row("Setup", "uv run check-key --profile test")
    table.add_row("Setup", "uv run refresh-ubuntu-index")
    table.add_row("Validate", "uv run validate")
    table.add_row("Repackage", "uv run repackage")
    table.add_row("Generate", "uv run generate --profile test")
    table.add_row("Verify", "uv run e2e")
    console.print(table)
    console.print(
        Panel(
            "uv run check-key --profile prod\nuv run generate --profile prod",
            title="Production",
        )
    )


def validate(skip_ubuntu_conflicts: bool = False) -> None:
    """Validate sources/*.yaml."""
    sources = load_sources(SOURCES_DIR)
    if not skip_ubuntu_conflicts:
        ensure_no_ubuntu_package_conflicts(sources)
    table = Table(title="EdgeAPT Sources")
    table.add_column("id")
    table.add_column("template")
    table.add_column("package")
    table.add_column("ubuntu override")
    table.add_column("upstreams", justify="right")
    for source in sources:
        table.add_row(
            source.id,
            source.template,
            source.package,
            "yes" if source.allow_ubuntu_package_override else "no",
            str(len(source.upstream)),
        )
    console.print(table)
    console.print(f"[green]Validated {len(sources)} source(s).[/green]")


def refresh_ubuntu_index() -> None:
    """Refresh cached Ubuntu official package indexes."""
    indexes = refresh_ubuntu_indexes()
    table = Table(title="Ubuntu Package Index")
    table.add_column("suite")
    table.add_column("arch")
    table.add_column("packages", justify="right")
    table.add_column("refreshed")
    for index in indexes:
        table.add_row(
            index.suite,
            index.arch,
            str(len(index.packages)),
            index.refreshed_at,
        )
    console.print(table)
    console.print("[green]Ubuntu package indexes refreshed.[/green]")


def init_test_key() -> None:
    """Create, import, or export the test archive signing key."""
    key = ensure_test_key()
    console.print("[green]Test signing key ready.[/green]")
    console.print(f"fingerprint: {key.fingerprint}")
    console.print(f"public keyring: {key.public_keyring}")
    console.print(f"public ascii: {key.public_ascii}")
    console.print(f"secret ascii: {key.secret_ascii}")


def check_key(profile: str = "test") -> None:
    """Check archive signing key files and local GPG secret key."""
    key = check_signing_key(profile)
    console.print(f"[green]{profile} signing key ready.[/green]")
    console.print(f"fingerprint: {key.fingerprint}")
    console.print(f"public keyring: {key.public_keyring}")
    console.print(f"public ascii: {key.public_ascii}")


def repackage() -> None:
    """Run upstream repackaging and write packages/ plus lock.json."""
    lock = repackage_all()
    console.print(f"[green]Wrote {LOCK_PATH}[/green]")
    console.print(f"[green]Processed {len(lock.sources)} source(s).[/green]")


def generate(profile: str = "test") -> None:
    """Generate signed APT repository output."""
    result = generate_repo(profile=profile)
    console.print(
        f"[green]Generated {result.profile} signed APT repository at "
        f"{result.output_dir}[/green]"
    )
    console.print(f"signing key: {result.signing_key_fingerprint}")


def e2e(
    suite: str = "noble",
    image: str = "ubuntu:24.04",
    package: str = "edgeapt-hello",
    command: str = "edgeapt-hello",
) -> None:
    """Run a Docker apt install smoke test against the local test repo."""
    run_e2e(suite=suite, image=image, package=package, command=command)
    console.print("[green]E2E smoke test passed.[/green]")


def guide_main() -> None:
    _run_cli(guide)


def validate_main() -> None:
    _run_cli(validate)


def refresh_ubuntu_index_main() -> None:
    _run_cli(refresh_ubuntu_index)


def init_test_key_main() -> None:
    _run_cli(init_test_key)


def check_key_main() -> None:
    _run_cli(check_key)


def repackage_main() -> None:
    _run_cli(repackage)


def generate_main() -> None:
    _run_cli(generate)


def e2e_main() -> None:
    _run_cli(e2e)


def _run_cli(command: Callable[..., object]) -> None:
    try:
        cyclopts.run(command)
    except EdgeAptError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc
