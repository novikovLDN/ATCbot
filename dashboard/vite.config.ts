import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

// In production the React build is mounted by FastAPI at /dashboard/
// (see app/api/__init__.py). The SPA must therefore be built with a
// base path that matches; otherwise asset URLs like /assets/foo.js
// 404 because the real path is /dashboard/assets/foo.js.
//
// package.json has `"type": "module"`, so this config runs as ESM —
// __dirname doesn't exist at runtime. fileURLToPath(new URL("./src",
// import.meta.url)) is the ESM equivalent of path.resolve(__dirname,
// "src") and works on Node 18+.
export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      // Dev: Vite serves SPA on 5173, FastAPI bot runs on 8080 locally.
      // Forward /dashboard/api/* and /dashboard/ws to it so dev feels
      // exactly like prod.
      "/dashboard/api": "http://localhost:8080",
      "/dashboard/ws": {
        target: "ws://localhost:8080",
        ws: true,
      },
    },
  },
});
