from __future__ import annotations

import subprocess
from pathlib import Path

import attrs

from edgeapt.constants import KEYS_DIR
from edgeapt.errors import CommandError
from edgeapt.util import run

TEST_KEY_UID = "EdgeAPT Test Archive Signing Key <edgeapt@example.invalid>"
TEST_KEY_ASC = KEYS_DIR / "test_edgeapt_archive_keyring.asc"
TEST_KEY_GPG = KEYS_DIR / "test_edgeapt_archive_keyring.gpg"
TEST_KEY_FINGERPRINT = KEYS_DIR / "test_edgeapt_fingerprint.txt"


@attrs.define(kw_only=True, frozen=True)
class SigningKey:
    fingerprint: str
    public_ascii: Path
    public_keyring: Path


def ensure_test_key() -> SigningKey:
    fingerprint = find_test_key()
    if fingerprint is None:
        run(
            [
                "gpg",
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-gen-key",
                TEST_KEY_UID,
                "rsa4096",
                "sign",
                "2y",
            ]
        )
        fingerprint = find_test_key()
        if fingerprint is None:
            raise CommandError("failed to create test signing key")

    export_public_key(fingerprint)
    return SigningKey(
        fingerprint=fingerprint,
        public_ascii=TEST_KEY_ASC,
        public_keyring=TEST_KEY_GPG,
    )


def load_test_signing_key() -> SigningKey:
    if not TEST_KEY_FINGERPRINT.exists():
        return ensure_test_key()
    fingerprint = TEST_KEY_FINGERPRINT.read_text(encoding="utf-8").strip()
    if fingerprint == "":
        return ensure_test_key()
    export_public_key(fingerprint)
    return SigningKey(
        fingerprint=fingerprint,
        public_ascii=TEST_KEY_ASC,
        public_keyring=TEST_KEY_GPG,
    )


def is_test_key_fingerprint(fingerprint: str) -> bool:
    if not TEST_KEY_FINGERPRINT.exists():
        return False
    return TEST_KEY_FINGERPRINT.read_text(encoding="utf-8").strip() == fingerprint


def find_test_key() -> str | None:
    result = subprocess.run(
        ["gpg", "--batch", "--list-secret-keys", "--with-colons", TEST_KEY_UID],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    current_is_secret = False
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if not parts:
            continue
        if parts[0] == "sec":
            current_is_secret = True
        elif parts[0] == "fpr" and current_is_secret and len(parts) > 9:
            return parts[9]
    return None


def export_public_key(fingerprint: str) -> None:
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    ascii_result = run(["gpg", "--batch", "--armor", "--export", fingerprint])
    TEST_KEY_ASC.write_text(ascii_result.stdout, encoding="utf-8")
    binary = subprocess.run(
        ["gpg", "--batch", "--export", fingerprint],
        check=False,
        capture_output=True,
    )
    if binary.returncode != 0:
        raise CommandError(binary.stderr.decode("utf-8", errors="replace").strip())
    TEST_KEY_GPG.write_bytes(binary.stdout)
    TEST_KEY_FINGERPRINT.write_text(f"{fingerprint}\n", encoding="utf-8")
