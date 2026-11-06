// The Finder page's pure helpers (pages/FinderPage.jsx), pinned by
// finder.test.js. The status words are docs/finder-spec.md's, nowhere else.

const LABELS = {
  asked: 'Asked', offered: 'Offered', rejected: 'Rejected',
  placed: 'Placed', gone: 'Price gone', expired: 'Expired',
}

export const statusLabel = (status) => LABELS[status] ?? status

// Pounds: '£5', '£7.50'; a dash when the tip and Hermes gave none.
export function stakeLabel(n) {
  if (n == null) return '—'
  return Number.isInteger(n) ? `£${n}` : `£${n.toFixed(2)}`
}

const DONE = new Set(['placed', 'gone', 'expired'])
const time = (iso) => new Date(iso).getTime()

// The board in its four groups: asked, offered and rejected soonest kick-off
// first; done (placed, price gone, expired) newest first.
export function groupRows(rows = []) {
  const g = { asked: [], offered: [], rejected: [], done: [] }
  for (const r of rows || []) {
    if (DONE.has(r.status)) g.done.push(r)
    else if (g[r.status]) g[r.status].push(r)
  }
  for (const k of ['asked', 'offered', 'rejected']) g[k].sort((a, b) => time(a.kickoff) - time(b.kickoff))
  g.done.sort((a, b) => time(b.created_at) - time(a.created_at))
  return g
}
