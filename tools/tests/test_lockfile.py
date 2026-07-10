from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.lockfile import load_lock
from edgeapt.lockfile import write_lock
from tests.factories import make_artifact
from tests.factories import make_deb_key
from tests.factories import make_lock
from tests.factories import make_publication


def test_lock_json_is_deterministic() -> None:
    key = make_deb_key(package="edgeapt-hello", deb_version="0.1.0-1")
    artifact = make_artifact(deb_key=key)
    publication = make_publication(
        deb_key=key,
        commands=(("edgeapt-hello",),),
    )
    lock = make_lock(artifacts=(artifact,), publications=(publication,))

    data = lock.to_json()

    assert data["artifacts"][0]["deb_key"] == {
        "package": "edgeapt-hello",
        "deb_version": "0.1.0-1",
        "arch": "amd64",
    }
    assert data["publications"][0]["e2e_commands"] == [["edgeapt-hello"]]


def test_lock_v2_round_trip(tmp_path: Path) -> None:
    key = make_deb_key(package="edgeapt-hello", deb_version="0.1.0-1")
    expected = make_lock(
        artifacts=(make_artifact(deb_key=key),),
        publications=(
            make_publication(deb_key=key, commands=(("edgeapt-hello",),)),
        ),
    )
    path = tmp_path / "lock.json"

    write_lock(expected, path)
    actual = load_lock(path)

    assert actual == expected


def test_lock_rejects_v1_schema(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    path.write_text('{"schema":"edgeapt.lock/v1"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="regenerate lock.json"):
        load_lock(path)
