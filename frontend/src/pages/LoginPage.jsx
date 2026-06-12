import { useState } from 'react'
import { login } from '../api'
import styles from './LoginPage.module.css'

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const user = await login(username, password)
      onLogin(user)
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <div className={styles.page}>
      <form className={styles.form} onSubmit={handleSubmit}>
        <div className={styles.lockup}>
          <span className={styles.wordmark}>puntmore</span>
          <span className={styles.tagline}>your betting edge</span>
        </div>

        {error && <p className={styles.error}>{error}</p>}

        <div className={styles.field}>
          <label className={styles.label}>Email or username</label>
          <input
            className={styles.input}
            value={username}
            autoFocus
            autoCapitalize="none"
            autoComplete="username"
            onChange={e => setUsername(e.target.value)}
          />
        </div>

        <div className={styles.field}>
          <label className={styles.label}>Password</label>
          <input
            className={styles.input}
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={e => setPassword(e.target.value)}
          />
        </div>

        <button
          className={styles.btn}
          type="submit"
          disabled={busy || !username || !password}
        >
          {busy ? 'Signing in…' : 'LOG IN'}
        </button>
      </form>
    </div>
  )
}
