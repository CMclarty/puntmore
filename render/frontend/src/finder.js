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

// The market as the tip names it, and its line when it has one:
// 'Total Goals 2.5', 'Match Result'.
export const marketLabel = (r) => (r.line != null ? `${r.market} ${r.line}` : r.market)

// A price to two places; a dash when the tip quotes none (an asked row).
export const priceLabel = (n) => (n == null ? '—' : n.toFixed(2))

const DONE = new Set(['placed', 'gone', 'expired'])
// An asked row Hermes could not place on a day and time has no kick-off: it
// sorts first, with the questions.
const time = (iso) => (iso ? new Date(iso).getTime() : -8.64e15)

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
