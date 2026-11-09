import { describe, it, expect } from 'vitest'
import { money, pounds, progress, dayName, stepDay, sections, mark, tally, pickText, openTo, flagged, koTime } from './aw26'

const sel = (o) => ({ leg_id: 1, key: 'k', game: 'w:a v b', event: 'A v B', market: null,
  selection: 'Over 10.5 corners', kickoff: '2026-11-09T15:00:00', day: '2026-11-09',
  slips: 1, odds: 1.8, result: 'pending', fair_assumed: false, ...o })
const NOW = Date.parse('2026-11-09T18:00:00Z')

describe('aw26', () => {
  it('writes money with its sign', () => {
    expect(money(3240)).toBe('+£3,240')
    expect(money(-12.5, true)).toBe('−£12.50')
    expect(money(0)).toBe('£0')
    expect(money(null)).toBe('—')
    expect(pounds(10700)).toBe('£10,700')
  })
  it('keeps the bar between empty and full, whatever the profit', () => {
    expect(progress(3240, 100000)).toEqual({ width: 3.24, label: '3.2% of £100,000' })
    expect(progress(-250, 100000)).toEqual({ width: 0, label: '0% of £100,000' })
    expect(progress(0, 100000).width).toBe(0)
    expect(progress(250000, 100000).width).toBe(100)
    expect(progress(10, 0).width).toBe(0)
  })
  it('names days', () => {
    expect(dayName('2026-11-09', '2026-11-09')).toBe('Today')
    expect(dayName('2026-11-10', '2026-11-09')).toBe('Tomorrow')
    expect(dayName('2026-11-12', '2026-11-09')).toBe('Thu 12 Nov')
    expect(dayName('2026-11-08', '2026-11-09')).toBe('Sun 8 Nov')
  })
  it('steps a day at a time, inside the first day and today', () => {
    expect(stepDay('2026-11-10', -1, '2026-11-09', '2026-11-12')).toBe('2026-11-09')
    expect(stepDay('2026-11-09', -1, '2026-11-09', '2026-11-12')).toBe(null)
    expect(stepDay('2026-11-12', 1, '2026-11-09', '2026-11-12')).toBe(null)
    expect(stepDay('2026-11-30', 1, '2026-11-09', '2026-12-05')).toBe('2026-12-01')
  })
  it('marks a selection, and gives up on one a day after kick-off', () => {
    expect(mark(sel({ result: 'won' }), NOW)).toBe('W')
    expect(mark(sel({ result: 'lost' }), NOW)).toBe('L')
    expect(mark(sel({ result: 'void' }), NOW)).toBe('V')
    expect(mark(sel({}), NOW)).toBe('–')
    expect(mark(sel({ kickoff: '2026-11-08T15:00:00' }), NOW)).toBe('?')
    expect(mark(sel({ kickoff: null }), NOW)).toBe('–')
  })
  it('puts a game with several selections on one row, under its day', () => {
    const out = sections([
      sel({ leg_id: 1, slips: 9, result: 'won' }),
      sel({ leg_id: 2, selection: 'Over 4.5 cards', slips: 5, result: 'lost' }),
      sel({ leg_id: 3, game: 'w:c v d', event: 'C v D', kickoff: '2026-11-09T12:30:00', slips: 6 }),
      sel({ leg_id: 4, game: 'w:e v f', event: 'E v F', kickoff: '2026-11-10T19:45:00', day: '2026-11-10' }),
    ], '2026-11-09', NOW)
    expect(out.map(s => [s.title, s.tally])).toEqual([['Today', '1 won · 1 lost · 1 to come'], ['Tomorrow', '1 to come']])
    expect(out[0].games.map(g => [g.event, g.slips, g.picks.map(p => p.mark).join('')])).toEqual([['C v D', 6, '–'], ['A v B', 14, 'WL']])
  })
  it('counts a day', () => {
    expect(tally([sel({ result: 'won' }), sel({ result: 'void' })])).toBe('1 won')
    expect(tally([])).toBe('')
  })
  it('shortens a pick and keeps old slips readable', () => {
    expect(pickText({ market: null, selection: 'Over 10.5 corners' })).toBe('O10.5 corners')
    expect(pickText({ market: null, selection: 'Under 3.5 Chelsea cards' })).toBe('U3.5 Chelsea cards')
    expect(pickText({ market: 'Total Match Corners', selection: 'Over 8.5' })).toBe('Total Match Corners O8.5')
    expect(pickText({ market: 'Match Result', selection: null })).toBe('Match Result')
  })
  it('says how long a slip stays open and whether it needs a look', () => {
    const legs = [{ result: 'won', kickoff: '2026-11-14T15:00:00' }, { result: 'pending', kickoff: '2026-11-12T19:45:00' },
      { result: 'pending', kickoff: null }]
    expect(openTo({ legs })).toBe('Thu')
    expect(openTo({ legs: [{ result: 'pending', kickoff: null }] })).toBe(null)
    expect(flagged({ ev: null })).toBe(true)
    expect(flagged({ ev: 0 })).toBe(false)
  })
  it('prints a kick-off as London time', () => {
    expect(koTime('2026-11-09T15:00:00')).toBe('15:00')
    expect(koTime('2026-07-01T15:00:00')).toBe('16:00')
    expect(koTime(null)).toBe('')
  })
})
