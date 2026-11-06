import { describe, expect, it } from 'vitest'
import { edgePct, emptyMeansNothingOnOffer, kickoffLabel, timeLabel } from './twoup'
import { tabsFor } from './pages/Three65Page'

describe('the 2-Up tab', () => {
  it('shows edge as the price against fair', () => {
    expect(edgePct(0.023)).toBe('102.3%')
    expect(edgePct(-0.01)).toBe('99.0%')
  })

  it('keeps the day off a kick-off that is today', () => {
    const now = new Date('2026-11-06T10:00:00')
    expect(kickoffLabel('2026-11-06T15:00:00', now)).toMatch(/^\d\d:\d\d$/)
    expect(kickoffLabel('2026-11-07T15:00:00', now)).toMatch(/^Sat \d\d:\d\d$/)
  })

  it('timeLabel is HH:MM or a dash', () => {
    expect(timeLabel('2026-11-06T14:05:00+00:00')).toMatch(/^\d{2}:\d{2}$/)
    expect(timeLabel(null)).toBe('—')
    expect(timeLabel(undefined)).toBe('—')
    expect(timeLabel('nope')).toBe('—')
  })

  it('timeLabel gives the browser-zone time and a dash for anything not a time', () => {
    // A zoneless ISO string is read as local time, so this pins the minutes and hours.
    expect(timeLabel('2026-11-06T09:07:00')).toBe('09:07')
    expect(timeLabel('')).toBe('—')
    expect(timeLabel(0)).toBe('—')
    expect(timeLabel({})).toBe('—')
  })

  it('says "nothing qualifies" only for a healthy, refreshed, unkilled board', () => {
    expect(emptyMeansNothingOnOffer({ degraded: false })).toBe(true)
    expect(emptyMeansNothingOnOffer(undefined)).toBe(true)          // an older API sends no status
    expect(emptyMeansNothingOnOffer({ killed: true })).toBe(false)
    expect(emptyMeansNothingOnOffer({ not_yet_refreshed: true })).toBe(false)
    // passes failing before any board exists: "Cannot price: ALL — …" says why
    expect(emptyMeansNothingOnOffer({ degraded: true, served_at: null })).toBe(false)
  })

  it('puts 2-Up first on the 365 page and shows only granted tabs', () => {
    expect(tabsFor(['accabonus', 'goalscorers']).map(t => t.path)).toEqual(['2up', 'scorers'])
    expect(tabsFor(['goalscorers']).map(t => t.path)).toEqual(['scorers'])
    expect(tabsFor([])).toEqual([])
  })
})
