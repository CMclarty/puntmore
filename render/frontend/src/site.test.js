import { expect, test } from 'vitest'
import * as site from './site'
import { siteFor, siteTools, toolSite, otherSiteUrl } from './site'

test('two sites: the main domain and me; shalgo is retired', () => {
  expect(siteFor('me.puntmore.com')).toBe('me')
  expect(siteFor('puntmore.com')).toBe('puntmore')
  expect(siteFor('shalgo.puntmore.com')).toBe('puntmore')
  expect(site.SHALGO).toBeUndefined()
  expect(site.SHALGO_TOOLS).toBeUndefined()
  expect(site.ME_TOOLS).toEqual(['finder', 'pnl', 'kelly', 'boost', 'shop'])
})

test('every public tool is on the main site, the personal ones on me', () => {
  for (const t of ['coupons', 'cards', 'hills', 'accabonus', 'goalscorers']) expect(toolSite(t)).toBe('puntmore')
  for (const t of ['finder', 'pnl', 'kelly', 'boost', 'shop']) expect(toolSite(t)).toBe('me')
  const loc = { protocol: 'https:', hostname: 'puntmore.com', port: '' }
  expect(otherSiteUrl('/finder', 'me', loc)).toBe('https://me.puntmore.com/finder')
  expect(otherSiteUrl('/paddy', 'puntmore', { ...loc, hostname: 'me.puntmore.com' })).toBe('https://puntmore.com/paddy')
})

test('a link to the main site drops the subdomain, and the port survives', () => {
  const loc = { protocol: 'http:', hostname: 'me.localhost', port: '5173' }
  expect(otherSiteUrl('/', 'puntmore', loc)).toBe('http://localhost:5173/')
  expect(otherSiteUrl('/x', 'me', { ...loc, hostname: 'www.puntmore.com', port: '' })).toBe('http://me.puntmore.com/x')
  expect(siteFor('me')).toBe('puntmore')
})

test('each site shows only its own tools', () => {
  const grant = ['coupons', 'cards', 'finder', 'kelly']
  expect(siteTools(grant, 'me')).toEqual(['finder', 'kelly'])
  expect(siteTools(grant, 'puntmore')).toEqual(['coupons', 'cards'])
})
