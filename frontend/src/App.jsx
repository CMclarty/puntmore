import { useState, useEffect, lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { getMe, logout as apiLogout } from './api'
import BottomNav, { landingFor } from './components/BottomNav'
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
const KellyPage = lazy(() => import('./pages/KellyPage'))
const BoostPage = lazy(() => import('./pages/BoostPage'))
const ShopPage = lazy(() => import('./pages/ShopPage'))
const AccaBonusPage = lazy(() => import('./pages/AccaBonusPage'))
const Three65Page = lazy(() => import('./pages/Three65Page'))
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

  const protectTool = (el, tool) => (
    <Protected user={user} tool={tool} onLogout={handleLogout}>{el}</Protected>
  )

  return (
    <BrowserRouter>
      <PageIdentity />
      <main className={`${styles.main} ${IS_FOCUSED ? styles.focused : ''}`}>
        <Suspense fallback={<div className={styles.boot}>Loading…</div>}>
        <Routes>
          <Route path="/login" element={<LoginRoute user={user} onLogin={setUser} />} />
          {/* The tracker opens on its landing page; /t/<code> is one coupon. */}
          <Route path="/" element={protectTool(<TrackerPage />, 'coupons')} />
          <Route path="/t/:code" element={protectTool(<TrackerPage />, 'coupons')} />
          {/* One ISSUE of a type — the midweek GG Bonus while the weekend one
              is also up. Without this every issue of a type shared one page
              and one combination, so only the newest was readable. */}
          <Route path="/t/:code/:couponId" element={protectTool(<TrackerPage />, 'coupons')} />
          <Route path="/upload" element={protectTool(<UploadPage user={user} />, 'coupons')} />
          {/* Goalscorers lives under 365 now (Oct 2026); old links land there. */}
          <Route path="/goalscorers" element={<Navigate to="/365/scorers" replace />} />
          {/* Two independent ledgers, one per racing category. Bare /pnl lands
              on dogs, the ledger that existed before the split. */}
          <Route path="/pnl" element={<Navigate to="/pnl/dogs" replace />} />
          <Route path="/pnl/dogs" element={protectTool(<PnlPage category="dogs" />, 'pnl')} />
          <Route path="/pnl/virtuals" element={protectTool(<PnlPage category="virtuals" />, 'pnl')} />
          {/* The Game Boy battle skin is GONE (Aug 2026) — see PLAN.md, it comes
              back once the tracker itself is finished. Its paths stay as
              redirects, not 404s: the skin was installable to a phone's home
              screen, so an already-installed shortcut still opens the ledger it
              was a skin OF rather than a dead page. */}
          <Route path="/pnl/battle" element={<Navigate to="/pnl/dogs" replace />} />
          <Route path="/pnl/dogs/battle" element={<Navigate to="/pnl/dogs" replace />} />
          <Route path="/pnl/virtuals/battle" element={<Navigate to="/pnl/virtuals" replace />} />
          <Route path="/kelly" element={protectTool(<KellyPage />, 'kelly')} />
          <Route path="/boost" element={protectTool(<BoostPage />, 'boost')} />
          <Route path="/shop" element={protectTool(<ShopPage />, 'shop')} />
          {/* 365: the bet365 offers, a tab each. /accabonus and /tennis are
              the acca boost's old addresses. */}
          <Route path="/365" element={<Three65Page tools={user?.tools} />}>
            <Route path="acca" element={protectTool(<AccaBonusPage />, 'accabonus')} />
            <Route path="scorers" element={protectTool(<GoalscorersPage />, 'goalscorers')} />
          </Route>
          <Route path="/accabonus" element={<Navigate to="/365/acca" replace />} />
          <Route path="/tennis" element={<Navigate to="/365/acca" replace />} />
          <Route path="/paddy" element={protectTool(<CardsPage />, 'cards')} />
          <Route path="/paddy/acca" element={<Navigate to="/paddy" replace />} />
          {/* Renamed from Cards (Oct 2026): installed shells and bookmarks
              still open /cards. */}
          <Route path="/cards" element={<Navigate to="/paddy" replace />} />
        </Routes>
        </Suspense>
      </main>
      {user && !IS_FOCUSED && <BottomNav onLogout={handleLogout} username={user.username} tools={user.tools} />}
    </BrowserRouter>
  )
}

/**
 * Gate a route on a tool grant: not logged in → login; logged in but not
 * granted this tool → the no-access notice (never a redirect to another guarded
 * route, which would loop for a user granted nothing).
 *
 * The redirect to /login carries the page that was asked for, because `replace`
 * drops the history entry it stands on — without this the destination is gone
 * before the password is typed, and every login ended on the tracker's landing
 * page no matter which tool was opened.
 */
function Protected({ user, tool, onLogout, children }) {
  const location = useLocation()
  if (!user) {
    // In the URL rather than router state: state is lost on a reload, and a
    // phone reloads a backgrounded page whenever it feels like it. The API
    // layer's own redirect (api.js) can only use the URL anyway, so one
    // mechanism serves both.
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?next=${next}`} replace />
  }
  if (!user.tools?.includes(tool)) return <NoAccess onLogout={onLogout} />
  return children
}

/**
 * The login page, and where a signed-in visitor to it goes: back to whatever
 * `Protected` was guarding, or — on a direct visit to /login, where there is no
 * such page — the first tool the account can see.
 *
 * The destination rides in `?next=`, set by whichever guard sent you here —
 * the in-app one (Protected) or the API layer's full page load when a session
 * lapses mid-use. Both use the URL, so a reload on the login page keeps it.
 */
function LoginRoute({ user, onLogin }) {
  const location = useLocation()
  if (!user) return <LoginPage onLogin={onLogin} />
  const next = new URLSearchParams(location.search).get('next')
  return <Navigate to={returnTo(next) || landingFor(user.tools)} replace />
}

/**
 * A return path is only honoured if it is a path on this site: one leading
 * slash (`//host` and `https://host` are somewhere else entirely), and not
 * /login itself, which would bounce straight back here.
 */
function returnTo(from) {
  if (typeof from !== 'string' || !from.startsWith('/') || from.startsWith('//')) return null
  return from === '/login' || from.startsWith('/login?') ? null : from
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
