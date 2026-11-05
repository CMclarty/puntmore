import { useState, useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import styles from './BottomNav.module.css'

// Order here is the list order. `sub` is a static one-word descriptor (no live
// fetch in the nav); `muted` dims a tool with no live data yet; `priv` marks a
// personal tool. Only tools in the user's grant appear.
const TOOLS = [
  {
    tool: 'coupons', name: 'Coupon', to: '/', sub: 'Tracker',
    matches: (p) => p === '/' || p.startsWith('/t/') || p.startsWith('/upload'),
    icon: (
      <svg viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <rect x="4" y="2" width="14" height="18" rx="2"/><line x1="7" y1="7" x2="15" y2="7"/><line x1="7" y1="11" x2="15" y2="11"/><line x1="7" y1="15" x2="11" y2="15"/>
      </svg>
    ),
  },
  {
    tool: 'pnl', name: 'Dogs', to: '/pnl/dogs', sub: 'Ledger', priv: true,
    matches: (p) => p.startsWith('/pnl') && !p.startsWith('/pnl/virtuals'),
    icon: (
      <svg viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="3,15 8,10 12,13 19,4"/><polyline points="14,4 19,4 19,9"/>
      </svg>
    ),
  },
  {
    tool: 'kelly', name: 'Kelly', to: '/kelly', sub: 'Staking',
    matches: (p) => p.startsWith('/kelly'),
    icon: (
      <svg viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <rect x="5" y="2.5" width="12" height="17" rx="2"/><line x1="8" y1="6" x2="14" y2="6"/>
        <circle cx="8.5" cy="11" r="0.7" fill="currentColor" stroke="none"/><circle cx="13.5" cy="11" r="0.7" fill="currentColor" stroke="none"/>
        <circle cx="8.5" cy="14.5" r="0.7" fill="currentColor" stroke="none"/><circle cx="13.5" cy="14.5" r="0.7" fill="currentColor" stroke="none"/>
      </svg>
    ),
  },
  {
    // The boost and the virtuals ledger, one page; shown to either grant.
    tool: 'boost', anyOf: ['pnl', 'boost'], name: 'Virtuals', to: '/virtuals', sub: 'Boost',
    matches: (p) => p.startsWith('/virtuals') || p.startsWith('/boost') || p.startsWith('/pnl/virtuals'),
    icon: (
      <svg viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="5,12 11,6 17,12"/><polyline points="5,17 11,11 17,17"/>
      </svg>
    ),
  },
  {
    // One entry for every bet365 offer; shown to a grant for any of them.
    tool: 'accabonus', anyOf: ['accabonus', 'goalscorers'], name: '365', to: '/365', sub: 'Offers',
    matches: (p) => p.startsWith('/365') || p.startsWith('/accabonus') || p.startsWith('/tennis') || p.startsWith('/goalscorers'),
    icon: (
      <svg viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="4,17 11,12 18,17"/><polyline points="4,12 11,7 18,12"/>
      </svg>
    ),
  },
  {
    tool: 'cards', name: 'Paddy', to: '/paddy', sub: 'Props', muted: true,
    matches: (p) => p.startsWith('/paddy') || p.startsWith('/cards'),
    icon: (
      <svg viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="4,4 4,18 18,18"/><polyline points="4,4 10,4 10,10 16,10 16,18"/>
      </svg>
    ),
  },
  {
    tool: 'hills', name: 'Hills', to: '/hills', sub: 'Props', muted: true,
    matches: (p) => p.startsWith('/hills'),
    icon: (
      <svg viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="2,17 8,8 12,13 15,9 20,17"/><line x1="2" y1="17" x2="20" y2="17"/>
      </svg>
    ),
  },
  {
    tool: 'scraper', name: 'Scraper', to: '/scraper', sub: 'Control',
    matches: (p) => p.startsWith('/scraper'),
    icon: (
      <svg viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        <line x1="4" y1="6" x2="18" y2="6"/><line x1="4" y1="11" x2="18" y2="11"/><line x1="4" y1="16" x2="18" y2="16"/>
        <circle cx="8" cy="6" r="1.6"/><circle cx="14" cy="11" r="1.6"/><circle cx="10" cy="16" r="1.6"/>
      </svg>
    ),
  },
]

/**
 * Where an account lands when there's no particular page to return to — the
 * first tool in nav order that it can actually see. "/" is the coupon tracker,
 * so an account without that grant must NOT be sent there: it would meet the
 * no-access notice on login and read as a permissions failure.
 */
const granted_ = (t, tools) => (t.anyOf || [t.tool]).some(x => tools.includes(x))

export function landingFor(tools = []) {
  return (TOOLS.find(t => granted_(t, tools)) || TOOLS[0]).to
}

export default function BottomNav({ tools = [] }) {
  const { pathname } = useLocation()
  const [open, setOpen] = useState(false)

  // Escape closes it, like any other popover. The scrim covers a tap outside;
  // this covers a keyboard, where there is nothing to tap.
  useEffect(() => {
    if (!open) return
    const onKey = e => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  const granted = TOOLS.filter(t => granted_(t, tools))
  // One tool (or none) needs no switcher — you're already on it; direct URL works.
  if (granted.length < 2) return null

  const active = granted.find(t => t.matches(pathname)) || granted[0]

  return (
    <div className={`${styles.dock} ${open ? styles.open : ''}`}>
      <div className={styles.scrim} onClick={() => setOpen(false)} />
      {/* Every granted tool, current one included and marked — a switcher that
          hides the tool you're on reads as a list with a hole in it, and the
          row order then shifts depending on where you happen to be. */}
      <nav className={styles.list} aria-hidden={!open} aria-label="Tools">
        {granted.map(t => (
          <Link
            key={t.tool}
            to={t.to}
            tabIndex={open ? 0 : -1}
            onClick={() => setOpen(false)}
            aria-current={t === active ? 'page' : undefined}
            className={`${styles.row} ${t.muted ? styles.muted : ''} ${t === active ? styles.rowOn : ''}`}
          >
            <span className={styles.rowIc}>{t.icon}</span>
            <span className={styles.rowNm}>{t.name}</span>
            <span className={styles.rowSub}>{t.sub}</span>
            {t.priv && <span className={styles.priv}>yours</span>}
          </Link>
        ))}
      </nav>
      <button
        type="button"
        className={styles.anchor}
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-label={`Current tool: ${active.name}. Tap to switch tools.`}
      >
        <span className={styles.anchorIc}>{active.icon}</span>
        <span className={styles.anchorNm}>{active.name}</span>
        <span className={`${styles.chev} ${open ? styles.chevOpen : ''}`}>
          <svg viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="6,13 11,8 16,13"/>
          </svg>
        </span>
      </button>
    </div>
  )
}
