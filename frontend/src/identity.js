// Home-screen identity per tool: iOS/Android read the icon, title and
// manifest present in the document at the moment "Add to Home Screen" is
// used, so with one shared icon.svg every tool looked identical once pinned.
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
const IDENTITIES = [
  { match: (p) => p === '/' || p.startsWith('/t/'), title: 'Puntmore — Coupon', icon: 'coupons' },
  { match: (p) => p === '/upload', title: 'Puntmore — Upload', icon: 'upload' },
  { match: (p) => p === '/pnl/dogs', title: 'P&L — Dogs', icon: 'pnl-dogs' },
  { match: (p) => p === '/virtuals', title: 'Puntmore — Virtuals', icon: 'boost', manifest: 'virtuals' },
  { match: (p) => p === '/kelly', title: 'Puntmore — Kelly', icon: 'kelly' },
  { match: (p) => p === '/aw26', title: 'Puntmore — AW 26', icon: 'aw26' },
  { match: (p) => p === '/shop', title: 'Puntmore — Shop Visits', icon: 'shop' },
  { match: (p) => p === '/365/scorers', title: 'Puntmore — Goalscorers', icon: 'goalscorers' },
  { match: (p) => p.startsWith('/365') || p === '/accabonus', title: 'Puntmore — 365', icon: 'accabonus' },
  { match: (p) => p.startsWith('/paddy') || p === '/cards', title: 'Puntmore — Paddy', icon: 'paddy' },
  { match: (p) => p === '/scraper', title: 'Puntmore — Scraper', icon: 'scraper' },
]

const DEFAULT_IDENTITY = { title: 'Puntmore', icon: 'puntmore' }

// The identity for a path: title plus the three files the head points at.
export function identityFor(pathname) {
  const { title, icon, manifest = icon } = IDENTITIES.find((i) => i.match(pathname)) || DEFAULT_IDENTITY
  return {
    title,
    svg: icon === 'puntmore' ? '/icon.svg' : `/icons/${icon}.svg`,
    png: `/icons/${icon}-180.png`,
    manifest: icon === 'puntmore' ? '/manifest.webmanifest' : `/manifests/${manifest}.webmanifest`,
  }
}
