import { useState, useEffect, lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { getMe, logout as apiLogout } from './api'
import BottomNav from './components/BottomNav'
import PageIdentity from './components/PageIdentity'
import TrackerPage from './pages/TrackerPage'
import LoginPage from './pages/LoginPage'
import styles from './App.module.css'

// The tracker and login are the entry points, so they stay in the main bundle.
// Everything else is a separate chunk fetched on first visit — a phone opening
// the tracker no longer downloads and parses the P&L chart code, the upload
// page or the two gated tools. They are small and same-origin, so the fetch is
// unnoticeable; Suspense covers it with the same wording as the boot state.
const UploadPage = lazy(() => import('./pages/UploadPage'))
const GoalscorersPage = lazy(() => import('./pages/GoalscorersPage'))
const PnlPage = lazy(() => import('./pages/PnlPage'))
const PnlBattlePage = lazy(() => import('./pages/PnlBattlePage'))
const KellyPage = lazy(() => import('./pages/KellyPage'))
const ShopPage = lazy(() => import('./pages/ShopPage'))
const AccaBonusPage = lazy(() => import('./pages/AccaBonusPage'))
const CardsPage = lazy(() => import('./pages/CardsPage'))

// The path the app was FIRST loaded at, captured once before the router starts
// navigating. A direct (hard) load of a focused route — e.g. saving /pnl to the
// home screen as a standalone ledger — shows just that tool with no switcher
// fan. Opening the app normally at / keeps the fan; reaching P&L via the fan is
// not a direct load, so this module-level capture stays on the entry URL.
const ENTRY_PATH = window.location.pathname
const FOCUSED_ROOTS = ['/pnl']
const IS_FOCUSED = FOCUSED_ROOTS.some(r => ENTRY_PATH === r || ENTRY_PATH.startsWith(r + '/'))

// AccaBonusPage and CardsPage are surfaced only to a grant that includes them
// (i.e. "*"), reached via the nav's "More" sheet. Their feeds are empty until
// ODDS_API_KEY is set / the cards client is rebuilt (docs/provider-verification).
// tracker below. Re-import and restore the routes to bring them back.

export default function App() {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [bootError, setBootError] = useState(null)

  useEffect(() => {
    getMe()
      .then(u => { setUser(u); setBootError(null) })
      .catch(e => {
        // A 401 is not an error — getMe returns null for it and we fall through
        // to the login route. Anything reaching here is the API being reachable
        // but unhealthy (502 mid-deploy) or the fetch failing outright, and it
        // must be said out loud: this used to render as a blank page, which is
        // indistinguishable from a bundle that never booted.
        setUser(null)
        setBootError(e?.message || 'Could not reach the server')
      })
      .finally(() => setLoading(false))
  }, [])

  async function handleLogout() {
    try { await apiLogout() } catch { /* ignore */ }
    setUser(null)
  }

  if (loading) return <div className={styles.boot}>Loading…</div>
  if (bootError) return <BootError detail={bootError} />

  const canSee = (tool) => Boolean(user?.tools?.includes(tool))

  // Gate a route on a tool grant: not logged in → login; logged in but not
  // granted this tool → the no-access notice (never a redirect to another
  // guarded route, which would loop for a user granted nothing).
  const protectTool = (el, tool) => {
    if (!user) return <Navigate to="/login" replace />
    if (!canSee(tool)) return <NoAccess onLogout={handleLogout} />
    return el
  }

  return (
    <BrowserRouter>
      <PageIdentity />
      <main className={`${styles.main} ${IS_FOCUSED ? styles.focused : ''}`}>
        <Suspense fallback={<div className={styles.boot}>Loading…</div>}>
        <Routes>
          <Route
            path="/login"
            element={user ? <Navigate to="/" replace /> : <LoginPage onLogin={setUser} />}
          />
          {/* The tracker opens on its landing page; /t/<code> is one coupon. */}
          <Route path="/" element={protectTool(<TrackerPage />, 'coupons')} />
          <Route path="/t/:code" element={protectTool(<TrackerPage />, 'coupons')} />
          <Route path="/upload" element={protectTool(<UploadPage user={user} />, 'coupons')} />
          <Route path="/goalscorers" element={protectTool(<GoalscorersPage />, 'goalscorers')} />
          {/* Two independent ledgers, one per racing category. Bare /pnl (and
              the old /pnl/battle) land on dogs, the ledger that existed before
              the split. */}
          <Route path="/pnl" element={<Navigate to="/pnl/dogs" replace />} />
          <Route path="/pnl/dogs" element={protectTool(<PnlPage category="dogs" />, 'pnl')} />
          <Route path="/pnl/virtuals" element={protectTool(<PnlPage category="virtuals" />, 'pnl')} />
          {/* Same ledgers, same API — a skin, behind its own path. */}
          <Route path="/pnl/battle" element={<Navigate to="/pnl/dogs/battle" replace />} />
          <Route path="/pnl/dogs/battle" element={protectTool(<PnlBattlePage category="dogs" />, 'pnl')} />
          <Route path="/pnl/virtuals/battle" element={protectTool(<PnlBattlePage category="virtuals" />, 'pnl')} />
          <Route path="/kelly" element={protectTool(<KellyPage />, 'kelly')} />
          <Route path="/shop" element={protectTool(<ShopPage />, 'shop')} />
          <Route path="/accabonus" element={protectTool(<AccaBonusPage />, 'accabonus')} />
          <Route path="/tennis" element={<Navigate to="/accabonus" replace />} />
          <Route path="/cards" element={protectTool(<CardsPage />, 'cards')} />
        </Routes>
        </Suspense>
      </main>
      {user && !IS_FOCUSED && <BottomNav onLogout={handleLogout} username={user.username} tools={user.tools} />}
    </BrowserRouter>
  )
}

// Shown when the app loads but the API doesn't answer healthily — most often
// the ~40s container swap on a deploy. Retry is a hard reload so it also drops
// any service-worker shell that's pinned to a build whose assets are gone.
function BootError({ detail }) {
  return (
    <div className={styles.noAccess}>
      <p>Couldn’t reach Puntmore. If a deploy is in progress this clears on its own in a minute.</p>
      <p className={styles.bootDetail}>{detail}</p>
      <button type="button" onClick={() => window.location.reload()}>Retry</button>
    </div>
  )
}

function NoAccess({ onLogout }) {
  return (
    <div className={styles.noAccess}>
      <p>Your account has no tools enabled. Ask an admin for access.</p>
      <button type="button" onClick={onLogout}>Log out</button>
    </div>
  )
}
