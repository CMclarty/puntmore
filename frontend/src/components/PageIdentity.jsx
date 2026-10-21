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
  { match: (p) => p === '/pnl/dogs', title: 'P&L — Dogs', icon: '/icons/pnl-dogs.svg', manifest: '/manifests/pnl-dogs.webmanifest' },
  { match: (p) => p === '/virtuals', title: 'Puntmore — Virtuals', icon: '/icons/boost.svg', manifest: '/manifests/virtuals.webmanifest' },
  { match: (p) => p === '/kelly', title: 'Puntmore — Kelly', icon: '/icons/kelly.svg', manifest: '/manifests/kelly.webmanifest' },
  { match: (p) => p === '/aw26', title: 'Puntmore — AW 26', icon: '/icons/aw26.svg', manifest: '/manifests/aw26.webmanifest' },
  { match: (p) => p === '/shop', title: 'Puntmore — Shop Visits', icon: '/icons/shop.svg', manifest: '/manifests/shop.webmanifest' },
  { match: (p) => p.startsWith('/365'), title: 'Puntmore — 365', icon: '/icons/accabonus.svg', manifest: '/manifests/accabonus.webmanifest' },
  { match: (p) => p === '/paddy' || p === '/cards', title: 'Puntmore — Paddy', icon: '/icons/paddy.svg', manifest: '/manifests/paddy.webmanifest' },
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
