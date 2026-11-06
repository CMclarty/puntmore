import { expect, test } from 'vitest'
import { groupRows, stakeLabel, statusLabel } from './finder'

test('labels and groups', () => {
  expect(statusLabel('gone')).toBe('Price gone')
  expect(stakeLabel(null)).toBe('—'); expect(stakeLabel(5)).toBe('£5'); expect(stakeLabel(7.5)).toBe('£7.50')
  const g = groupRows([
    { tip_id: 'a', status: 'offered', kickoff: '2026-11-07T15:00:00Z', created_at: '2026-11-06T10:00:00Z' },
    { tip_id: 'b', status: 'offered', kickoff: '2026-11-07T12:00:00Z', created_at: '2026-11-06T11:00:00Z' },
    { tip_id: 'c', status: 'placed', kickoff: '2026-11-07T12:00:00Z', created_at: '2026-11-06T09:00:00Z' },
    { tip_id: 'd', status: 'expired', kickoff: '2026-11-07T12:00:00Z', created_at: '2026-11-06T12:00:00Z' },
  ])
  expect(g.offered.map(r => r.tip_id)).toEqual(['b', 'a'])
  expect(g.done.map(r => r.tip_id)).toEqual(['d', 'c'])
  expect(g.asked).toEqual([])
})

test('every status word has its label, an unknown word is shown as given', () => {
  expect(['asked', 'offered', 'rejected', 'placed', 'gone', 'expired'].map(statusLabel))
    .toEqual(['Asked', 'Offered', 'Rejected', 'Placed', 'Price gone', 'Expired'])
  expect(statusLabel('void')).toBe('void')
  expect(stakeLabel(undefined)).toBe('—'); expect(stakeLabel(0)).toBe('£0')
})

test('asked and rejected rows go soonest kick-off first, and an empty board has four empty groups', () => {
  const g = groupRows([
    { tip_id: 'r2', status: 'rejected', kickoff: '2026-11-08T12:00:00Z', created_at: '2026-11-06T10:00:00Z' },
    { tip_id: 'r1', status: 'rejected', kickoff: '2026-11-07T12:00:00Z', created_at: '2026-11-06T09:00:00Z' },
    { tip_id: 'q', status: 'asked', kickoff: '2026-11-07T12:00:00Z', created_at: '2026-11-06T09:00:00Z' },
    { tip_id: 'g', status: 'gone', kickoff: '2026-11-07T12:00:00Z', created_at: '2026-11-06T09:30:00Z' },
  ])
  expect(g.rejected.map(r => r.tip_id)).toEqual(['r1', 'r2'])
  expect(g.asked.map(r => r.tip_id)).toEqual(['q'])
  expect(g.done.map(r => r.tip_id)).toEqual(['g'])
  expect(groupRows([])).toEqual({ asked: [], offered: [], rejected: [], done: [] })
  expect(groupRows(undefined)).toEqual({ asked: [], offered: [], rejected: [], done: [] })
})
