from __future__ import annotations

from cyclopts import App
from rich.console import Console
from rich.table import Table

from edgeapt.constants import LOCK_PATH, SOURCES_DIR
from edgeapt.e2e import run_e2e
from edgeapt.errors import EdgeAptError
from edgeapt.keyring import ensure_test_key
from edgeapt.repackage import repackage_all
from edgeapt.repo import generate_repo
from edgeapt.sources import load_sources

app = App(name="edgeapt")
console = Console()


@app.command
def validate() -> None:
    """Validate sources/*.yaml."""
    sources = load_sources(SOURCES_DIR)
    table = Table(title="EdgeAPT Sources")
    table.add_column("id")
    table.add_column("template")
    table.add_column("package")
    table.add_column("upstreams", justify="right")
    for source in sources:
        table.add_row(source.id, source.template, source.package, str(len(source.upstream)))
    console.print(table)
    console.print(f"[green]Validated {len(sources)} source(s).[/green]")


@app.command
def init_test_key() -> None:
    """Create or export the local test archive signing key."""
    key = ensure_test_key()
    console.print("[green]Test signing key ready.[/green]")
    console.print(f"fingerprint: {key.fingerprint}")
    console.print(f"public keyring: {key.public_keyring}")
    console.print(f"public ascii: {key.public_ascii}")


@app.command
def repackage() -> None:
    """Run upstream repackaging and write packages/ plus lock.json."""
    lock = repackage_all()
    console.print(f"[green]Wrote {LOCK_PATH}[/green]")
    console.print(f"[green]Processed {len(lock.sources)} source(s).[/green]")


@app.command
def generate(
    profile: str = "test",
    signing_key_fingerprint: str | None = None,
) -> None:
    """Generate signed APT repository output."""
    result = generate_repo(
        profile=profile,
        signing_key_fingerprint=signing_key_fingerprint,
    )
    console.print(
        f"[green]Generated {result.profile} signed APT repository at "
        f"{result.output_dir}[/green]"
    )
    console.print(f"signing key: {result.signing_key_fingerprint}")


@app.command
def sync(
    profile: str = "test",
    signing_key_fingerprint: str | None = None,
) -> None:
    """Run repackage and generate in sequence."""
    repackage()
    generate(profile=profile, signing_key_fingerprint=signing_key_fingerprint)


@app.command
def e2e(
    suite: str = "noble",
    image: str = "ubuntu:24.04",
    package: str = "edgeapt-hello",
    command: str = "edgeapt-hello",
) -> None:
    """Run a Docker apt install smoke test against the local test repo."""
    run_e2e(suite=suite, image=image, package=package, command=command)
    console.print("[green]E2E smoke test passed.[/green]")


def main() -> None:
    try:
        app()
    except EdgeAptError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
