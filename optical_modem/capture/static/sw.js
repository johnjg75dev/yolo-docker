/* Service worker: app shell only.
 *
 * The suite is useless offline (datasets, jobs and transfers are server-side),
 * so this caches the *shell* to make the app installable and instant to open on
 * a phone with a poor signal — and never touches /api/, which must always come
 * from the network.
 */
const VERSION = "om-suite-v4";
const SHELL = [
  "/",
  "/login",
  "/guide",
  "/dataset",
  "/train",
  "/evaluate",
  "/models",
  "/s2c",
  "/display",
  "/camera",
  "/manifest.webmanifest",
  "/static/css/app.css",
  "/static/js/common.js",
  "/static/js/index.js",
  "/static/js/display.js",
  "/static/js/camera.js",
  "/suite-assets/css/suite.css",
  "/suite-assets/js/suite.js",
  "/suite-assets/js/home.js",
  "/suite-assets/js/guide.js",
  "/suite-assets/js/dataset.js",
  "/suite-assets/js/train.js",
  "/suite-assets/js/evaluate.js",
  "/suite-assets/js/models.js",
  "/suite-assets/js/s2c.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  // never cache the API, the SSE stream, or anything cross-origin
  if (url.pathname.startsWith("/api/") || url.origin !== location.origin) return;
  // frames and photos are big and immutable: let the HTTP cache do the work
  if (url.pathname.endsWith(".png") &&
      (url.pathname.includes("/frame") || url.pathname.includes("/shots/"))) return;

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => caches.match(req).then((hit) => hit || caches.match("/")))
    );
    return;
  }

  event.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => {});
      }
      return res;
    }))
  );
});
