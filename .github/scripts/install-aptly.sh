#!/usr/bin/env bash
set -euo pipefail

version=1.6.3
archive="aptly_${version}_linux_amd64.zip"
expected_sha256=535ed24eb706bc25a316b59d709a7e74e10165f2ffab8552097ba7e4816a3e7e
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

curl --fail --location --retry 3 \
  --output "$work_dir/$archive" \
  "https://github.com/aptly-dev/aptly/releases/download/v${version}/${archive}"
printf '%s  %s\n' "$expected_sha256" "$work_dir/$archive" | sha256sum --check
unzip -q "$work_dir/$archive" -d "$work_dir"
sudo install -m 0755 \
  "$work_dir/aptly_${version}_linux_amd64/aptly" \
  /usr/local/bin/aptly
aptly version | grep --fixed-strings "aptly version: $version"
