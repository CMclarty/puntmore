// Which site this page is: Puntmore, or Shalgo (the props side — Paddy, Hills,
// AW 26 and the scraper). Same app, API and accounts; Shalgo has its own
// address, name, icon and login, and each site shows only its own tools. A
// link to the other site's tool goes there, same path. MIRRORS api/app/site.py:
// a host whose first label is "shalgo" is Shalgo (shalgo.puntmore.com,
// shalgo.localhost in development).
export const PUNTMORE = 'puntmore'
export const SHALGO = 'shalgo'
export const SHALGO_TOOLS = ['cards', 'hills', 'aw26', 'scraper']

export function siteFor(hostname) {
  const name = (hostname || '').toLowerCase()
  return name.includes('.') && name.split('.')[0] === SHALGO ? SHALGO : PUNTMORE
}

export const SITE = siteFor(typeof window === 'undefined' ? '' : window.location.hostname)
export const SITE_NAME = SITE === SHALGO ? 'Shalgo' : 'Puntmore'

export const toolSite = (tool) => (SHALGO_TOOLS.includes(tool) ? SHALGO : PUNTMORE)

// The tools of a grant that this site shows.
export const siteTools = (tools = [], site = SITE) => tools.filter(t => toolSite(t) === site)

// The same path on the other site's host: shalgo.<host> <-> <host>.
export function otherSiteUrl(path, loc = window.location) {
  const host = SITE === SHALGO
    ? loc.hostname.replace(/^shalgo\./, '')
    : `shalgo.${loc.hostname.replace(/^www\./, '')}`
  return `${loc.protocol}//${host}${loc.port ? `:${loc.port}` : ''}${path}`
}
