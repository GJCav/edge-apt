#!/usr/bin/env bash
set -euo pipefail

python --version | grep --fixed-strings "Python 3.13.12"
uv --version | grep --fixed-strings "uv 0.11.23"
dpkg-deb --version | head -n 1 | grep --fixed-strings "version 1.22.6"
aptly version | grep --fixed-strings "aptly version: 1.6.3"
node --version | grep --fixed-strings "v24.14.0"
pnpm --version | grep --fixed-strings "11.11.0"
(
  cd cloudflare
  pnpm exec wrangler --version | grep --fixed-strings "4.110.0"
)
