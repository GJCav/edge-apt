from __future__ import annotations

import subprocess
from pathlib import Path

from edgeapt.errors import CommandError
from edgeapt.util import require_executable


def deploy_cloudflare(*, root: Path, dry_run: bool = False) -> None:
    """Deploy the generated repository and Worker through the Cloudflare project."""
    pnpm = require_executable("pnpm")
    script = "deploy:dry" if dry_run else "deploy"
    result = subprocess.run(
        [pnpm, "run", script],
        cwd=root / "cloudflare",
        check=False,
    )
    if result.returncode != 0:
        raise CommandError(f"Cloudflare {script} failed with exit code {result.returncode}")
