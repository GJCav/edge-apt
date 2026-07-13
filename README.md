# EdgeAPT

EdgeAPT is a personal, serverless Ubuntu repository hosted on Cloudflare Workers,
designed for ultra-fast global package delivery **completely FREE OF CHARGE**.

Browse available packages, get installation instructions, or download deb files
directly at **[edgeapt.gjm20.top](https://edgeapt.gjm20.top/)**.

## Implementation

EdgeAPT turns declarative package definitions into a signed APT repository:

```text
sources/*.yaml
      |
      v
validate and plan publications
      |
      v
download, verify, and repackage debs
      |
      v
lock.json + packages/
      |
      v
generate signed APT metadata and static assets
      |
      v
Cloudflare Workers + Static Assets
```

Package sources describe upstream releases, target Ubuntu suites and architectures,
packaging templates, metadata, and end-to-end checks. Every upstream input is
protected by a declared SHA256 digest.

Validation expands those source definitions into concrete package publications and
rejects conflicting plans. Repackaging then downloads verified upstream artifacts
and either adopts existing debs or builds new ones through the selected template.
`lock.json` records the reviewed build plan and expected artifact identities.

Generation uses Aptly and GnuPG to create the signed repository. Debs that exceed
Cloudflare's individual asset limit are split into static chunks; the Worker
reassembles them as a streaming response under the original APT URL. The package
explorer and installation guide are generated alongside the repository metadata.

`packages/` is disposable build output. It is ignored by Git and used only as a
local or GitHub Actions cache. A clean checkout can always rebuild it from the
source declarations and committed lock.

## Toolchain

| Tool | Role |
| --- | --- |
| Python and uv | Planner, validation, packaging, generation, and workflow entry points |
| dpkg-deb | Deterministic deb construction and inspection |
| Aptly | APT repository metadata generation |
| GnuPG | Repository signing |
| Docker | Installation tests across supported Ubuntu suites |
| Node.js and pnpm | Cloudflare Worker development and testing |
| Wrangler | Local Worker runtime and production deployment |
| Cloudflare Workers | Global delivery, Range requests, and large-deb streaming |

CI currently pins Ubuntu 24.04, Python 3.13.12, uv 0.11.23, dpkg-deb 1.22.6,
Aptly 1.6.3, Node.js 24.14.0, pnpm 11.11.0, and Wrangler 4.110.0.

## Local Development

Install the Python and Worker dependencies:

```bash
cd tools
uv sync --frozen --all-groups

cd ../cloudflare
pnpm install --frozen-lockfile
```

The project CLI is the canonical entry point for both test and production
workflows. Start with:

```bash
cd tools
uv run guide
```

The guide lists each stage in order, including key setup, Ubuntu index refresh,
validation, repackaging, repository generation, Worker startup, E2E testing, and
deployment.

After changing `sources/*.yaml`, intentionally update the lock with:

```bash
uv run refresh-ubuntu-index
uv run validate
uv run repackage --mode update-lock
```

For a small source change, use a scoped maintenance run. Planning and conflict
validation still cover the complete repository, while only the selected sources
are downloaded, packaged, published to the test repository, and installed by E2E:

```bash
uv run refresh-ubuntu-index
uv run validate
uv run repackage --mode update-lock --source lf
uv run generate --profile test --source lf
uv run e2e --source lf
```

Repeat `--source` to maintain multiple sources together. Scoped runs update the
single canonical `lock.json`; they never create or merge partial lock files.

Normal builds and CI use locked mode, which rebuilds missing artifacts and verifies
them without modifying `lock.json`:

```bash
uv run repackage --mode locked
```

## GitHub Actions

The `CI` workflow runs on pull requests and pushes to `master`. It performs Python
tests, Pyright checks, Worker typechecks and tests, a cold-capable locked build, and
the complete Docker installation matrix. Package artifacts are cached by operating
system, repository plan, and lock digest; fallback caches allow unchanged debs to
be reused after source changes.

The `Deploy` workflow can be started manually and can also deploy pushes to
`master` when the repository variable `PRODUCTION_DEPLOY_ENABLED=true`. It uses the
GitHub Environment `production`, which must provide these secrets:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`
- `EDGEAPT_GPG_PRIVATE_KEY`

The environment may define `EDGEAPT_BASE_URL`; it defaults to
`https://edgeapt.gjm20.top`. The signing key must match
`keys/prod/fingerprint.txt`. Custom-domain routing remains managed in the
Cloudflare Dashboard.

The `Check updates` workflow runs at 08:00 (UTC+8) on the first day of each month
and can also be started manually. It checks recognized GitHub Releases and
cargo-quickinstall artifacts, then creates a GitHub issue when it finds actionable
upstream updates or check errors. It infers repositories, release tags, target
assets, crates, and target triples from the existing upstream URLs, so source
templates do not need update-specific fields.

Run the same upstream check locally with:

```bash
cd tools
uv run check-updates
```
