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
  build: {
    rollupOptions: {
      output: {
        // Маршруты уже разнесены по чанкам через lazy() в App.tsx. Здесь
        // отделяем две группы зависимостей, которые иначе размазались бы по
        // общему чанку:
        //   charts — recharts и d3 под ним, самая тяжёлая зависимость в
        //            проекте. Нужна только на экранах с графиками, поэтому
        //            не должна лежать в том же файле, что и роутер;
        //   vendor — react, роутер, react-query. Меняются раз в полгода, и
        //            отдельным файлом переживают выкладку нового кода в кэше
        //            браузера.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return;
          if (/[\\/]node_modules[\\/](recharts|d3-|victory-|internmap|delaunator|robust-predicates)/.test(id))
            return "charts";
          if (
            /[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler|@tanstack)[\\/]/.test(id)
          )
            return "vendor";
        },
      },
    },
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
