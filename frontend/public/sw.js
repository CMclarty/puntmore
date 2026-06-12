const SHELL_CACHE = 'puntmore-shell-v1'

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
  const { pathname } = new URL(e.request.url)

  // API: network-first, fall back to empty JSON array (offline tolerance)
  if (pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request).catch(() =>
        new Response('[]', { headers: { 'Content-Type': 'application/json' } })
      )
    )
    return
  }

  // App shell: cache-first, fill cache on network hit
  e.respondWith(
    caches.match(e.request).then(cached => {
      const networkFetch = fetch(e.request).then(resp => {
        if (resp.ok) {
          caches.open(SHELL_CACHE).then(c => c.put(e.request, resp.clone()))
        }
        return resp
      })
      return cached || networkFetch
    }).catch(() => caches.match('/'))
  )
})
