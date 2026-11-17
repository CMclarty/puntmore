import { describe, expect, it } from 'vitest'
import {
  NO_FILTERS, STATS, VALUE_FLOOR, filterRows, loadFilters, oldestPricedAt, rowsOf, sortRows,
} from './props'

const bet = (over = {}) => ({
  player: 'Erling Haaland', player_id: 1, stat: 'shotsongoal', n: 2,
  pick: 'Erling Haaland 2+ shots on target', odds: 2.5, fair: 2.2, value: 0.1, pinnacle_margin: 0.05,
  ...over,
})
const item = (over = {}) => ({
  event_id: 'e1', match: 'Manchester United vs Wolverhampton Wanderers', home: 'Manchester United',
  away: 'Wolverhampton Wanderers', commence_time: '2026-11-17T20:00:00Z',
  priced_at: '2026-11-17T12:00:00Z', best_value: 0.1, bets: [bet()], ...over,
})
const now = Date.parse('2026-11-17T12:00:00Z')

describe('rowsOf', () => {
  it('keeps a bet at 3% and drops one at 2.9%', () => {
    expect(VALUE_FLOOR).toBe(0.03)
    const rows = rowsOf([item({ bets: [bet({ player_id: 1, value: 0.029 }), bet({ player_id: 2, value: 0.03 })] })])
    expect(rows.map(r => r.player_id)).toEqual([2])
  })
  it('carries the match in short names, its id and kick-off', () => {
    const [r] = rowsOf([item()])
    expect(r.match).toBe('Man Utd vs Wolves')
    expect(r.matchId).toBe('e1')
    expect(r.commence_time).toBe('2026-11-17T20:00:00Z')
    expect(r.pick).toBe('Erling Haaland 2+ shots on target')
  })
  it('gives each row its own key', () => {
    const rows = rowsOf([item({ bets: [bet({ n: 1 }), bet({ n: 2 }), bet({ stat: 'shots' })] })])
    expect(new Set(rows.map(r => r.key)).size).toBe(3)
  })
  it('is empty for an empty feed', () => {
    expect(rowsOf([])).toEqual([])
    expect(rowsOf(null)).toEqual([])
    expect(oldestPricedAt([])).toBeUndefined()
  })
})

describe('oldestPricedAt', () => {
  it('is the oldest stamp, ignoring a missing one', () => {
    expect(oldestPricedAt([item({ priced_at: '2026-11-17T12:05:00Z' }), item({ priced_at: null }),
      item({ priced_at: '2026-11-17T11:58:00Z' })])).toBe('2026-11-17T11:58:00Z')
  })
})

describe('filterRows', () => {
  const rows = rowsOf([
    item({ event_id: 'soon', commence_time: '2026-11-17T12:30:00Z', bets: [bet({ stat: 'goals', odds: 1.4 })] }),
    item({ event_id: 'tonight', commence_time: '2026-11-17T20:00:00Z', bets: [bet({ stat: 'tackles', odds: 2.0 })] }),
    item({ event_id: 'later', commence_time: '2026-11-19T20:00:00Z', bets: [bet({ stat: 'saves', odds: 4.0 })] }),
  ])
  const ids = (fl) => filterRows(rows, { ...NO_FILTERS, ...fl }, now).map(r => r.matchId)

  it('shows every row by default', () => {
    expect(ids({})).toEqual(['soon', 'tonight', 'later'])
  })
  it('keeps only the markets switched on', () => {
    expect(ids({ markets: ['tackles', 'saves'] })).toEqual(['tonight', 'later'])
    expect(ids({ markets: [] })).toEqual([])
  })
  it('keeps the kick-off window', () => {
    expect(ids({ within: 1 })).toEqual(['soon'])
    expect(ids({ within: 24 })).toEqual(['soon', 'tonight'])
  })
  it('keeps today', () => {
    const local = new Date(2026, 10, 17, 12, 0).getTime()
    const today = rowsOf([
      item({ event_id: 'today', commence_time: new Date(2026, 10, 17, 22, 0).toISOString() }),
      item({ event_id: 'tomorrow', commence_time: new Date(2026, 10, 18, 1, 0).toISOString() }),
    ])
    expect(filterRows(today, { ...NO_FILTERS, within: 'today' }, local).map(r => r.matchId)).toEqual(['today'])
  })
  it('keeps odds up to the max', () => {
    expect(ids({ maxOdds: 2 })).toEqual(['soon', 'tonight'])
    expect(ids({ maxOdds: 1.5 })).toEqual(['soon'])
  })
})

describe('sortRows', () => {
  const rows = [
    bet({ player_id: 'a', odds: 3.0, value: 0.05 }),
    bet({ player_id: 'b', odds: 1.8, value: 0.2 }),
    bet({ player_id: 'c', odds: 1.8, value: 0.3 }),
    bet({ player_id: 'd', odds: 5.0, value: 0.1 }),
  ]
  it('puts the best value first', () => {
    expect(sortRows(rows, 'best').map(r => r.player_id)).toEqual(['c', 'b', 'd', 'a'])
  })
  it('puts the shortest odds first, best value breaking a tie', () => {
    expect(sortRows(rows, 'shortest').map(r => r.player_id)).toEqual(['c', 'b', 'a', 'd'])
  })
  it('leaves the list it was given alone', () => {
    sortRows(rows, 'best')
    expect(rows.map(r => r.player_id)).toEqual(['a', 'b', 'c', 'd'])
  })
})

describe('loadFilters', () => {
  const saved = v => () => JSON.stringify(v)
  it('starts with every market on, any time, any odds, best first', () => {
    expect(loadFilters(() => null)).toEqual({ markets: STATS, within: null, maxOdds: null, sort: 'best' })
  })
  it('reads back what was saved', () => {
    const fl = { markets: ['goals', 'saves'], within: 'today', maxOdds: 2, sort: 'shortest' }
    expect(loadFilters(saved(fl))).toEqual(fl)
  })
  it('drops a stale saved value back to its default', () => {
    expect(loadFilters(saved({ markets: ['goals', 'corners'], within: 3, maxOdds: 2.5, sort: 'worst' })))
      .toEqual({ markets: ['goals'], within: null, maxOdds: null, sort: 'best' })
  })
  it('falls back on junk, a missing market list, or storage that throws', () => {
    expect(loadFilters(() => '{not json')).toEqual(NO_FILTERS)
    expect(loadFilters(saved({ within: 24 }))).toEqual(NO_FILTERS)
    expect(loadFilters(() => { throw new Error('private mode') })).toEqual(NO_FILTERS)
  })
})
