# EdgeAPT Cloudflare Worker

This Worker serves the generated repository in `../public`. Small assets use
Cloudflare Static Assets directly. Requests for oversized deb paths fall
through to the Worker, which validates their generated sidecar and streams the
content-addressed chunks through the original URL.

The user-facing workflow is orchestrated from `../tools`:

```bash
cd ../tools
uv run generate --profile test
uv run e2e

uv run generate --profile prod
uv run deploy --dry-run
uv run deploy
```

`uv run e2e` starts and stops the test-profile Worker automatically. `uv run
deploy` publishes both the production Worker and the generated static assets;
it requires Wrangler authentication.

Direct pnpm commands are reserved for development inside this Worker project:

```bash
pnpm install --frozen-lockfile
pnpm check
```
