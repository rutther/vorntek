import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  base: "./",
  publicDir: false,
  define: { __FLOW_CRM_BUILD__: "true" },
  plugins: [react()],
  build: {
    outDir: fileURLToPath(new URL("../console/static/console/flow-workspace/", import.meta.url)),
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      treeshake: false,
      input: fileURLToPath(new URL("./src/main.jsx", import.meta.url)),
      output: { entryFileNames: "app.js", chunkFileNames: "chunks/[name]-[hash].js", assetFileNames: (asset) => asset.names?.some((name) => name.endsWith(".css")) ? "app.css" : "assets/[name]-[hash][extname]" },
    },
  },
});
