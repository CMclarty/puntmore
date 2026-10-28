import { PUNTMORE, SHALGO, SITE } from './site'

// Home-screen identity per tool. The icon and title are read when "Add to
// Home Screen" is tapped, but iOS reads the MANIFEST (start_url, id) at page
// load, before this runs — so the server rewrites the shell's head for the
// path it serves (api/app/identity.py mirrors this table; a test keeps them
// in step) and this keeps it right across client-side navigation.
// scope stays "/" for all of them — these are entry points into the same
// standalone app, not separate scopes; only start_url/icon/name differ, so
// navigating to another tool from an installed shortcut stays in-app.
//
// iOS takes only a PNG apple-touch-icon (an SVG there means a page
// screenshot), so each tool has /icons/<name>-180.png beside its SVG — both
// drawn by frontend/scripts/icons.py. `icon` names the file; `manifest`
// only where the manifest's name differs from the icon's.
//
// Each manifest also carries its own `id` (= start_url). Without one, every
// shortcut was the SAME installed app (shared scope "/"), so tapping Paddy
// while Coupon was open in the background just brought Coupon forward — the
// "wrong page half the time". Only routes that render a page are listed; a
// route that redirects (battles, /pnl/virtuals, /boost) takes its target's.
//
// Two sites share the shell (site.js): Paddy, Hills, AW 26 and the scraper
// carry Shalgo's name, and a path with no tool of its own (/login, Shalgo's
// landing "/") takes the identity of the site it is on.
const IDENTITIES = [
  { match: (p) => p === '/' || p.startsWith('/t/'), title: 'Puntmore — Coupon', icon: 'coupons' },
  { match: (p) => p === '/upload', title: 'Puntmore — Upload', icon: 'upload' },
  { match: (p) => p === '/pnl/dogs', title: 'P&L — Dogs', icon: 'pnl-dogs' },
  { match: (p) => p === '/virtuals', title: 'Puntmore — Virtuals', icon: 'boost', manifest: 'virtuals' },
  { match: (p) => p === '/kelly', title: 'Puntmore — Kelly', icon: 'kelly' },
  { match: (p) => p === '/shop', title: 'Puntmore — Shop Visits', icon: 'shop' },
  { match: (p) => p === '/365/scorers', title: 'Puntmore — Goalscorers', icon: 'goalscorers' },
  { match: (p) => p.startsWith('/365') || p === '/accabonus', title: 'Puntmore — 365', icon: 'accabonus' },
  { match: (p) => p.startsWith('/paddy') || p === '/cards', title: 'Shalgo — Paddy', icon: 'paddy' },
  { match: (p) => p.startsWith('/hills'), title: 'Shalgo — Hills', icon: 'hills' },
  { match: (p) => p === '/scraper', title: 'Shalgo — Scraper', icon: 'scraper' },
]

const DEFAULTS = {
  [PUNTMORE]: { title: 'Puntmore', icon: 'puntmore' },
  [SHALGO]: { title: 'Shalgo', icon: 'shalgo' },
}

// The identity for a path on a site: title plus the three files the head points at.
export function identityFor(pathname, site = SITE) {
  const rows = site === SHALGO && pathname === '/' ? [] : IDENTITIES
  const { title, icon, manifest = icon } = rows.find((i) => i.match(pathname)) || DEFAULTS[site]
  return {
    title,
    svg: icon === 'puntmore' ? '/icon.svg' : `/icons/${icon}.svg`,
    png: `/icons/${icon}-180.png`,
    manifest: icon === 'puntmore' ? '/manifest.webmanifest' : `/manifests/${manifest}.webmanifest`,
  }
}
