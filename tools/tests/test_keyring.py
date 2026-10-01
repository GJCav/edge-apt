from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from edgeapt.errors import ValidationError
from edgeapt.infrastructure import signing


def test_profile_key_paths_are_profile_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(signing, "KEYS_DIR", tmp_path)

    assert signing.profile_public_ascii("test") == tmp_path / "test" / "edgeapt.asc"
    assert signing.profile_public_keyring("test") == tmp_path / "test" / "edgeapt.gpg"
    assert signing.profile_fingerprint_path("test") == tmp_path / "test" / "fingerprint.txt"
    assert signing.profile_secret_ascii("test") == tmp_path / "test" / "sec.asc"


def test_prod_signing_key_requires_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(signing, "KEYS_DIR", tmp_path)

    with pytest.raises(ValidationError, match="missing signing key fingerprint"):
        signing.load_signing_key("prod")


def test_prod_profile_cannot_use_test_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(signing, "KEYS_DIR", tmp_path)
    (tmp_path / "test").mkdir()
    (tmp_path / "prod").mkdir()
    (tmp_path / "test" / "fingerprint.txt").write_text("ABC123\n", encoding="utf-8")
    (tmp_path / "prod" / "fingerprint.txt").write_text("ABC123\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="cannot use the test signing key"):
        signing.load_signing_key("prod")


def test_invalid_key_profile_is_rejected() -> None:
    with pytest.raises(ValidationError, match="profile must be either test or prod"):
        signing.profile_key_dir("staging")


@pytest.mark.parametrize("exports_exist", [False, True])
def test_ready_test_key_exports_once_and_refreshes_key_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exports_exist: bool,
) -> None:
    monkeypatch.setattr(signing, "KEYS_DIR", tmp_path)
    fingerprint = "A" * 40
    fingerprint_path = signing.profile_fingerprint_path("test")
    fingerprint_path.parent.mkdir(parents=True)
    fingerprint_path.write_text(f"{fingerprint}\n", encoding="utf-8")
    if exports_exist:
        for path in (
            signing.profile_public_ascii("test"),
            signing.profile_public_keyring("test"),
            signing.profile_secret_ascii("test"),
        ):
            path.write_bytes(b"old export")

    def has_secret_key(value: str) -> bool:
        assert value == fingerprint
        return True

    monkeypatch.setattr(signing, "has_secret_key", has_secret_key)
    calls = _mock_key_exports(monkeypatch, fingerprint=fingerprint)

    key = signing.ensure_test_key()

    assert key == signing.SigningKey(
        profile="test",
        fingerprint=fingerprint,
        public_ascii=tmp_path / "test" / "edgeapt.asc",
        public_keyring=tmp_path / "test" / "edgeapt.gpg",
        secret_ascii=tmp_path / "test" / "sec.asc",
    )
    assert len(calls) == 3
    assert key.public_ascii.read_text(encoding="utf-8") == "mock public ASCII\n"
    assert key.public_keyring.read_bytes() == b"mock public keyring"
    assert key.secret_ascii.read_text(encoding="utf-8") == "mock secret ASCII\n"
    assert fingerprint_path.read_text(encoding="utf-8") == f"{fingerprint}\n"


def test_load_test_key_seeds_missing_fingerprint_before_reload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(signing, "KEYS_DIR", tmp_path)
    fingerprint = "B" * 40
    finds: list[str] = []

    def find_test_key() -> str:
        assert not signing.profile_fingerprint_path("test").exists()
        assert not finds
        finds.append(fingerprint)
        return fingerprint

    def has_secret_key(value: str) -> bool:
        assert value == fingerprint
        assert signing.read_profile_fingerprint("test", required=True) == fingerprint
        return True

    monkeypatch.setattr(signing, "find_test_key", find_test_key)
    monkeypatch.setattr(signing, "has_secret_key", has_secret_key)
    calls = _mock_key_exports(monkeypatch, fingerprint=fingerprint)

    key = signing.load_signing_key("test")

    assert key.fingerprint == fingerprint
    assert finds == [fingerprint]
    assert len(calls) == 6
    assert calls[:3] == calls[3:]
    assert signing.profile_fingerprint_path("test").read_text(
        encoding="utf-8"
    ) == f"{fingerprint}\n"


def test_test_key_imports_missing_local_secret_before_one_export(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(signing, "KEYS_DIR", tmp_path)
    fingerprint = "C" * 40
    fingerprint_path = signing.profile_fingerprint_path("test")
    fingerprint_path.parent.mkdir(parents=True)
    fingerprint_path.write_text(f"{fingerprint}\n", encoding="utf-8")
    secret_ascii = signing.profile_secret_ascii("test")
    secret_ascii.write_text("stored secret\n", encoding="utf-8")
    imports: list[Path] = []

    def has_secret_key(value: str) -> bool:
        assert value == fingerprint
        return bool(imports)

    def import_secret_key(path: Path) -> None:
        assert path == secret_ascii
        assert path.read_text(encoding="utf-8") == "stored secret\n"
        imports.append(path)

    monkeypatch.setattr(signing, "has_secret_key", has_secret_key)
    monkeypatch.setattr(signing, "import_secret_key", import_secret_key)
    calls = _mock_key_exports(monkeypatch, fingerprint=fingerprint)

    key = signing.ensure_test_key()

    assert key.fingerprint == fingerprint
    assert imports == [secret_ascii]
    assert len(calls) == 3
    assert secret_ascii.read_text(encoding="utf-8") == "mock secret ASCII\n"


def _mock_key_exports(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fingerprint: str,
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(args))
        if args == ["gpg", "--batch", "--armor", "--export", fingerprint]:
            return subprocess.CompletedProcess(
                args, 0, stdout="mock public ASCII\n", stderr=""
            )
        secret_ascii = signing.profile_secret_ascii("test")
        assert args == [
            "gpg",
            "--batch",
            "--yes",
            "--armor",
            "--output",
            str(secret_ascii),
            "--export-secret-keys",
            fingerprint,
        ]
        secret_ascii.write_text("mock secret ASCII\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def export_binary(
        args: list[str],
        *,
        check: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        assert args == ["gpg", "--batch", "--export", fingerprint]
        assert check is False
        assert capture_output is True
        calls.append(tuple(args))
        return subprocess.CompletedProcess(
            args, 0, stdout=b"mock public keyring", stderr=b""
        )

    monkeypatch.setattr(signing, "run", run)
    monkeypatch.setattr(signing.subprocess, "run", export_binary)
    return calls
