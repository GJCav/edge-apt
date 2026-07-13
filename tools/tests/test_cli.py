from __future__ import annotations

from io import StringIO

from rich.console import Console

from edgeapt import cli


def test_guide_uses_tools_as_the_single_workflow_entrypoint() -> None:
    output = StringIO()
    original_console = cli.console
    cli.console = Console(file=output, width=120, color_system=None)
    try:
        cli.guide()
    finally:
        cli.console = original_console

    rendered = output.getvalue()
    assert "uv run e2e" in rendered
    assert "uv run deploy" in rendered
    assert "Shared" not in rendered
    assert "Runtime" not in rendered
    assert "../cloudflare" not in rendered
    assert "pnpm" not in rendered
    assert rendered.count("uv run refresh-ubuntu-index") == 2
    assert rendered.count("uv run validate") == 2
    assert rendered.count("uv run repackage") == 3
    assert "uv run generate --profile test --source <id>" in rendered
    assert "uv run e2e --source <id>" in rendered
