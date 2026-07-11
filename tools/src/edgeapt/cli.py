from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import cyclopts
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from edgeapt.constants import (
    LOCK_PATH,
    ROOT,
    SUPPORTED_SUITES,
    UBUNTU_COMPONENTS,
    UBUNTU_INDEX_ARCHES,
)
from edgeapt.config import load_config
from edgeapt.e2e import run_e2e, E2EEvent
from edgeapt.errors import EdgeAptError
from edgeapt.infrastructure.signing import check_signing_key, ensure_test_key
from edgeapt.infrastructure.ubuntu_index import refresh_ubuntu_indexes
from edgeapt.infrastructure.ubuntu_index import UbuntuIndexRefreshEvent
from edgeapt.project import create_project
from edgeapt.workflows.generate import generate_repository
from edgeapt.workflows.repackage import (
    prune_packages,
    repackage_project,
    PruneResult,
    RepackageEvent,
)
from edgeapt.workflows.validate import validate_project

console = Console()
PROJECT = create_project(ROOT)


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
    result = validate_project(
        project=PROJECT,
        skip_ubuntu_conflicts=skip_ubuntu_conflicts,
    )
    plan = result.plan
    table = Table(title="EdgeAPT Publications")
    table.add_column("suite")
    table.add_column("component")
    table.add_column("package")
    table.add_column("version")
    table.add_column("arch")
    table.add_column("sources")
    for publication in plan.publications:
        table.add_row(
            publication.key.suite,
            publication.key.component,
            publication.key.package,
            publication.key.deb_version,
            publication.key.arch,
            ", ".join(item.source_id for item in publication.provenance),
        )
    console.print(table)
    console.print(
        f"[green]Validated {result.source_count} source(s), "
        f"{len(plan.builds)} build(s), "
        f"{len(plan.publications)} publication(s).[/green]"
    )


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


def repackage(
    prune: bool = False,
    dry_run: Annotated[bool, cyclopts.Parameter(alias="-n")] = False,
) -> None:
    """Run upstream repackaging and write packages/ plus lock.json."""
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        TextColumn("[progress.percentage]{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Waiting to start", total=None)

        def on_event(event: RepackageEvent) -> None:
            if event.kind == "sources_loaded":
                progress.update(
                    task_id,
                    total=event.build_count,
                    description=event.message,
                )
            elif event.kind == "artifact_done":
                progress.console.print(_format_repackage_done(event), soft_wrap=True)
                progress.update(
                    task_id,
                    description=_format_repackage_status(event),
                    advance=1,
                )
            elif event.kind in {
                "cache_hit",
                "cache_miss",
                "source_start",
                "fetch_start",
                "extract_start",
                "inspect_deb_start",
            }:
                progress.console.print(_format_repackage_log(event), soft_wrap=True)
                progress.update(task_id, description=_format_repackage_status(event))
            else:
                progress.update(task_id, description=event.message)

        result = repackage_project(on_event=on_event, project=PROJECT)
        progress.update(task_id, description="Repackaging complete")

    artifact_count = len(result.lock.artifacts)
    console.print(f"[green]Wrote {LOCK_PATH}[/green]")
    console.print(
        f"[green]Processed {result.source_count} source(s), "
        f"{artifact_count} artifact(s).[/green]"
    )
    if prune:
        _print_prune_result(
            prune_packages(
                result.lock,
                dry_run=dry_run,
                packages_dir=PROJECT.paths.packages_dir,
                root=PROJECT.paths.root,
            )
        )


def generate(profile: str = "test") -> None:
    """Generate signed APT repository output."""
    result = generate_repository(profile=profile, project=PROJECT)
    console.print(
        f"[green]Generated {result.profile} signed APT repository at "
        f"{result.output_dir}[/green]"
    )
    console.print(f"install page: {result.index_html}")
    console.print(f"signing key: {result.signing_key_fingerprint}")


def e2e(
    suite: str | None = None,
    source: str | None = None,
    package: str | None = None,
    jobs: int = 4,
    apt_cache: bool = True,
    clear_apt_cache: bool = False,
) -> None:
    """Run Docker apt install tests against the local test repo."""

    def on_event(event: E2EEvent) -> None:
        if event.kind == "group_start":
            console.print(
                "\n".join(
                    [
                        f"\n---- {event.suite} / {event.arch} ----",
                        _format_repackage_field("Image", event.image),
                    ]
                )
            )
        elif event.kind == "test_start":
            prefix = f"({event.suite}/{event.arch})"
            console.print(
                "\n".join(
                    [
                        f"{prefix} Package: {event.package} {event.version}",
                        f"{prefix} Command: {' '.join(event.command)}",
                    ]
                )
            )
        elif event.kind == "test_pass":
            console.print(
                f"({event.suite}/{event.arch}) Result: [green]pass[/green]"
            )
        elif event.kind == "test_skip":
            console.print(_format_repackage_field("Skip", event.message))

    result = run_e2e(
        suite=suite,
        source=source,
        package=package,
        jobs=jobs,
        apt_cache=apt_cache,
        clear_apt_cache=clear_apt_cache,
        on_event=on_event,
    )
    console.print(
        f"[green]E2E passed: {result.tested} test(s), "
        f"{result.groups} group(s), {result.skipped} skipped.[/green]"
    )


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
    if event.kind == "cache_hit":
        return _format_repackage_field("Cache", "hit")
    if event.kind == "cache_miss":
        return _format_repackage_field("Cache", event.message)
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


def _print_prune_result(result: PruneResult) -> None:
    console.print("\n[bold]Prune packages[/bold]")
    console.print(_format_repackage_field("Mode", "dry-run" if result.dry_run else "apply"))
    console.print(_format_repackage_field("Referenced", len(result.referenced)))
    console.print(_format_repackage_field("Orphans", len(result.orphans)))
    if not result.orphans:
        console.print("[green]Nothing to prune.[/green]")
        return

    heading = "Would delete" if result.dry_run else "Deleted"
    paths = result.orphans if result.dry_run else result.deleted
    console.print(f"\n{heading}:")
    for path in paths:
        console.print(_display_path(path))


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT.paths.root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


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
