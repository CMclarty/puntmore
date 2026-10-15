import { useEffect, useRef } from 'react'

/**
 * Re-run `refresh` every `everyMs` while the page is on screen, and at once
 * when the app comes back to the foreground after `staleMs` away — a phone
 * left in a pocket must not show the prices it had when it was put away.
 *
 * Silent by design: the caller refreshes in place, keeping the list on screen.
 * Every feed this serves reads the server's own caches, so a refresh costs no
 * vendor quota however many people have the app open.
 */
export const REFRESH_MS = 2 * 60_000

export default function useAutoRefresh(refresh, { everyMs = REFRESH_MS, staleMs = 30_000 } = {}) {
  const fn = useRef(refresh)
  fn.current = refresh
  const last = useRef(Date.now())

  useEffect(() => {
    const run = () => { last.current = Date.now(); fn.current() }
    const tick = setInterval(() => {
      if (document.visibilityState === 'visible') run()
    }, everyMs)
    const onShow = () => {
      if (document.visibilityState === 'visible' && Date.now() - last.current >= staleMs) run()
    }
    document.addEventListener('visibilitychange', onShow)
    window.addEventListener('focus', onShow)
    window.addEventListener('online', onShow)
    return () => {
      clearInterval(tick)
      document.removeEventListener('visibilitychange', onShow)
      window.removeEventListener('focus', onShow)
      window.removeEventListener('online', onShow)
    }
  }, [everyMs, staleMs])
}
