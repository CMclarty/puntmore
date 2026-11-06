// Which site this page is: the main domain (Puntmore, every public tool) or
// Me (Callum's personal tools). Same app, API and accounts; Me has its own
// address, name, icon and login, and each site shows only its own tools. A
// link to the other site's tool goes there, same path. MIRRORS
// api/app/site.py.
//
// Me exists only when the API's ME_HOST names its host, which the app reads
// at boot from GET /api/auth/site (App.jsx calls setMeHost before anything
// renders): a page on exactly that host is Me. Blank (the default, until
// me.puntmore.com resolves) there is one site, and every tool, the personal
// ones included, is the main domain's. The retired shalgo host is redirected
// to the main domain by the server before this page loads.
export const PUNTMORE = 'puntmore'
export const ME = 'me'
export const ME_TOOLS = ['finder', 'pnl', 'kelly', 'boost', 'shop']
const NAMES = { [PUNTMORE]: 'Puntmore', [ME]: 'Me' }

const hostName = (host) => (host || '').split(':')[0].trim().toLowerCase()
const pageHostname = () => (typeof window === 'undefined' ? '' : window.location.hostname)

let meHost = ''

export function siteFor(hostname, me = meHost) {
  return me && hostName(hostname) === me ? ME : PUNTMORE
}

// Live bindings: set by setMeHost, read wherever they are imported.
export let SITE = PUNTMORE
export let SITE_NAME = NAMES[PUNTMORE]

// Take the API's ME_HOST ("" = no Me) and settle which site this page is.
export function setMeHost(host, hostname = pageHostname()) {
  meHost = hostName(host)
  SITE = siteFor(hostname)
  SITE_NAME = NAMES[SITE]
}

export const toolSite = (tool) => (meHost && ME_TOOLS.includes(tool) ? ME : PUNTMORE)

// The tools of a grant that this site shows.
export const siteTools = (tools = [], site = SITE) => tools.filter(t => toolSite(t) === site)

// The same path on `site`'s host: Me's host, or the main domain, which is Me's
// host less its first label.
export function otherSiteUrl(path, site, loc = window.location) {
  const here = hostName(loc.hostname).replace(/^www\./, '')
  const main = here === meHost ? here.split('.').slice(1).join('.') : here
  const host = site === ME ? meHost : main
  return `${loc.protocol}//${host}${loc.port ? `:${loc.port}` : ''}${path}`
}
