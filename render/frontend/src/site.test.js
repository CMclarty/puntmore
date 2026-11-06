import { afterEach, expect, test } from 'vitest'
import * as site from './site'
import { siteFor, siteTools, toolSite, otherSiteUrl, setMeHost } from './site'

// Me exists only when the API says so (GET /api/auth/site, ME_HOST); blank by default.
afterEach(() => setMeHost('', 'puntmore.com'))

test('two sites: the main domain and me; shalgo is retired', () => {
  setMeHost('me.puntmore.com', 'me.puntmore.com')
  expect(site.SITE).toBe('me')
  expect(site.SITE_NAME).toBe('Me')
  expect(siteFor('me.puntmore.com')).toBe('me')
  expect(siteFor('puntmore.com')).toBe('puntmore')
  expect(siteFor('shalgo.puntmore.com')).toBe('puntmore')
  expect(site.SHALGO).toBeUndefined()
  expect(site.SHALGO_TOOLS).toBeUndefined()
  expect(site.ME_TOOLS).toEqual(['finder', 'pnl', 'kelly', 'boost', 'shop'])
})

test('every public tool is on the main site, the personal ones on me', () => {
  setMeHost('me.puntmore.com', 'puntmore.com')
  for (const t of ['coupons', 'cards', 'hills', 'accabonus', 'goalscorers']) expect(toolSite(t)).toBe('puntmore')
  for (const t of ['finder', 'pnl', 'kelly', 'boost', 'shop']) expect(toolSite(t)).toBe('me')
  const loc = { protocol: 'https:', hostname: 'puntmore.com', port: '' }
  expect(otherSiteUrl('/finder', 'me', loc)).toBe('https://me.puntmore.com/finder')
  expect(otherSiteUrl('/paddy', 'puntmore', { ...loc, hostname: 'me.puntmore.com' })).toBe('https://puntmore.com/paddy')
})

test('a link to the main site drops the subdomain, and the port survives', () => {
  setMeHost('me.localhost', 'localhost')
  const loc = { protocol: 'http:', hostname: 'me.localhost', port: '5173' }
  expect(otherSiteUrl('/', 'puntmore', loc)).toBe('http://localhost:5173/')
  expect(otherSiteUrl('/x', 'me', { ...loc, hostname: 'localhost' })).toBe('http://me.localhost:5173/x')
  setMeHost('me.puntmore.com', 'puntmore.com')
  expect(otherSiteUrl('/x', 'me', { ...loc, hostname: 'www.puntmore.com', port: '' })).toBe('http://me.puntmore.com/x')
  expect(siteFor('me')).toBe('puntmore')
})

test('me is the one host the API names, port and case aside', () => {
  setMeHost('Me.Puntmore.com', 'puntmore.com')
  expect(siteFor('ME.puntmore.com')).toBe('me')
  expect(siteFor('me.localhost')).toBe('puntmore')
})

test('with no me host every tool is the main site\'s and nothing points at me', () => {
  setMeHost('', 'me.puntmore.com')
  expect(site.SITE).toBe('puntmore')
  expect(site.SITE_NAME).toBe('Puntmore')
  expect(siteFor('me.puntmore.com')).toBe('puntmore')
  for (const t of ['finder', 'pnl', 'kelly', 'boost', 'shop', 'coupons', 'cards']) expect(toolSite(t)).toBe('puntmore')
  const grant = ['coupons', 'cards', 'finder', 'kelly']
  expect(siteTools(grant, 'puntmore')).toEqual(grant)
  expect(siteTools(grant, 'me')).toEqual([])
})

test('each site shows only its own tools', () => {
  setMeHost('me.puntmore.com', 'puntmore.com')
  const grant = ['coupons', 'cards', 'finder', 'kelly']
  expect(siteTools(grant, 'me')).toEqual(['finder', 'kelly'])
  expect(siteTools(grant, 'puntmore')).toEqual(['coupons', 'cards'])
})
