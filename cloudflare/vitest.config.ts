import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  buildPagesASSETSBinding,
  cloudflareTest,
} from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

const directory = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [
    cloudflareTest(async () => ({
      main: "./src/index.ts",
      compatibilityDate: "2026-07-11",
      compatibilityFlags: ["nodejs_compat"],
      miniflare: {
        serviceBindings: {
          ASSETS: await buildPagesASSETSBinding(
            path.join(directory, "test", "fixtures", "public"),
          ),
        },
      },
    })),
  ],
});
