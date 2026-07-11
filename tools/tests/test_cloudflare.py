from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from edgeapt.errors import CommandError
from edgeapt.infrastructure.cloudflare import deploy_cloudflare


def _pnpm_path(name: str) -> Path:
    assert name == "pnpm"
    return Path("/usr/bin/pnpm")


def test_deploy_cloudflare_runs_pnpm_from_cloudflare_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[Path | str], Path]] = []
    pnpm = Path("/usr/bin/pnpm")
    monkeypatch.setattr("edgeapt.infrastructure.cloudflare.require_executable", _pnpm_path)

    def run(
        args: list[Path | str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        calls.append((args, cwd))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("edgeapt.infrastructure.cloudflare.subprocess.run", run)

    deploy_cloudflare(root=tmp_path)

    assert calls == [([pnpm, "run", "deploy"], tmp_path / "cloudflare")]


def test_deploy_cloudflare_supports_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("edgeapt.infrastructure.cloudflare.require_executable", _pnpm_path)
    calls: list[list[Path | str]] = []

    def run(
        args: list[Path | str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("edgeapt.infrastructure.cloudflare.subprocess.run", run)

    deploy_cloudflare(root=tmp_path, dry_run=True)

    assert calls == [[Path("/usr/bin/pnpm"), "run", "deploy:dry"]]


def test_deploy_cloudflare_reports_failed_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("edgeapt.infrastructure.cloudflare.require_executable", _pnpm_path)

    def run(
        args: list[Path | str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(
        "edgeapt.infrastructure.cloudflare.subprocess.run",
        run,
    )

    with pytest.raises(CommandError, match="Cloudflare deploy failed with exit code 1"):
        deploy_cloudflare(root=tmp_path)
