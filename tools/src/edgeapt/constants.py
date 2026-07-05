from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES_DIR = ROOT / "sources"
PACKAGES_DIR = ROOT / "packages"
PUBLIC_DIR = ROOT / "public"
TMP_DIR = ROOT / "tmp"
TEST_PUBLIC_DIR = TMP_DIR / "public-test"
LOCK_PATH = ROOT / "lock.json"
KEYS_DIR = ROOT / "keys"

SUPPORTED_TEMPLATES = {
    "edgeapt.single_binary/v1",
    "edgeapt.deb_upstream/v1",
}
SUPPORTED_SUITES = {"jammy", "noble"}
SUPPORTED_ARCHES = {"amd64", "arm64"}
SUPPORTED_E2E_ARCHES = {"amd64"}

COMPONENT = "main"
LOCK_SCHEMA = "edgeapt.lock/v1"
