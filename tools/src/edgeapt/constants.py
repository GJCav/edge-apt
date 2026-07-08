from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES_DIR = ROOT / "sources"
PACKAGES_DIR = ROOT / "packages"
PUBLIC_DIR = ROOT / "public"
TMP_DIR = ROOT / "tmp"
TEST_PUBLIC_DIR = TMP_DIR / "public-test"
UBUNTU_INDEX_DIR = TMP_DIR / "ubuntu-index"
LOCK_PATH = ROOT / "lock.json"
KEYS_DIR = ROOT / "keys"

SUPPORTED_TEMPLATES = {
    "edgeapt.single_binary/v1",
    "edgeapt.deb_upstream/v1",
}
SUPPORTED_SUITES = {"jammy", "noble"}
SUPPORTED_ARCHES = {"amd64", "arm64"}
SUPPORTED_E2E_ARCHES = {"amd64"}
UBUNTU_INDEX_ARCHES = ("amd64",)
UBUNTU_COMPONENTS = ("main", "restricted", "universe", "multiverse")
DEFAULT_UBUNTU_ARCHIVE_BASE_URL = "http://archive.ubuntu.com/ubuntu"

COMPONENT = "main"
LOCK_SCHEMA = "edgeapt.lock/v1"
STATIC_ASSET_SIZE_LIMIT_BYTES = 25 * 1024 * 1024
