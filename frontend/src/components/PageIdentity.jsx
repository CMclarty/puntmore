import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'

// Home-screen identity per tool: iOS/Android read the icon, title and
// manifest present in the document at the moment "Add to Home Screen" is
// used, so with one shared icon.svg every tool looked identical once pinned.
// scope stays "/" for all of them — these are entry points into the same
// standalone app, not separate scopes; only start_url/icon/name differ, so
// navigating to another tool from an installed shortcut stays in-app.
const IDENTITIES = [
  { match: (p) => p === '/upload', title: 'Puntmore — Upload', icon: '/icons/upload.svg', manifest: '/manifests/upload.webmanifest' },
  { match: (p) => p === '/goalscorers', title: 'Puntmore — Goalscorers', icon: '/icons/goalscorers.svg', manifest: '/manifests/goalscorers.webmanifest' },
  { match: (p) => p === '/pnl/dogs', title: 'P&L — Dogs', icon: '/icons/pnl-dogs.svg', manifest: '/manifests/pnl-dogs.webmanifest' },
  { match: (p) => p === '/pnl/virtuals', title: 'P&L — Virtuals', icon: '/icons/pnl-virtuals.svg', manifest: '/manifests/pnl-virtuals.webmanifest' },
  { match: (p) => p === '/boost', title: 'Puntmore — Boost', icon: '/icons/boost.svg', manifest: '/manifests/boost.webmanifest' },
  { match: (p) => p === '/kelly', title: 'Puntmore — Kelly', icon: '/icons/kelly.svg', manifest: '/manifests/kelly.webmanifest' },
  { match: (p) => p === '/shop', title: 'Puntmore — Shop Visits', icon: '/icons/shop.svg', manifest: '/manifests/shop.webmanifest' },
  { match: (p) => p === '/accabonus', title: 'Puntmore — AccaBonus', icon: '/icons/accabonus.svg', manifest: '/manifests/accabonus.webmanifest' },
  { match: (p) => p === '/cards', title: 'Puntmore — Cards', icon: '/icons/cards.svg', manifest: '/manifests/cards.webmanifest' },
]

const DEFAULT_IDENTITY = { title: 'Puntmore', icon: '/icon.svg', manifest: '/manifest.webmanifest' }

function setLinkHref(rel, href) {
  let el = document.querySelector(`link[rel="${rel}"]`)
  if (!el) {
    el = document.createElement('link')
    el.rel = rel
    document.head.appendChild(el)
  }
  el.href = href
}

export default function PageIdentity() {
  const { pathname } = useLocation()

  useEffect(() => {
    const identity = IDENTITIES.find((i) => i.match(pathname)) || DEFAULT_IDENTITY
    document.title = identity.title
    setLinkHref('icon', identity.icon)
    setLinkHref('apple-touch-icon', identity.icon)
    setLinkHref('manifest', identity.manifest)
    const appleTitle = document.querySelector('meta[name="apple-mobile-web-app-title"]')
    if (appleTitle) appleTitle.content = identity.title
  }, [pathname])

  return null
}
