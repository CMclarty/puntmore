// The retired shalgo host's service worker, served as shalgo's /sw.js by
// app/site.py (ShalgoRedirect) with the placeholder below set to the main domain's
// origin. An app installed from shalgo runs the real worker, which a redirect
// can never replace; this one takes over at once, unregisters itself (the old
// worker's cache goes with the registration) and loads every window again on
// the main domain, same path. Tested by frontend/src/serviceWorkers.test.js.
const MAIN = __MAIN__
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    await self.clients.claim()
    await self.registration.unregister()
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    await Promise.all(windows.map((client) => {
      const url = new URL(client.url)
      return client.navigate(MAIN + url.pathname + url.search + url.hash).catch(() => null)
    }))
  })())
})
