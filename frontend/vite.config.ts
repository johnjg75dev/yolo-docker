import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

const outDir = new URL("../optical_modem/suite/static", import.meta.url).pathname;

export default defineConfig({
  root: ".",
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg"],
      manifest: {
        name: "Screen2Camera Optical Modem Suite",
        short_name: "S2C Suite",
        description:
          "Software-defined screen-to-camera optical modem studio: dataset synthesis, YOLO26-pose training, benchmark verification, and live optical data transfer.",
        theme_color: "#090c10",
        background_color: "#090c10",
        display: "standalone",
        orientation: "any",
        scope: "/",
        start_url: "/",
        icons: [
          {
            src: "/static/icons/icon-192.png",
            sizes: "192x192",
            type: "image/png",
          },
          {
            src: "/static/icons/icon-512.png",
            sizes: "512x512",
            type: "image/png",
          },
          {
            src: "/static/icons/icon-maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      workbox: {
        skipWaiting: true,
        clientsClaim: true,
        cleanupOutdatedCaches: true,
        globPatterns: ["**/*.{js,css,html,svg,png,webmanifest,ico}"],
        navigateFallback: "/suite-assets/index.html",
        navigateFallbackDenylist: [
          /^\/api\//,
          /^\/display/,
          /^\/camera/,
          /^\/static\//,
          /^\/healthz/,
          /^\/version/,
          /^\/suite-assets\/api\//,
        ],
        runtimeCaching: [
          {
            urlPattern: /^\/api\//,
            handler: "NetworkOnly",
          },
        ],
      },
    }),
  ],
  build: {
    outDir,
    emptyOutDir: true,
    sourcemap: false,
  },
  base: "/suite-assets/",
  server: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts: true,
    proxy: {
      "/api": "http://127.0.0.1:8889",
      "/static": "http://127.0.0.1:8889",
      "/display": "http://127.0.0.1:8889",
      "/camera": "http://127.0.0.1:8889",
      "/healthz": "http://127.0.0.1:8889",
      "/version": "http://127.0.0.1:8889",
    },
  },
});
