// Service worker for the home-screen app. It keeps only the two app icons so the icon shows up fast and offline.
// It never touches anything else: pages, /api/ calls, uploads, videos and sign-in always go straight to the network.
const CACHE = "video-maker-shell-v1";
const SHELL = ["/assets/icon-192.png", "/assets/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()).catch(() => {}));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
      .catch(() => {})
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || !SHELL.includes(url.pathname)) return;   // not ours: do nothing
  event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
