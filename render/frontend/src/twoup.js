// The 2-Up tab's pure helpers (TwoUpPage.jsx), pinned by twoup.test.js.

// Edge fraction -> the bet's price as a % of fair: 0.023 -> "102.3%".
export const edgePct = (edge) => `${((1 + edge) * 100).toFixed(1)}%`

// "Sat 15:00" in the viewer's zone; the day only when it is not today.
export function kickoffLabel(iso, now = new Date()) {
  const d = new Date(iso)
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (d.toDateString() === now.toDateString()) return time
  return `${d.toLocaleDateString([], { weekday: 'short' })} ${time}`
}

// "14:05" in the viewer's zone, for when the board or a row was priced; a dash
// for a missing or unparseable time (an older API mid-deploy sends none).
// hourCycle keeps it 24-hour in a 12-hour locale too.
export function timeLabel(iso) {
  if (typeof iso !== 'string' || !iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
}
