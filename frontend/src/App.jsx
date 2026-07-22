import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { getMe, logout as apiLogout } from './api'
import BottomNav from './components/BottomNav'
import TrackerPage from './pages/TrackerPage'
import UploadPage from './pages/UploadPage'
import GoalscorersPage from './pages/GoalscorersPage'
import PnlPage from './pages/PnlPage'
import AccaBonusPage from './pages/AccaBonusPage'
import CardsPage from './pages/CardsPage'
import LoginPage from './pages/LoginPage'
import styles from './App.module.css'
// AccaBonusPage and CardsPage are surfaced only to a grant that includes them
// (i.e. "*"), reached via the nav's "More" sheet. Their feeds are empty until
// ODDS_API_KEY is set / the cards client is rebuilt (docs/provider-verification).
// tracker below. Re-import and restore the routes to bring them back.

export default function App() {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  async function handleLogout() {
    try { await apiLogout() } catch { /* ignore */ }
    setUser(null)
  }

  if (loading) return null

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
      <main className={styles.main}>
        <Routes>
          <Route
            path="/login"
            element={user ? <Navigate to="/" replace /> : <LoginPage onLogin={setUser} />}
          />
          <Route path="/" element={protectTool(<TrackerPage />, 'coupons')} />
          <Route path="/upload" element={protectTool(<UploadPage user={user} />, 'coupons')} />
          <Route path="/goalscorers" element={protectTool(<GoalscorersPage />, 'goalscorers')} />
          <Route path="/pnl" element={protectTool(<PnlPage />, 'pnl')} />
          <Route path="/accabonus" element={protectTool(<AccaBonusPage />, 'accabonus')} />
          <Route path="/tennis" element={<Navigate to="/accabonus" replace />} />
          <Route path="/cards" element={protectTool(<CardsPage />, 'cards')} />
        </Routes>
      </main>
      {user && <BottomNav onLogout={handleLogout} username={user.username} tools={user.tools} />}
    </BrowserRouter>
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
