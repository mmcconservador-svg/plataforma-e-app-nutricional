// Service worker mínimo: guarda só arquivos estáticos públicos.
// Páginas e dados de saúde NUNCA são armazenados em cache (LGPD, aparelhos compartilhados).
var CACHE = "nsi-static-v1";
var ESTATICOS = ["/static/style.css", "/static/app.js", "/static/icon-192.png", "/offline"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(ESTATICOS); }));
  self.skipWaiting();
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }));
});
self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.indexOf("/static/") === 0) {
    e.respondWith(caches.match(req).then(function (r) { return r || fetch(req); }));
  } else if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(function () { return caches.match("/offline"); }));
  }
});
