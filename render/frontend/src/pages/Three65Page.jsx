import { NavLink, Navigate, Outlet, useLocation } from 'react-router-dom'
import styles from './Three65Page.module.css'

/**
 * 365 — the bet365 offers, one tool with a tab per offer. Each tab is its own
 * page with its own permission; this shell only switches between the ones the
 * account can see. 2-Up (the engine, `accabonus` grant) is the live tab; the
 * acca boost returns as a tab on OddsPapi (PLAN.md item 11).
 */
export const TABS = [
  { path: '2up', label: '2-Up', tool: 'accabonus' },
  { path: 'scorers', label: 'Goalscorers', tool: 'goalscorers' },
]

export const tabsFor = (tools = []) => TABS.filter(t => tools.includes(t.tool))

export default function Three65Page({ tools = [] }) {
  const { pathname } = useLocation()
  const tabs = tabsFor(tools)
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
