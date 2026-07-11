from __future__ import annotations

import subprocess
from pathlib import Path

import attrs

from edgeapt.constants import KEYS_DIR
from edgeapt.errors import CommandError, ValidationError
from edgeapt.util import run

TEST_KEY_UID = "EdgeAPT Test Archive Signing Key <edgeapt@example.invalid>"
VALID_KEY_PROFILES = frozenset({"test", "prod"})


@attrs.define(kw_only=True, frozen=True)
class SigningKey:
    profile: str
    fingerprint: str
    public_ascii: Path
    public_keyring: Path
    secret_ascii: Path


def ensure_test_key() -> SigningKey:
    secret_ascii = profile_secret_ascii("test")
    fingerprint = read_profile_fingerprint("test", required=False)
    if fingerprint is not None and not has_secret_key(fingerprint) and secret_ascii.exists():
        import_secret_key(secret_ascii)

    if fingerprint is not None and has_secret_key(fingerprint):
        export_profile_key_files(profile="test", fingerprint=fingerprint, export_secret=True)
        return load_signing_key("test")

    if secret_ascii.exists():
        import_secret_key(secret_ascii)

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

    export_profile_key_files(profile="test", fingerprint=fingerprint, export_secret=True)
    return load_signing_key("test")


def load_signing_key(profile: str) -> SigningKey:
    validate_key_profile(profile)
    if profile == "test" and not profile_fingerprint_path(profile).exists():
        return ensure_test_key()

    fingerprint = read_profile_fingerprint(profile, required=True)
    if fingerprint is None:
        raise ValidationError(f"keys/{profile}/fingerprint.txt does not exist")
    if profile == "prod" and fingerprint == read_profile_fingerprint("test", required=False):
        raise ValidationError("prod profile cannot use the test signing key")
    if not has_secret_key(fingerprint):
        raise ValidationError(
            f"missing GPG secret key for {profile} signing key {fingerprint}; "
            f"run: gpg --import {profile_secret_ascii(profile)}"
        )
    export_profile_key_files(
        profile=profile,
        fingerprint=fingerprint,
        export_secret=profile == "test",
    )
    return SigningKey(
        profile=profile,
        fingerprint=fingerprint,
        public_ascii=profile_public_ascii(profile),
        public_keyring=profile_public_keyring(profile),
        secret_ascii=profile_secret_ascii(profile),
    )


def check_signing_key(profile: str) -> SigningKey:
    key = load_signing_key(profile)
    if not key.public_ascii.exists():
        raise ValidationError(f"missing public key: {key.public_ascii}")
    if not key.public_keyring.exists():
        raise ValidationError(f"missing public keyring: {key.public_keyring}")
    if profile == "prod" and is_git_tracked(key.secret_ascii):
        raise ValidationError("prod secret key must not be tracked by git: keys/prod/sec.asc")
    return key


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


def export_profile_key_files(
    *,
    profile: str,
    fingerprint: str,
    export_secret: bool,
) -> None:
    validate_key_profile(profile)
    profile_key_dir(profile).mkdir(parents=True, exist_ok=True)
    ascii_result = run(["gpg", "--batch", "--armor", "--export", fingerprint])
    profile_public_ascii(profile).write_text(ascii_result.stdout, encoding="utf-8")
    binary = subprocess.run(
        ["gpg", "--batch", "--export", fingerprint],
        check=False,
        capture_output=True,
    )
    if binary.returncode != 0:
        raise CommandError(binary.stderr.decode("utf-8", errors="replace").strip())
    profile_public_keyring(profile).write_bytes(binary.stdout)
    profile_fingerprint_path(profile).write_text(f"{fingerprint}\n", encoding="utf-8")

    if export_secret:
        run(
            [
                "gpg",
                "--batch",
                "--yes",
                "--armor",
                "--output",
                str(profile_secret_ascii(profile)),
                "--export-secret-keys",
                fingerprint,
            ]
        )


def import_secret_key(path: Path) -> None:
    run(["gpg", "--batch", "--import", str(path)])


def has_secret_key(fingerprint: str) -> bool:
    result = subprocess.run(
        ["gpg", "--batch", "--list-secret-keys", "--with-colons", fingerprint],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    return any(line.startswith("sec:") for line in result.stdout.splitlines())


def read_profile_fingerprint(profile: str, *, required: bool) -> str | None:
    path = profile_fingerprint_path(profile)
    if not path.exists():
        if required:
            raise ValidationError(f"missing signing key fingerprint: {path}")
        return None
    fingerprint = path.read_text(encoding="utf-8").strip()
    if fingerprint == "":
        if required:
            raise ValidationError(f"empty signing key fingerprint: {path}")
        return None
    return fingerprint


def validate_key_profile(profile: str) -> None:
    if profile not in VALID_KEY_PROFILES:
        raise ValidationError("profile must be either test or prod")


def profile_key_dir(profile: str) -> Path:
    validate_key_profile(profile)
    return KEYS_DIR / profile


def profile_public_ascii(profile: str) -> Path:
    return profile_key_dir(profile) / "edgeapt.asc"


def profile_public_keyring(profile: str) -> Path:
    return profile_key_dir(profile) / "edgeapt.gpg"


def profile_fingerprint_path(profile: str) -> Path:
    return profile_key_dir(profile) / "fingerprint.txt"


def profile_secret_ascii(profile: str) -> Path:
    return profile_key_dir(profile) / "sec.asc"


def is_git_tracked(path: Path) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
