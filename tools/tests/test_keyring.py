from __future__ import annotations

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
