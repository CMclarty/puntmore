import { Link, useLocation } from 'react-router-dom'
import styles from './BottomNav.module.css'

const TABS = [
  {
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
    label: 'Tennis',
    to: '/tennis',
    matches: (p) => p.startsWith('/tennis'),
    icon: (
      <svg width="22" height="22" viewBox="0 0 22 22" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
        <circle cx="11" cy="11" r="8"/>
        <path d="M3.5 11c2.5-2.5 2.5-5.5 0-8M18.5 11c-2.5 2.5-2.5 5.5 0 8"/>
      </svg>
    ),
  },
  {
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

export default function BottomNav() {
  const { pathname } = useLocation()
  return (
    <nav className={styles.nav}>
      {TABS.map(tab => {
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
