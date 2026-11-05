import { NavLink, Navigate, Outlet, useLocation } from 'react-router-dom'
import styles from './Three65Page.module.css'

/**
 * 365 — the bet365 offers, one tool with a tab per offer. Each tab is its own
 * page with its own permission; this shell only switches between the ones the
 * account can see. The acca boost returns as a tab on OddsPapi and 2-Up joins
 * once its feed exists (PLAN.md item 11).
 */
export const TABS = [
  { path: 'scorers', label: 'Goalscorers', tool: 'goalscorers' },
]

export default function Three65Page({ tools = [] }) {
  const { pathname } = useLocation()
  const tabs = TABS.filter(t => tools.includes(t.tool))
  if (pathname.replace(/\/$/, '') === '/365') {
    return <Navigate to={tabs.length ? `/365/${tabs[0].path}` : '/'} replace />
  }
  return (
    <>
      {tabs.length > 1 && (
        <nav className={styles.tabs} aria-label="365 offers">
          {tabs.map(t => (
            <NavLink key={t.path} to={`/365/${t.path}`}
              className={({ isActive }) => `${styles.tab} ${isActive ? styles.on : ''}`}>
              {t.label}
            </NavLink>
          ))}
        </nav>
      )}
      <Outlet />
    </>
  )
}
