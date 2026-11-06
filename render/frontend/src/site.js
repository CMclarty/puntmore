// Which site this page is: Puntmore, Shalgo (the props side — Paddy and
// Hills) or Me (Callum's own tools, Finder first). Same app, API and accounts;
// Shalgo and Me each have their own address, name, icon and login, and each
// site shows only its own tools. A link to another site's tool goes to the
// site that owns it, same path. MIRRORS api/app/site.py: a host whose first
// label is "shalgo" is Shalgo, "me" is Me (shalgo.puntmore.com,
// me.puntmore.com; shalgo.localhost, me.localhost in development).
export const PUNTMORE = 'puntmore'
export const SHALGO = 'shalgo'
export const ME = 'me'
export const SHALGO_TOOLS = ['cards', 'hills']
export const ME_TOOLS = ['finder']

export function siteFor(hostname) {
  const name = (hostname || '').toLowerCase()
  const label = name.includes('.') ? name.split('.')[0] : ''
  return label === SHALGO || label === ME ? label : PUNTMORE
}

export const SITE = siteFor(typeof window === 'undefined' ? '' : window.location.hostname)
const NAMES = { [PUNTMORE]: 'Puntmore', [SHALGO]: 'Shalgo', [ME]: 'Me' }
export const SITE_NAME = NAMES[SITE]

export const toolSite = (tool) => (
  SHALGO_TOOLS.includes(tool) ? SHALGO : ME_TOOLS.includes(tool) ? ME : PUNTMORE
)

// The tools of a grant that this site shows.
export const siteTools = (tools = [], site = SITE) => tools.filter(t => toolSite(t) === site)

// The same path on `site`'s host: <host>, shalgo.<host> or me.<host>, from
// whichever of the three this page is on.
export function otherSiteUrl(path, site, loc = window.location) {
  const base = loc.hostname.replace(/^(www|shalgo|me)\./, '')
  const host = site === PUNTMORE ? base : `${site}.${base}`
  return `${loc.protocol}//${host}${loc.port ? `:${loc.port}` : ''}${path}`
}
