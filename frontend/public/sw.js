/*
 * CoursePilot service worker (UI polish B4): installable app + offline page.
 *
 * Privacy: it caches ONLY the static offline page and brand icons. Pages and
 * /api responses (student data) are never stored; they always go to the
 * network. When a page navigation fails offline, the offline page is shown.
 * No push notifications.
 */
const CACHE = "coursepilot-offline-v1";
const OFFLINE_URL = "/offline.html";
const PRECACHE = [OFFLINE_URL, "/brand/coursepilot-mark-128.png", "/brand/pwa-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }
  const url = new URL(request.url);
  if (url.origin === self.location.origin && PRECACHE.includes(url.pathname)) {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
  }
});
