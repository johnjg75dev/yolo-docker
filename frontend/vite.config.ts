import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  root: ".",
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",

      includeAssets: ["favicon.svg", "icon-192.png", "icon-512.png"],
      manifest: {
        name: "Screen2Camera Optical Modem",
        short_name: "S2C Modem",
        description: "Encode, decode, and train screen-to-camera optical modem payloads.",
        theme_color: "#0b0d10",
        background_color: "#0b0d10",
        display: "standalone",
        orientation: "portrait",
        scope: "/suite-assets/",
        start_url: "/suite-assets/",
        icons: [
          {
            name: "icon-192",
            src: "/suite-assets/icon-192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            name: "icon-512",
            src: "/suite-assets/icon-512.png",
            sizes: "512x512",
            type: "image/png",
          },
        ],
      },
      workbox: {
        skipWaiting: true,
        clientsClaim: true,
        cleanupOutdatedCaches: true,
        globPatterns: ["**/*.{js,css,html,svg,png,webmanifest,ico}"],
        navigateFallback: "/suite-assets/index.html",
        navigateFallbackDenylist: [/^\/api\//, /^\/capture/, /^\/suite-assets\/api\//],
        runtimeCaching: [
          {
            urlPattern: /^\/api\//,
            handler: "NetworkFirst",
            options: {
              cacheName: "api-cache",
              expiration: { maxEntries: 50, maxAgeSeconds: 60 },
            },
          },
        ],
      },
    }),
  ],
  build: {
    outDir: "/workspace/yolo-docker/optical_modem/suite/static",
    emptyOutDir: false,
    sourcemap: true,
  },
  base: "/suite-assets/",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
      "/capture": "http://localhost:8080",
    },
  },
});