import { parseByteRange, RangeNotSatisfiable, type ByteRange } from "./range";

const SIDECAR_SCHEMA = "edgeapt.chunked-asset/v1";
const SIDECAR_SUFFIX = ".edgeapt-chunks.json";
const SHA256_PATTERN = /^sha256:[0-9a-f]{64}$/;
const MAX_CHUNKS = 32;

interface ChunkFact {
  path: string;
  offset: number;
  size: number;
  sha256: string;
}

interface ChunkedAssetManifest {
  schema: string;
  path: string;
  size: number;
  sha256: string;
  content_type: string;
  chunks: ChunkFact[];
}

interface Segment {
  chunk: ChunkFact;
  start: number;
  end: number;
}

interface FetchedSegment {
  response: Response;
  skip: number;
  length: number;
}

export async function handleChunkedAsset(
  request: Request,
  assets: Fetcher,
): Promise<Response | null> {
  const url = new URL(request.url);
  if (!url.pathname.endsWith(".deb")) return null;
  if (!new Set(["GET", "HEAD"]).has(request.method)) {
    return new Response("Method Not Allowed", {
      status: 405,
      headers: { Allow: "GET, HEAD" },
    });
  }

  const sidecarUrl = new URL(request.url);
  sidecarUrl.pathname = `${url.pathname}${SIDECAR_SUFFIX}`;
  sidecarUrl.search = "";
  const sidecarResponse = await assets.fetch(sidecarUrl);
  if (sidecarResponse.status === 404) return null;
  if (!sidecarResponse.ok) {
    throw new Error(`sidecar request failed: ${sidecarResponse.status}`);
  }
  const manifest = validateManifest(await sidecarResponse.json(), url.pathname);
  const etag = `"${manifest.sha256.slice("sha256:".length)}"`;
  const headers = responseHeaders(manifest, etag);
  if (etagMatches(request.headers.get("If-None-Match"), etag)) {
    return new Response(null, { status: 304, headers });
  }

  let range: ByteRange | null = null;
  const rangeHeader = request.headers.get("Range");
  const ifRange = request.headers.get("If-Range");
  if (rangeHeader && (!ifRange || ifRange === etag)) {
    try {
      range = parseByteRange(rangeHeader, manifest.size);
    } catch (error) {
      if (!(error instanceof RangeNotSatisfiable)) throw error;
      headers.set("Content-Range", `bytes */${manifest.size}`);
      return new Response(null, { status: 416, headers });
    }
  }

  const selected = range ?? { start: 0, end: manifest.size - 1 };
  const contentLength = selected.end - selected.start + 1;
  headers.set("Content-Length", String(contentLength));
  if (range) {
    headers.set(
      "Content-Range",
      `bytes ${selected.start}-${selected.end}/${manifest.size}`,
    );
  }
  const status = range ? 206 : 200;
  if (request.method === "HEAD") {
    return new Response(null, { status, headers });
  }

  const segments = selectSegments(manifest.chunks, selected);
  const first = await fetchSegment(assets, request.url, segments[0]);
  const body = streamSegments(
    first,
    segments.slice(1),
    assets,
    request.url,
    url.pathname,
    contentLength,
  );
  return new Response(body, { status, headers });
}

export function validateManifest(
  value: unknown,
  expectedPath: string,
): ChunkedAssetManifest {
  if (!isRecord(value) || value.schema !== SIDECAR_SCHEMA) {
    throw new Error("unsupported chunked asset manifest schema");
  }
  const path = requiredString(value, "path");
  const sha256 = requiredString(value, "sha256");
  const contentType = requiredString(value, "content_type");
  const size = requiredInteger(value, "size");
  if (path !== expectedPath || size < 1 || !SHA256_PATTERN.test(sha256)) {
    throw new Error("invalid chunked asset identity");
  }
  if (!Array.isArray(value.chunks) || value.chunks.length < 1) {
    throw new Error("chunked asset manifest has no chunks");
  }
  if (value.chunks.length > MAX_CHUNKS) {
    throw new Error("chunked asset manifest exceeds chunk limit");
  }
  const chunkPrefix = `/__edgeapt/chunks/sha256/${sha256.slice(7)}/`;
  let expectedOffset = 0;
  const chunks = value.chunks.map((raw): ChunkFact => {
    if (!isRecord(raw)) throw new Error("invalid chunk entry");
    const chunk: ChunkFact = {
      path: requiredString(raw, "path"),
      offset: requiredInteger(raw, "offset"),
      size: requiredInteger(raw, "size"),
      sha256: requiredString(raw, "sha256"),
    };
    if (
      !chunk.path.startsWith(chunkPrefix)
      || !/^\d{4}\.part$/.test(chunk.path.slice(chunkPrefix.length))
      || chunk.offset !== expectedOffset
      || chunk.size < 1
      || !SHA256_PATTERN.test(chunk.sha256)
    ) {
      throw new Error("invalid chunk entry");
    }
    expectedOffset += chunk.size;
    return chunk;
  });
  if (expectedOffset !== size) throw new Error("chunk sizes do not match asset size");
  return {
    schema: SIDECAR_SCHEMA,
    path,
    size,
    sha256,
    content_type: contentType,
    chunks,
  };
}

function selectSegments(chunks: ChunkFact[], range: ByteRange): Segment[] {
  return chunks.flatMap((chunk) => {
    const chunkEnd = chunk.offset + chunk.size - 1;
    const start = Math.max(range.start, chunk.offset);
    const end = Math.min(range.end, chunkEnd);
    return start <= end ? [{ chunk, start, end }] : [];
  });
}

async function fetchSegment(
  assets: Fetcher,
  requestUrl: string,
  segment: Segment,
): Promise<FetchedSegment> {
  const relativeStart = segment.start - segment.chunk.offset;
  const relativeEnd = segment.end - segment.chunk.offset;
  const chunkUrl = new URL(segment.chunk.path, requestUrl);
  const partial = relativeStart !== 0 || relativeEnd !== segment.chunk.size - 1;
  const response = await assets.fetch(
    new Request(chunkUrl, {
      headers: partial
        ? { Range: `bytes=${relativeStart}-${relativeEnd}` }
        : undefined,
    }),
  );
  if (!response.ok || response.body === null) {
    throw new Error(`chunk request failed: ${segment.chunk.path} ${response.status}`);
  }
  return {
    response,
    skip: partial && response.status !== 206 ? relativeStart : 0,
    length: relativeEnd - relativeStart + 1,
  };
}

function streamSegments(
  first: FetchedSegment,
  remaining: Segment[],
  assets: Fetcher,
  requestUrl: string,
  artifactPath: string,
  contentLength: number,
): ReadableStream<Uint8Array> {
  const { readable, writable } = new FixedLengthStream(contentLength);
  void pumpSegments(
    first,
    remaining,
    assets,
    requestUrl,
    artifactPath,
    writable,
  );
  return readable;
}

async function pumpSegments(
  first: FetchedSegment,
  remaining: Segment[],
  assets: Fetcher,
  requestUrl: string,
  artifactPath: string,
  writable: WritableStream<Uint8Array>,
): Promise<void> {
  const writer = writable.getWriter();
  try {
    await pipeSegment(first, writer);
    for (const segment of remaining) {
      await pipeSegment(
        await fetchSegment(assets, requestUrl, segment),
        writer,
      );
    }
    await writer.close();
  } catch (error) {
    console.error(JSON.stringify({
      event: "chunked_asset_stream_error",
      path: artifactPath,
      error: error instanceof Error ? error.message : String(error),
    }));
    await writer.abort(error);
  }
}

async function pipeSegment(
  segment: FetchedSegment,
  writer: WritableStreamDefaultWriter<Uint8Array>,
): Promise<void> {
  const reader = segment.response.body!.getReader();
  let skip = segment.skip;
  let remaining = segment.length;
  try {
    while (remaining > 0) {
      const result = await reader.read();
      if (result.done) break;
      let content = result.value;
      if (skip >= content.byteLength) {
        skip -= content.byteLength;
        continue;
      }
      if (skip > 0) {
        content = content.slice(skip);
        skip = 0;
      }
      if (content.byteLength > remaining) content = content.slice(0, remaining);
      await writer.write(content);
      remaining -= content.byteLength;
    }
    if (remaining !== 0) throw new Error("chunk response ended before expected length");
  } finally {
    await reader.cancel();
  }
}

function responseHeaders(manifest: ChunkedAssetManifest, etag: string): Headers {
  return new Headers({
    "Accept-Ranges": "bytes",
    "Cache-Control": "public, max-age=31536000, immutable",
    "Content-Type": manifest.content_type,
    ETag: etag,
  });
}

function etagMatches(value: string | null, etag: string): boolean {
  if (!value) return false;
  return value.split(",").some((candidate) => {
    const normalized = candidate.trim();
    return normalized === "*" || normalized === etag || normalized === `W/${etag}`;
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: Record<string, unknown>, field: string): string {
  const item = value[field];
  if (typeof item !== "string" || item === "") throw new Error(`invalid ${field}`);
  return item;
}

function requiredInteger(value: Record<string, unknown>, field: string): number {
  const item = value[field];
  if (!Number.isSafeInteger(item)) throw new Error(`invalid ${field}`);
  return item as number;
}
