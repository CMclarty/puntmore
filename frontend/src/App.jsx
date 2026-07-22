import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { getMe, logout as apiLogout } from './api'
import BottomNav from './components/BottomNav'
import TrackerPage from './pages/TrackerPage'
import UploadPage from './pages/UploadPage'
import GoalscorersPage from './pages/GoalscorersPage'
import PnlPage from './pages/PnlPage'
import LoginPage from './pages/LoginPage'
import styles from './App.module.css'
// AccaBonusPage and CardsPage are hidden for now (coupon-tracker-only mode,
// PLAN.md v5). Their pages/API stay in the repo; their routes redirect to the
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
          {/* Hidden tools — redirect to the tracker (PLAN.md v5). */}
          <Route path="/accabonus" element={<Navigate to="/" replace />} />
          <Route path="/tennis" element={<Navigate to="/" replace />} />
          <Route path="/cards" element={<Navigate to="/" replace />} />
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
