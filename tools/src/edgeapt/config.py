from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError as PydanticValidationError
from pydantic import field_validator

from edgeapt.constants import DEFAULT_UBUNTU_ARCHIVE_BASE_URL, ROOT
from edgeapt.errors import ValidationError

CONFIG_PATH = ROOT / "edgeapt.toml"


class EdgeAptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ubuntu_mirror_url: str = DEFAULT_UBUNTU_ARCHIVE_BASE_URL

    @field_validator("ubuntu_mirror_url")
    @classmethod
    def validate_ubuntu_mirror_url(cls, value: str) -> str:
        return normalize_ubuntu_mirror_url(value)


def load_config(path: Path = CONFIG_PATH) -> EdgeAptConfig:
    if not path.exists():
        return EdgeAptConfig()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(f"invalid config file {path}: {exc}") from exc
    try:
        return EdgeAptConfig.model_validate(raw)
    except PydanticValidationError as exc:
        raise ValidationError(f"invalid config file {path}: {exc}") from exc


def normalize_ubuntu_mirror_url(url: str) -> str:
    normalized = url.strip().rstrip("/")
    if normalized == "":
        raise ValueError("ubuntu_mirror_url must be a non-empty string")
    if not (normalized.startswith("http://") or normalized.startswith("https://")):
        raise ValueError("ubuntu_mirror_url must start with http:// or https://")
    return normalized
