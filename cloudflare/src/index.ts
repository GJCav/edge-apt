import { handleChunkedAsset } from "./chunked_asset";

export default {
  async fetch(request, env): Promise<Response> {
    try {
      const response = await handleChunkedAsset(request, env.ASSETS);
      return response ?? env.ASSETS.fetch(request);
    } catch (error) {
      console.error(JSON.stringify({
        event: "chunked_asset_request_error",
        path: new URL(request.url).pathname,
        error: error instanceof Error ? error.message : String(error),
      }));
      return new Response("Chunked asset unavailable", { status: 502 });
    }
  },
} satisfies ExportedHandler<Env>;
