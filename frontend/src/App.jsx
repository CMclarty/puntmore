import { useState, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { getMe, logout as apiLogout } from './api'
import BottomNav from './components/BottomNav'
import TrackerPage from './pages/TrackerPage'
import UploadPage from './pages/UploadPage'
import ArbsPage from './pages/ArbsPage'
import CardsPage from './pages/CardsPage'
import LoginPage from './pages/LoginPage'
import styles from './App.module.css'

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
          <Route path="/tennis" element={protect(<ArbsPage />)} />
          <Route path="/cards" element={protect(<CardsPage />)} />
        </Routes>
      </main>
      {user && <BottomNav onLogout={handleLogout} username={user.username} />}
    </BrowserRouter>
  )
}
