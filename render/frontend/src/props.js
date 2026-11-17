// Props (pages/PropsPage.jsx): the pure parts, pinned by props.test.js.
import { shortTeams } from './teams'

// The page lists from 103% of fair (Callum agreed the 3% start); the API
// serves from fair so the cut-off can move without an API deploy.
export const VALUE_FLOOR = 0.03

// One chip per stat the feed carries, in this order.
export const STATS = ['goals', 'assists', 'cards', 'foulscommitted', 'offsides', 'saves', 'shots',
  'shotsongoal', 'tackles']
export const STAT_LABELS = {
  goals: 'Goals', assists: 'Assists', cards: 'Cards', foulscommitted: 'Fouls',
  offsides: 'Offsides', saves: 'Saves', shots: 'Shots', shotsongoal: 'Shots on target',
  tackles: 'Tackles',
}
// Paddy's kick-off windows and max odds.
export const WITHIN = [[null, 'Any'], [1, '< 1h'], ['today', 'Today'], [24, '24h'], [48, '48h'], [72, '72h']]
export const MAX_ODDS = [null, 1.5, 2, 3, 5]
export const SORTS = ['best', 'shortest']   // by the Value and Odds headings
export const NO_FILTERS = { markets: STATS, within: null, maxOdds: null, sort: 'best' }

// A saved filter must never hold a value its option list no longer offers: a
// pill looks the value up by option and would throw. `read` returns the saved
// JSON (localStorage.getItem); it may throw (private mode).
export function loadFilters(read) {
  try {
    const v = JSON.parse(read())
    if (!v || !Array.isArray(v.markets)) return NO_FILTERS
    const oneOf = (x, opts, dflt) => (opts.includes(x) ? x : dflt)
    return {
      markets: v.markets.filter(m => STATS.includes(m)),
      within: oneOf(v.within, WITHIN.map(([w]) => w), null),
      maxOdds: oneOf(v.maxOdds, MAX_ODDS, null),
      sort: oneOf(v.sort, SORTS, 'best'),
    }
  } catch { /* none saved, or private mode */ }
  return NO_FILTERS
}

export const allMarkets = markets => STATS.every(m => markets.includes(m))

export function inWindow(iso, within, now) {
  if (within == null) return true
  const ko = new Date(iso).getTime()
  const midnight = new Date(now); midnight.setHours(24, 0, 0, 0)
  if (within === 'today') return ko < midnight.getTime()
  return ko - now <= within * 3600_000
}

// Every bet at or above the floor, flat, carrying its match (short names) and kick-off.
export const rowsOf = feed => (feed || []).flatMap(item => item.bets
  .filter(b => b.value >= VALUE_FLOOR)
  .map(b => ({
    ...b, key: `${item.event_id}|${b.player_id}|${b.stat}|${b.n}`,
    match: shortTeams(item.match), matchId: item.event_id, commence_time: item.commence_time,
  })))

export const filterRows = (rows, fl, now) => rows.filter(b => fl.markets.includes(b.stat)
  && inWindow(b.commence_time, fl.within, now)
  && (fl.maxOdds == null || b.odds <= fl.maxOdds))

// Best value first, or shortest odds first (best value breaking a tie).
export const sortRows = (rows, sort) => [...rows].sort(sort === 'shortest'
  ? (x, y) => x.odds - y.odds || y.value - x.value
  : (x, y) => y.value - x.value)

// The oldest fetch behind the board: "updated" never claims fresher prices.
export const oldestPricedAt = feed => (feed || []).map(i => i.priced_at).filter(Boolean).sort()[0]
