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

// A bet's one line: its legs as "selection @ price", joined for a multiple.
export const betLabel = (bet) =>
  (bet.legs || []).map(l => `${l.selection} @ ${Number(l.price).toFixed(2)}`).join(' / ')

// What the bet is worth saying after its status: the price it went on at, the
// new price it was re-run at, why it failed, or how it settled.
export function betNote(bet) {
  switch (bet.status) {
    case 'placed': return bet.actual_price != null ? `@ ${bet.actual_price.toFixed(2)}` : ''
    case 'price_changed': return bet.new_price != null ? `now ${bet.new_price.toFixed(2)}` : ''
    case 'failed': return bet.fail_reason || ''
    case 'settled': return bet.outcome
      ? `${bet.outcome}${bet.return_amount != null ? ` £${bet.return_amount.toFixed(2)}` : ''}` : ''
    default: return ''
  }
}

export const gbp = (n) => `£${Number(n || 0).toFixed(2)}`

// A signed P&L: "+£12.50" / "-£3.00".
export const signedGbp = (n) => `${n < 0 ? '-' : '+'}£${Math.abs(Number(n || 0)).toFixed(2)}`
