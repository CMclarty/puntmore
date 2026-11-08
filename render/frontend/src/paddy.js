// The Paddy board's small pure helpers (pages/CardsPage.jsx), pinned by paddy.test.js.

// How long ago the prices were fetched: "now", "3m", "2h".
export function age(iso, now) {
  if (!iso) return null
  const m = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 60_000))
  return m < 1 ? 'now' : m < 60 ? `${m}m` : `${Math.floor(m / 60)}h`
}

// Kick-off: the time, with the day above it when the game isn't today.
export function kickoff(iso, now = Date.now()) {
  const d = new Date(iso)
  const day = d.toDateString() === new Date(now).toDateString() ? null
    : d.toLocaleDateString('en-GB', { weekday: 'short' })
  return { day, time: d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) }
}
