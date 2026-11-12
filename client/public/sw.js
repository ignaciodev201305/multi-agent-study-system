// Service worker: guarda solo el shell estático para que la app abra sin conexión con la PC.
const CACHE = 'estudio-shell-v1';
// Únicas rutas que se guardan; la API, el WebSocket y otros orígenes nunca pasan por aquí.
const STATIC_PATHS = /^\/(assets|icons)\/|^\/(logo\.png|manifest\.webmanifest)$/;

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((name) => name !== CACHE).map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  );
});

const store = async (request, response) => {
  if (response.ok) (await caches.open(CACHE)).put(request, response.clone());
  return response;
};

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    // Red primero para recibir la versión nueva; sin red, el último shell guardado.
    event.respondWith(
      fetch(request).then((response) => store('/index.html', response)).catch(() => caches.match('/index.html')),
    );
    return;
  }

  if (STATIC_PATHS.test(url.pathname)) {
    event.respondWith(
      caches.match(request).then((cached) => cached ?? fetch(request).then((response) => store(request, response))),
    );
  }
});
