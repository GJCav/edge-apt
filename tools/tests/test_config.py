from __future__ import annotations

from pathlib import Path

import pytest

from edgeapt.config import load_config
from edgeapt.config import normalize_ubuntu_mirror_url
from edgeapt.constants import DEFAULT_UBUNTU_ARCHIVE_BASE_URL
from edgeapt.errors import ValidationError


def test_load_config_uses_default_when_file_is_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.toml")

    assert config.ubuntu_mirror_url == DEFAULT_UBUNTU_ARCHIVE_BASE_URL


def test_load_config_normalizes_trailing_slash(tmp_path: Path) -> None:
    path = tmp_path / "edgeapt.toml"
    path.write_text(
        'ubuntu_mirror_url = "https://mirrors.tuna.tsinghua.edu.cn/ubuntu/"\n',
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.ubuntu_mirror_url == "https://mirrors.tuna.tsinghua.edu.cn/ubuntu"


def test_load_config_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "edgeapt.toml"
    path.write_text(
        'ubuntu_mirror_url = "https://example.test/ubuntu"\nunknown = true\n',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_config(path)


def test_load_config_rejects_non_string_mirror(tmp_path: Path) -> None:
    path = tmp_path / "edgeapt.toml"
    path.write_text("ubuntu_mirror_url = 123\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="Input should be a valid string"):
        load_config(path)


def test_load_config_rejects_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "edgeapt.toml"
    path.write_text("ubuntu_mirror_url = [\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="invalid config file"):
        load_config(path)


@pytest.mark.parametrize("url", ["", "ftp://example.test/ubuntu"])
def test_normalize_ubuntu_mirror_url_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_ubuntu_mirror_url(url)
