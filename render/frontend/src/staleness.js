// When a coupon's prices are old enough to say so (PLAN.md item 4): the newest
// fair-odds time among the priced legs, when it is more than twice the
// server's pricing refresh ago, else null. A list whose refresh has silently
// stopped must not read as current (frontend AGENTS.md, "every live-price
// screen"). The page's own 2-minute poll only re-reads what the server
// priced; the server re-prices every 10 minutes (api scheduler).
export const SERVER_REFRESH_MS = 10 * 60_000
export const STALE_AFTER_MS = 2 * SERVER_REFRESH_MS

export function pricesStaleSince(legs, now = Date.now(), staleAfterMs = STALE_AFTER_MS) {
  let newest = null
  for (const leg of legs || []) {
    if (leg.fair_odds == null || !leg.fair_odds_updated_at) continue
    const t = new Date(leg.fair_odds_updated_at).getTime()
    if (!Number.isNaN(t) && (newest === null || t > newest)) newest = t
  }
  if (newest === null || now - newest <= staleAfterMs) return null
  return new Date(newest)
}
