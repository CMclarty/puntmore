// Bump this on any caching-strategy change — activate() purges older caches.
// v3: v2 could pin a device to a dead shell. A deploy swaps containers for
// ~40s, during which every request 502s, and content-hashed assets from the
// outgoing build are deleted. A shell cached before the deploy points at those
// deleted hashes, so it renders a blank page forever. The bump is load-bearing:
// it evicts any already-poisoned v2 cache on the next visit.
const SHELL_CACHE = 'puntmore-shell-v3'

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
  // Only cache a genuine hit: a 404 (the hash was deleted by a later deploy) or
  // a 502 (mid-deploy) must never be stored, or the miss becomes permanent.
  if (pathname.startsWith('/assets/')) {
    e.respondWith(
      caches.match(req).then(cached =>
        cached || fetch(req).then(resp => {
          if (resp.ok) {
            const copy = resp.clone()
            caches.open(SHELL_CACHE).then(c => c.put(req, copy))
          }
          return resp
        })
      )
    )
    return
  }

  // App shell / navigations: NETWORK-FIRST so a new deploy (and its new nav) is
  // picked up on the very next load; fall back to the cached shell offline.
  // Cache-first here was why new tabs never appeared until the cache expired.
  //
  // A failed response is NOT the same as a failed fetch. Render returns a real
  // 502 while it swaps containers, which resolves the promise — passing that
  // straight through is what put a raw error page (and a blank PWA) on screen
  // during every deploy. Treat a non-ok navigation like an offline one: serve
  // the cached shell if there is one, and only surface the error if there
  // isn't. The shell is still never overwritten with a failure response.
  e.respondWith(
    fetch(req).then(resp => {
      if (resp.ok && req.method === 'GET') {
        const copy = resp.clone()
        caches.open(SHELL_CACHE).then(c => c.put(req, copy))
        return resp
      }
      if (!resp.ok) {
        return caches.match(req).then(c => c || caches.match('/')).then(c => c || resp)
      }
      return resp
    }).catch(() => caches.match(req).then(c => c || caches.match('/')))
  )
})
