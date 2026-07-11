# EdgeAPT

EdgeAPT builds a signed APT repository from declarative source files and deploys
the generated repository with Cloudflare Workers and Static Assets. Large debs are
split during generation and streamed through the Worker under their normal APT URL.

## Repository inputs

Package definitions live in `sources/*.yaml`. Every upstream input must include a
lowercase `sha256:<64 hex digits>` digest. `lock.json` is the reviewed publication
and output-integrity manifest.

The `packages/` directory is generated output. It is ignored by Git and may be
cached locally or by GitHub Actions, but a cold checkout must always be able to
rebuild it.

`dev-logs/` is an optional private submodule. It is not needed to build, test, or
deploy EdgeAPT.

## Toolchain

GitHub Actions uses Ubuntu 24.04 with Python 3.13.12, uv 0.11.23, dpkg-deb 1.22.6,
aptly 1.6.3, Node.js 24.14.0, pnpm 11.11.0, and Wrangler 4.110.0. Local development
should use compatible versions; `uv run guide` shows the supported workflow.

## Local workflow

Install dependencies:

```bash
cd tools
uv sync --frozen --all-groups

cd ../cloudflare
pnpm install --frozen-lockfile
```

Validate and intentionally update the lock after changing sources:

```bash
cd tools
uv run refresh-ubuntu-index
uv run validate
uv run repackage --mode update-lock
```

Build from the committed lock without changing it:

```bash
cd tools
uv run repackage --mode locked
uv run generate --profile test
uv run e2e
```

Production generation and deployment are explicit steps:

```bash
cd tools
uv run check-key --profile prod
uv run repackage --mode locked
uv run generate --profile prod
uv run deploy --dry-run
uv run deploy
```

## GitHub Actions

`CI` runs Python and Worker checks plus the full package installation matrix without
Git LFS or the private submodule. Its package cache is keyed by the canonical
repository plan and is only a performance optimization.

`Deploy` uses the GitHub Environment `production`. Automatic deployment on pushes
to `master` is enabled by setting the repository or environment variable
`PRODUCTION_DEPLOY_ENABLED=true`; it can always be invoked manually.

The production environment requires these secrets:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `EDGEAPT_GPG_PRIVATE_KEY`

It may define `EDGEAPT_BASE_URL`; the workflow defaults to
`https://edgeapt.gjm20.top`. The signing key fingerprint must match
`keys/prod/fingerprint.txt`. Cloudflare custom-domain routing remains managed in the
Dashboard.
