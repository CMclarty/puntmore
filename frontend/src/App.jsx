import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { getMe, logout as apiLogout } from './api'
import BottomNav from './components/BottomNav'
import TrackerPage from './pages/TrackerPage'
import UploadPage from './pages/UploadPage'
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

  const protect = (el) => (user ? el : <Navigate to="/login" replace />)

  return (
    <BrowserRouter>
      <main className={styles.main}>
        <Routes>
          <Route
            path="/login"
            element={user ? <Navigate to="/" replace /> : <LoginPage onLogin={setUser} />}
          />
          <Route path="/" element={protect(<TrackerPage />)} />
          <Route path="/upload" element={protect(<UploadPage user={user} />)} />
          {/* Hidden tools — redirect to the tracker (PLAN.md v5). */}
          <Route path="/accabonus" element={<Navigate to="/" replace />} />
          <Route path="/tennis" element={<Navigate to="/" replace />} />
          <Route path="/cards" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      {user && <BottomNav onLogout={handleLogout} username={user.username} />}
    </BrowserRouter>
  )
}
