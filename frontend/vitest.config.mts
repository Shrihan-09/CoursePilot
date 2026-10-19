import path from "node:path";

import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    // .tsx: Phase 7.2 presenters are rendered to static markup (react-dom/
    // server) - what a student would read - without a DOM emulator.
    include: ["tests/unit/**/*.test.{ts,tsx}"],
  },
  // Vite 8 transforms with Oxc; tsconfig keeps jsx "preserve" for Next.js.
  oxc: { jsx: { runtime: "automatic" } },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
      "server-only": path.resolve(__dirname, "tests/unit/server-only-stub.ts"),
    },
  },
});
