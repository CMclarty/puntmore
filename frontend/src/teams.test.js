import { describe, it, expect } from 'vitest'
import { shortTeams } from './teams'

describe('shortTeams', () => {
  it('shows clubs by their common short name', () => {
    expect(shortTeams('Tottenham Hotspur')).toBe('Spurs')
    expect(shortTeams('Tottenham')).toBe('Spurs')
    expect(shortTeams('Milton Keynes Dons')).toBe('MK Dons')
    expect(shortTeams('Nottm Forest')).toBe('Forest')
  })
  it('shortens every club in a match or a pick', () => {
    expect(shortTeams('Manchester United vs Wolverhampton Wanderers')).toBe('Man Utd vs Wolves')
    expect(shortTeams('Tottenham Hotspur Over 4.5 corners')).toBe('Spurs Over 4.5 corners')
    expect(shortTeams('Brighton & Hove Albion v West Bromwich Albion')).toBe('Brighton v West Brom')
  })
  it('replaces only whole names and leaves others alone', () => {
    expect(shortTeams('Inter Miami vs Bristol Rovers')).toBe('Inter Miami vs Bristol Rovers')
    expect(shortTeams('Arsenal')).toBe('Arsenal')
    expect(shortTeams('TOTTENHAM HOTSPUR')).toBe('Spurs')
    expect(shortTeams(undefined)).toBe(undefined)
  })
})
