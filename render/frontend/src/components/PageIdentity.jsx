import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { identityFor } from '../identity'

// Points the document head (title, favicon, apple-touch-icon, manifest) at
// the current tool's identity — see src/identity.js for why and the list.
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
    const identity = identityFor(pathname)
    const { svg, png, manifest } = identity
    document.title = identity.title
    setLinkHref('icon', svg)
    setLinkHref('apple-touch-icon', png)
    setLinkHref('manifest', manifest)
    const appleTitle = document.querySelector('meta[name="apple-mobile-web-app-title"]')
    if (appleTitle) appleTitle.content = identity.title
  }, [pathname])

  return null
}
