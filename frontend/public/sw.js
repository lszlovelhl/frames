/* 帧间 PWA Service Worker —— 离线壳缓存
   策略：HTML 导航 network-first（保证部署新版本立即可见），静态资源 cache-first，API 永不缓存 */
const CACHE = "frames-shell-v2";
const SHELL = ["/", "/manifest.webmanifest", "/logo.png", "/icon-192.png", "/icon-512.png", "/favicon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // 仅同源 GET；API 请求永不缓存（数据本地实时）
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  const isNav = e.request.mode === "navigate";
  if (isNav) {
    // HTML 导航：网络优先，离线时回退缓存壳
    e.respondWith(
      fetch(e.request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
          return resp;
        })
        .catch(() => caches.match(e.request).then((hit) => hit || caches.match("/"))),
    );
    return;
  }
  // 静态资源：cache-first，未命中再缓存
  e.respondWith(
    caches.match(e.request).then(
      (hit) =>
        hit ||
        fetch(e.request)
          .then((resp) => {
            if (resp.ok) {
              const copy = resp.clone();
              caches.open(CACHE).then((c) => c.put(e.request, copy));
            }
            return resp;
          })
          .catch(() => caches.match("/")),
    ),
  );
});
