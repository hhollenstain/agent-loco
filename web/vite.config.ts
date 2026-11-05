import { resolve } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const outDir = process.env.LBW_WEB_OUT
  ? resolve(process.env.LBW_WEB_OUT)
  : resolve(__dirname, "../src/agent_lbw/static/app");

export default defineConfig({
  plugins: [react()],
  build: {
    outDir,
    emptyOutDir: true,
    sourcemap: false,
    cssCodeSplit: false,
    modulePreload: false,
    rollupOptions: {
      input: resolve(__dirname, "src/main.tsx"),
      output: {
        format: "iife",
        inlineDynamicImports: true,
        entryFileNames: "app.js",
        assetFileNames: "app[extname]",
      },
    },
  },
});
