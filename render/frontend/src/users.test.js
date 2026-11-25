import { describe, expect, it } from 'vitest'
import { changed, isAdmin, toggle, toolName, toolsLine } from './users'

const GRANTABLE = ['coupons', 'cards', 'pnl', 'hills', 'props']

describe('isAdmin', () => {
  it('is only the raw grant *', () => {
    expect(isAdmin({ permissions: '*' })).toBe(true)
    expect(isAdmin({ permissions: 'cards+hills' })).toBe(false)
    expect(isAdmin({ permissions: 'admin' })).toBe(false)
    expect(isAdmin({ permissions: '' })).toBe(false)
    expect(isAdmin(null)).toBe(false)
  })
})

describe('toolsLine', () => {
  it('names each tool, and an admin has all of them', () => {
    expect(toolsLine({ permissions: 'cards+hills', tools: ['cards', 'hills'] })).toBe('Paddy, Hills')
    expect(toolsLine({ permissions: '*', tools: ['coupons', 'admin'] })).toBe('All tools')
    expect(toolsLine({ permissions: 'x', tools: [] })).toBe('')
  })
  it('shows an unknown grant by its own name', () => {
    expect(toolName('mystery')).toBe('mystery')
    expect(toolName('pnl')).toBe('P&L')
  })
})

describe('toggle', () => {
  it('adds and removes a tool, in the grantable order', () => {
    expect(toggle(['hills'], 'cards', GRANTABLE)).toEqual(['cards', 'hills'])
    expect(toggle(['cards', 'hills'], 'cards', GRANTABLE)).toEqual(['hills'])
    expect(toggle([], 'props', GRANTABLE)).toEqual(['props'])
  })
  it('drops a tick for a tool that cannot be granted', () => {
    expect(toggle(['admin', 'cards'], 'hills', GRANTABLE)).toEqual(['cards', 'hills'])
  })
})

describe('changed', () => {
  it('ignores order and sees a difference', () => {
    expect(changed(['cards', 'hills'], ['hills', 'cards'])).toBe(false)
    expect(changed(['cards'], ['cards', 'hills'])).toBe(true)
    expect(changed(['cards', 'pnl'], ['cards', 'hills'])).toBe(true)
  })
})
