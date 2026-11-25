// The Users page's helpers (pages/UsersPage.jsx). Pure, pinned by users.test.js.
// The API is the one source of which tools can be granted (`grantable`); these
// only name them and keep a tick list in that order.

// A grant name as the page shows it: the tool's own name (BottomNav.jsx).
const NAMES = {
  coupons: 'Coupon', cards: 'Paddy', hills: 'Hills', goalscorers: 'Goalscorers', pnl: 'P&L',
  kelly: 'Kelly', boost: 'Virtuals', shop: 'Shop', aw26: 'AW 26', finder: 'Finder',
  props: 'Props', accabonus: '365', admin: 'Users',
}

export const toolName = tool => NAMES[tool] || tool

// Only a raw grant of exactly "*" is an admin: it has every tool and no controls.
export const isAdmin = user => (user?.permissions || '').trim() === '*'

// A user's tools as one line.
export const toolsLine = user => (isAdmin(user) ? 'All tools' : (user?.tools || []).map(toolName).join(', '))

// Tick or untick `tool`, keeping the list in the grantable order.
export function toggle(ticked, tool, grantable) {
  const on = new Set(ticked)
  if (on.has(tool)) on.delete(tool)
  else on.add(tool)
  return grantable.filter(t => on.has(t))
}

// The ticks differ from what is saved: a save would change something.
export function changed(ticked, saved) {
  const a = new Set(ticked), b = new Set(saved)
  return a.size !== b.size || [...a].some(t => !b.has(t))
}
