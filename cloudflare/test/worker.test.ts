import { env } from "cloudflare:test";
import { exports } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { validateManifest } from "../src/chunked_asset";

const URL = "https://example.test/pool/test.deb";
const CHUNK_URL =
  "https://example.test/__edgeapt/chunks/sha256/98fc47ca0a2753c9c4c5528dd22a63db7f4cae22df55df8494c2747848545aaa/0000.part";
const ETAG = '"98fc47ca0a2753c9c4c5528dd22a63db7f4cae22df55df8494c2747848545aaa"';

describe("chunked asset worker", () => {
  it("streams a complete artifact", async () => {
    const response = await exports.default.fetch(URL);

    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Length")).toBe("12");
    expect(response.headers.get("Accept-Ranges")).toBe("bytes");
    expect(response.headers.get("ETag")).toBe(ETAG);
    expect(await bodyText(response)).toBe("abcde\nfghij\n");
  });

  it("serves HEAD without reading the body", async () => {
    const response = await exports.default.fetch(new Request(URL, { method: "HEAD" }));

    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Length")).toBe("12");
    expect(await response.text()).toBe("");
  });

  it("serves a range crossing chunk boundaries", async () => {
    const response = await exports.default.fetch(new Request(URL, {
      headers: { Range: "bytes=4-8" },
    }));

    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Range")).toBe("bytes 4-8/12");
    expect(response.headers.get("Content-Length")).toBe("5");
    expect(await bodyText(response)).toBe("e\nfgh");
  });

  it("slices ranges when the static asset binding ignores Range", async () => {
    const assetResponse = await env.ASSETS.fetch(new Request(CHUNK_URL, {
      headers: { Range: "bytes=1-2" },
    }));
    expect(assetResponse.status).toBe(200);
    expect(await bodyText(assetResponse)).toBe("abcde\n");

    const response = await exports.default.fetch(new Request(URL, {
      headers: { Range: "bytes=1-2" },
    }));
    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Length")).toBe("2");
    expect(await bodyText(response)).toBe("bc");
  });

  it("serves suffix ranges", async () => {
    const response = await exports.default.fetch(new Request(URL, {
      headers: { Range: "bytes=-3" },
    }));

    expect(response.status).toBe(206);
    expect(await bodyText(response)).toBe("ij\n");
  });

  it("serves open-ended ranges and honors If-Range", async () => {
    const ranged = await exports.default.fetch(new Request(URL, {
      headers: { Range: "bytes=6-", "If-Range": ETAG },
    }));
    expect(ranged.status).toBe(206);
    expect(await bodyText(ranged)).toBe("fghij\n");

    const full = await exports.default.fetch(new Request(URL, {
      headers: { Range: "bytes=6-", "If-Range": '"outdated"' },
    }));
    expect(full.status).toBe(200);
    expect(await bodyText(full)).toBe("abcde\nfghij\n");
  });

  it("rejects invalid and multiple ranges", async () => {
    for (const range of ["bytes=99-100", "bytes=0-1,4-5"]) {
      const response = await exports.default.fetch(new Request(URL, {
        headers: { Range: range },
      }));
      expect(response.status).toBe(416);
      expect(response.headers.get("Content-Range")).toBe("bytes */12");
    }
  });

  it("honors conditional requests", async () => {
    const response = await exports.default.fetch(new Request(URL, {
      headers: { "If-None-Match": ETAG },
    }));

    expect(response.status).toBe(304);
  });

  it("falls back to static assets and normal 404 responses", async () => {
    const staticResponse = await exports.default.fetch("https://example.test/hello.txt");
    expect(staticResponse.status).toBe(200);
    expect(await staticResponse.text()).toBe("hello\n");

    const missingResponse = await exports.default.fetch(
      "https://example.test/pool/missing.deb",
    );
    expect(missingResponse.status).toBe(404);
  });

  it("rejects unsafe chunk paths and non-contiguous offsets", () => {
    expect(() => validateManifest({
      schema: "edgeapt.chunked-asset/v1",
      path: "/pool/test.deb",
      size: 1,
      sha256: `sha256:${"a".repeat(64)}`,
      content_type: "application/vnd.debian.binary-package",
      chunks: [{
        path: "/outside/0000.part",
        offset: 1,
        size: 1,
        sha256: `sha256:${"b".repeat(64)}`,
      }],
    }, "/pool/test.deb")).toThrow("invalid chunk entry");
  });
});

async function bodyText(response: Response): Promise<string> {
  return new TextDecoder().decode(await response.arrayBuffer());
}
