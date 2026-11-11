import { expect, test } from 'vitest'
import { isAutoSettled } from './hills'

test('both vendors settle automatically; a person does not', () => {
  expect(isAutoSettled('odds-api.net')).toBe(true)
  expect(isAutoSettled('oddspapi')).toBe(true)
  expect(isAutoSettled('callum')).toBe(false)
  expect(isAutoSettled(null)).toBe(false)
})
