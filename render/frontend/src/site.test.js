import { expect, test } from 'vitest'
import { siteFor, siteTools, toolSite, otherSiteUrl } from './site'

test('me is the third site and finder lives there', () => {
  expect(siteFor('me.puntmore.com')).toBe('me')
  expect(siteFor('shalgo.puntmore.com')).toBe('shalgo')
  expect(siteFor('puntmore.com')).toBe('puntmore')
  expect(toolSite('finder')).toBe('me')
  expect(toolSite('cards')).toBe('shalgo')
  expect(toolSite('coupons')).toBe('puntmore')
  const loc = { protocol: 'https:', hostname: 'puntmore.com', port: '' }
  expect(otherSiteUrl('/finder', 'me', loc)).toBe('https://me.puntmore.com/finder')
  expect(otherSiteUrl('/paddy', 'shalgo', { ...loc, hostname: 'me.puntmore.com' })).toBe('https://shalgo.puntmore.com/paddy')
})

test('a link to the main site drops the subdomain, and the port survives', () => {
  const loc = { protocol: 'http:', hostname: 'me.localhost', port: '5173' }
  expect(otherSiteUrl('/', 'puntmore', loc)).toBe('http://localhost:5173/')
  expect(otherSiteUrl('/x', 'me', { ...loc, hostname: 'www.puntmore.com', port: '' })).toBe('http://me.puntmore.com/x')
  expect(siteFor('me')).toBe('puntmore')
})

test('each site shows only its own tools', () => {
  const grant = ['coupons', 'cards', 'finder', 'kelly']
  expect(siteTools(grant, 'me')).toEqual(['finder'])
  expect(siteTools(grant, 'shalgo')).toEqual(['cards'])
  expect(siteTools(grant, 'puntmore')).toEqual(['coupons', 'kelly'])
})
