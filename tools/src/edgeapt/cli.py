from __future__ import annotations

from collections.abc import Callable

import cyclopts
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from edgeapt.constants import (
    LOCK_PATH,
    SOURCES_DIR,
    SUPPORTED_SUITES,
    UBUNTU_COMPONENTS,
    UBUNTU_INDEX_ARCHES,
)
from edgeapt.config import load_config
from edgeapt.e2e import run_e2e
from edgeapt.errors import EdgeAptError
from edgeapt.keyring import check_signing_key, ensure_test_key
from edgeapt.repackage import repackage_all, RepackageEvent
from edgeapt.repo import generate_repo
from edgeapt.sources import load_sources
from edgeapt.ubuntu_index import ensure_no_ubuntu_package_conflicts
from edgeapt.ubuntu_index import refresh_ubuntu_indexes
from edgeapt.ubuntu_index import UbuntuIndexRefreshEvent

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
    config = load_config()
    total_downloads = (
        len(SUPPORTED_SUITES) * len(UBUNTU_INDEX_ARCHES) * len(UBUNTU_COMPONENTS)
    )
    console.print(f"Ubuntu mirror: {config.ubuntu_mirror_url}")
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Refreshing Ubuntu package indexes", total=total_downloads)

        def on_download_start(event: UbuntuIndexRefreshEvent) -> None:
            progress.update(
                task_id,
                description=f"Downloading {event.suite}/{event.arch} {event.component}",
                advance=1,
            )

        indexes = refresh_ubuntu_indexes(
            base_url=config.ubuntu_mirror_url,
            on_download_start=on_download_start,
        )
        progress.update(task_id, description="Ubuntu package indexes refreshed")
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
    sources = load_sources(SOURCES_DIR)
    total_artifacts = sum(len(source.upstream) for source in sources)
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        TextColumn("[progress.percentage]{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Waiting to start", total=total_artifacts)

        def on_event(event: RepackageEvent) -> None:
            if event.kind == "artifact_done":
                progress.console.print(_format_repackage_done(event), soft_wrap=True)
                progress.update(
                    task_id,
                    description=_format_repackage_status(event),
                    advance=1,
                )
            elif event.kind in {
                "source_start",
                "fetch_start",
                "extract_start",
                "inspect_deb_start",
            }:
                progress.console.print(_format_repackage_log(event), soft_wrap=True)
                progress.update(task_id, description=_format_repackage_status(event))
            else:
                progress.update(task_id, description=event.message)

        lock = repackage_all(on_event=on_event)
        progress.update(task_id, description="Repackaging complete")

    artifact_count = sum(len(source_lock.artifacts) for source_lock in lock.sources.values())
    console.print(f"[green]Wrote {LOCK_PATH}[/green]")
    console.print(
        f"[green]Processed {len(lock.sources)} source(s), "
        f"{artifact_count} artifact(s).[/green]"
    )


def generate(profile: str = "test") -> None:
    """Generate signed APT repository output."""
    result = generate_repo(profile=profile)
    console.print(
        f"[green]Generated {result.profile} signed APT repository at "
        f"{result.output_dir}[/green]"
    )
    console.print(f"install page: {result.index_html}")
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


def _format_repackage_log(event: RepackageEvent) -> str:
    if event.kind == "source_start":
        return "\n".join(
            [
                f"\n---- {event.package} ----",
                _format_repackage_field("Template", event.template),
                _format_repackage_field("ID", event.source_id),
            ]
        )
    if event.kind == "fetch_start":
        return "\n".join(
            [
                _format_repackage_field(
                    "Package",
                    f"{event.package} {event.version} {event.arch}",
                ),
                _format_repackage_field("Fetch", event.url),
            ]
        )
    if event.kind == "extract_start":
        return _format_repackage_field("Extract", event.message)
    if event.kind == "inspect_deb_start":
        return _format_repackage_field("Inspect", "upstream deb control")
    return event.message


def _format_repackage_done(event: RepackageEvent) -> str:
    return "\n".join(
        [
            _format_repackage_field("Artifact", event.path),
            _format_repackage_field("Size", _format_bytes(event.size)),
        ]
    )


def _format_repackage_status(event: RepackageEvent) -> str:
    package = event.package or "-"
    version = event.version or "-"
    arch = event.arch or "-"
    return f"Processing {package} {version} {arch}"


def _format_bytes(size: int | None) -> str:
    if size is None:
        return "-"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.2f} KiB"
    return f"{size / (1024 * 1024):.2f} MiB"


def _format_repackage_field(label: str, value: object) -> str:
    return f"{label + ':':<10} {value}"


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
