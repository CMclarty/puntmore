import { describe, expect, it } from 'vitest'
import { identityFor } from './identity'

describe('identityFor', () => {
  it('gives each tool its own PNG home-screen icon, SVG favicon and manifest', () => {
    expect(identityFor('/paddy')).toEqual({
      title: 'Puntmore — Paddy', svg: '/icons/paddy.svg', png: '/icons/paddy-180.png',
      manifest: '/manifests/paddy.webmanifest',
    })
  })

  it('names the coupon tracker and the scraper page, not just the default', () => {
    expect(identityFor('/').png).toBe('/icons/coupons-180.png')
    expect(identityFor('/t/abc').title).toBe('Puntmore — Coupon')
    expect(identityFor('/scraper').manifest).toBe('/manifests/scraper.webmanifest')
  })

  it('keeps the boost icon on the virtuals manifest', () => {
    expect(identityFor('/virtuals')).toMatchObject({ png: '/icons/boost-180.png', manifest: '/manifests/virtuals.webmanifest' })
  })

  it('tells goalscorers apart from the rest of 365', () => {
    expect(identityFor('/365/scorers').png).toBe('/icons/goalscorers-180.png')
    expect(identityFor('/365/acca').png).toBe('/icons/accabonus-180.png')
  })

  it('falls back to the Puntmore identity elsewhere', () => {
    expect(identityFor('/login')).toEqual({
      title: 'Puntmore', svg: '/icon.svg', png: '/icons/puntmore-180.png', manifest: '/manifest.webmanifest',
    })
  })
})
