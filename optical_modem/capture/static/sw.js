/* Service worker: app shell only, versioned by the build.
 *
 * Two rules learned the hard way:
 *
 * 1. **The cache name must change on every deploy.** The BUILD placeholder
 *    below is replaced by the server (a hash of this release's code and static
 *    files — see `net.build_id()`), so a rebuild
 *    automatically invalidates the previous shell. With a hand-maintained
 *    version string, an installed PWA keeps serving last release's JS against
 *    this release's HTML — and since service workers only run in a secure
 *    context, that bug is invisible over plain http.
 *
 * 2. **Nothing is cache-first forever.** Navigations and scripts are fetched
 *    from the network first (with the cache as an offline fallback); everything
 *    else is stale-while-revalidate. The suite is useless offline — the shell is
 *    cached to make it installable and to survive a flaky phone signal, not to
 *    serve a frozen copy of the app.
 *
 * /api/** is never touched: jobs, transfers and frame images must always be live.
 */
const VERSION = "om-suite-__BUILD__";

const SHELL = [
  "/",
  "/login",
  "/wizard",
  "/projects",
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
  "/suite-assets/js/viz.js",
  "/suite-assets/js/suite.js",
  "/suite-assets/js/home.js",
  "/suite-assets/js/wizard.js",
  "/suite-assets/js/guide.js",
  "/suite-assets/js/dataset.js",
  "/suite-assets/js/train.js",
  "/suite-assets/js/evaluate.js",
  "/suite-assets/js/models.js",
  "/suite-assets/js/projects.js",
  "/suite-assets/js/s2c.js",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(VERSION);
    // add one by one: a single 404 must not abort the whole install
    await Promise.all(SHELL.map((url) =>
      cache.add(new Request(url, { cache: "reload" })).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("message", (event) => {
  if (event.data === "skip-waiting" || event.data?.type === "skip-waiting") {
    self.skipWaiting();
  }
  if (event.data?.type === "version") {
    event.source?.postMessage({ type: "version", version: VERSION });
  }
});

function isApi(url) {
  return url.pathname.startsWith("/api/") ||
         url.pathname === "/healthz" ||
         url.pathname === "/sw.js";
}

async function networkFirst(request, fallbackKey) {
  try {
    const res = await fetch(request);
    if (res && res.ok) {
      const cache = await caches.open(VERSION);
      cache.put(request, res.clone()).catch(() => {});
    }
    return res;
  } catch (err) {
    const hit = await caches.match(request);
    if (hit) return hit;
    if (fallbackKey) {
      const shell = await caches.match(fallbackKey);
      if (shell) return shell;
    }
    throw err;
  }
}

async function staleWhileRevalidate(request) {
  const hit = await caches.match(request);
  const update = fetch(request).then((res) => {
    if (res && res.ok) {
      caches.open(VERSION).then((c) => c.put(request, res.clone())).catch(() => {});
    }
    return res;
  }).catch(() => null);
  return hit || (await update) || Response.error();
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (isApi(url) || url.origin !== location.origin) return;
  if (url.pathname.endsWith(".png") &&
      (url.pathname.includes("/frame") || url.pathname.includes("/shots/"))) return;

  const isShellAsset = /\.(js|css)$/.test(url.pathname);

  if (req.mode === "navigate") {
    // pages: always ask the server; the cache is the offline fallback only
    event.respondWith(networkFirst(req, "/"));
    return;
  }

  if (isShellAsset) {
    // scripts and styles: network first, so a deploy lands on the next load
    event.respondWith(networkFirst(req));
    return;
  }

  event.respondWith(staleWhileRevalidate(req));
});
