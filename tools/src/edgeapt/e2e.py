from __future__ import annotations

import signal
import socket
import subprocess
import time

from edgeapt.constants import ROOT, TEST_PUBLIC_DIR
from edgeapt.errors import CommandError
from edgeapt.keyring import profile_public_keyring
from edgeapt.util import require_executable, run


def run_e2e(*, suite: str, image: str, package: str, command: str) -> None:
    require_executable("docker")
    test_keyring = profile_public_keyring("test")
    port = _free_port()
    server = subprocess.Popen(
        [
            "python",
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
    try:
        time.sleep(1)
        if server.poll() is not None:
            raise CommandError("local HTTP server failed to start")
        script = f"""
set -eux
apt-get update
apt-get install -y ca-certificates
install -d -m 0755 /etc/apt/keyrings
cp /edgeapt-key.gpg /etc/apt/keyrings/edgeapt-test-archive-keyring.gpg
echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/edgeapt-test-archive-keyring.gpg] http://127.0.0.1:{port} {suite} main' > /etc/apt/sources.list.d/edgeapt.list
apt-get update
apt-get install -y {package}
{command}
"""
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "host",
                "-v",
                f"{test_keyring.resolve()}:/edgeapt-key.gpg:ro",
                image,
                "bash",
                "-lc",
                script,
            ]
        )
    finally:
        _terminate(server)


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
