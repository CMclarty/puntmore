import { describe, expect, it } from 'vitest'
import { age, kickoff } from './paddy'

describe('age', () => {
  const now = Date.parse('2026-11-08T15:05:00Z')
  it('counts minutes, then hours', () => {
    expect(age('2026-11-08T15:04:40Z', now)).toBe('now')
    expect(age('2026-11-08T15:02:00Z', now)).toBe('3m')
    expect(age('2026-11-08T13:01:00Z', now)).toBe('2h')
  })
  it('says nothing without a time', () => {
    expect(age(null, now)).toBe(null)
    expect(age(undefined, now)).toBe(null)
  })
})

describe('kickoff', () => {
  const now = new Date(2026, 10, 8, 12, 0).getTime()   // Sun 8 Nov, local
  it('is the time alone today', () => {
    expect(kickoff(new Date(2026, 10, 8, 16, 30).toISOString(), now)).toEqual({ day: null, time: '16:30' })
  })
  it('puts the day above another day', () => {
    expect(kickoff(new Date(2026, 10, 9, 20, 0).toISOString(), now)).toEqual({ day: 'Mon', time: '20:00' })
  })
})
