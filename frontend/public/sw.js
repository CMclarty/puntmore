// Bump this on any caching-strategy change — activate() purges older caches.
const SHELL_CACHE = 'puntmore-shell-v2'

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(SHELL_CACHE)
      .then(c => c.addAll(['/']))
      .then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== SHELL_CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', e => {
  const req = e.request
  const { pathname } = new URL(req.url)

  // API: network-first, fall back to empty JSON array (offline tolerance).
  if (pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(req).catch(() =>
        new Response('[]', { headers: { 'Content-Type': 'application/json' } })
      )
    )
    return
  }

  // Content-hashed build assets are immutable — cache-first is safe and fast.
  if (pathname.startsWith('/assets/')) {
    e.respondWith(
      caches.match(req).then(cached =>
        cached || fetch(req).then(resp => {
          if (resp.ok) caches.open(SHELL_CACHE).then(c => c.put(req, resp.clone()))
          return resp
        })
      )
    )
    return
  }

  // App shell / navigations: NETWORK-FIRST so a new deploy (and its new nav) is
  // picked up on the very next load; fall back to the cached shell offline.
  // Cache-first here was why new tabs never appeared until the cache expired.
  e.respondWith(
    fetch(req).then(resp => {
      if (resp.ok && req.method === 'GET') {
        const copy = resp.clone()
        caches.open(SHELL_CACHE).then(c => c.put(req, copy))
      }
      return resp
    }).catch(() => caches.match(req).then(c => c || caches.match('/')))
  )
})
