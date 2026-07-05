from __future__ import annotations

import shutil

import pytest

from edgeapt.constants import PUBLIC_DIR, TEST_PUBLIC_DIR
from edgeapt.errors import ValidationError
from edgeapt.keyring import ensure_test_key
from edgeapt.repo import generate_repo
from edgeapt.repackage import repackage_all
from edgeapt.util import run


@pytest.mark.integration
def test_generate_repo_writes_signed_metadata() -> None:
    ensure_test_key()
    repackage_all()
    if PUBLIC_DIR.exists():
        shutil.rmtree(PUBLIC_DIR)
    result = generate_repo(profile="test")
    assert result.output_dir == TEST_PUBLIC_DIR

    inrelease = TEST_PUBLIC_DIR / "dists" / "noble" / "InRelease"
    release = TEST_PUBLIC_DIR / "dists" / "noble" / "Release"
    release_gpg = TEST_PUBLIC_DIR / "dists" / "noble" / "Release.gpg"

    assert inrelease.exists()
    assert release.exists()
    assert release_gpg.exists()
    assert not PUBLIC_DIR.exists()
    run(["gpg", "--verify", inrelease])


def test_prod_requires_explicit_real_key() -> None:
    with pytest.raises(ValidationError):
        generate_repo(profile="prod")
