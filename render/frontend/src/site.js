// Which site this page is: the main domain (Puntmore, every public tool) or
// Me (Callum's personal tools). Same app, API and accounts; Me has its own
// address, name, icon and login, and each site shows only its own tools. A
// link to the other site's tool goes there, same path. MIRRORS
// api/app/site.py: a host whose first label is "me" is Me (me.puntmore.com;
// me.localhost in development). The retired shalgo host is redirected to the
// main domain by the server before this page loads.
export const PUNTMORE = 'puntmore'
export const ME = 'me'
export const ME_TOOLS = ['finder', 'pnl', 'kelly', 'boost', 'shop']

export function siteFor(hostname) {
  const name = (hostname || '').toLowerCase()
  const label = name.includes('.') ? name.split('.')[0] : ''
  return label === ME ? ME : PUNTMORE
}

export const SITE = siteFor(typeof window === 'undefined' ? '' : window.location.hostname)
const NAMES = { [PUNTMORE]: 'Puntmore', [ME]: 'Me' }
export const SITE_NAME = NAMES[SITE]

export const toolSite = (tool) => (ME_TOOLS.includes(tool) ? ME : PUNTMORE)

// The tools of a grant that this site shows.
export const siteTools = (tools = [], site = SITE) => tools.filter(t => toolSite(t) === site)

// The same path on `site`'s host: <host> or me.<host>, from whichever of the
// two this page is on.
export function otherSiteUrl(path, site, loc = window.location) {
  const base = loc.hostname.replace(/^(www|me)\./, '')
  const host = site === PUNTMORE ? base : `${site}.${base}`
  return `${loc.protocol}//${host}${loc.port ? `:${loc.port}` : ''}${path}`
}
