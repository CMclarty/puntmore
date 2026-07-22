import { Link, useLocation } from 'react-router-dom'
import styles from './BottomNav.module.css'

const TABS = [
  {
    tool: 'coupons',
    label: 'Coupon',
    to: '/',
    matches: (p) => p === '/' || p.startsWith('/upload'),
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="2" width="16" height="18" rx="2"/>
        <line x1="7" y1="7" x2="15" y2="7"/>
        <line x1="7" y1="11" x2="15" y2="11"/>
        <line x1="7" y1="15" x2="11" y2="15"/>
      </svg>
    ),
  },
  {
    tool: 'goalscorers',
    label: 'Scorers',
    to: '/goalscorers',
    matches: (p) => p.startsWith('/goalscorers'),
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="7"/>
        <polygon points="11,7 13.4,9 12.5,12 9.5,12 8.6,9"/>
      </svg>
    ),
  },
  {
    // Hidden for now — coupon-tracker-only mode (PLAN.md v5). Definition kept;
    // remove `hidden` to bring the tab back (also re-enable its API router).
    hidden: true,
    tool: 'accabonus',
    label: 'AccaBonus',
    to: '/accabonus',
    matches: (p) => p.startsWith('/accabonus') || p.startsWith('/tennis'),
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="4,17 11,13 18,17"/>
        <polyline points="4,12 11,8 18,12"/>
        <polyline points="4,7 11,3 18,7"/>
      </svg>
    ),
  },
  {
    // Hidden for now — coupon-tracker-only mode (PLAN.md v5). See above.
    hidden: true,
    tool: 'cards',
    label: 'C/C',
    to: '/cards',
    matches: (p) => p.startsWith('/cards'),
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="4,4 4,18 18,18"/>
        <polyline points="4,4 10,4 10,10 16,10 16,18"/>
      </svg>
    ),
  },
]

export default function BottomNav({ tools = [] }) {
  const { pathname } = useLocation()
  // A tab shows only if it isn't globally hidden (PLAN.md v5) AND this user is
  // granted its tool. The server already intersects the grant with the enabled
  // tools, so `tools` never contains a globally-disabled tool.
  const visible = TABS.filter(tab => !tab.hidden && tools.includes(tab.tool))
  return (
    <nav className={styles.nav}>
      {visible.map(tab => {
        const active = tab.matches(pathname)
        return (
          <Link
            key={tab.to}
            to={tab.to}
            className={`${styles.tab}${active ? ` ${styles.active}` : ''}`}
          >
            <span className={styles.icon}>{tab.icon}</span>
            <span className={styles.label}>{tab.label}</span>
          </Link>
        )
      })}
    </nav>
  )
}
