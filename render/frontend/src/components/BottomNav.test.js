import { expect, test } from 'vitest'
import { landingFor } from './BottomNav'

// Where an account lands with no page to return to: its first tool in nav
// order. On the main domain an account without the tracker (Paddy and Hills
// only) must not be sent to "/", which is the tracker.
test('an account lands on its first granted tool', () => {
  expect(landingFor(['cards', 'hills'])).toBe('/paddy')
  expect(landingFor(['pnl'])).toBe('/pnl/dogs')
  expect(landingFor(['coupons', 'cards'])).toBe('/')
})

test('an account with nothing lands on the tracker path', () => {
  expect(landingFor([])).toBe('/')
  expect(landingFor()).toBe('/')
})

test('no account lands on Users, which is last', () => {
  expect(landingFor(['coupons', 'cards', 'admin'])).toBe('/')
  expect(landingFor(['pnl', 'admin'])).toBe('/pnl/dogs')
})
