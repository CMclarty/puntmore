import { useState, useEffect, useCallback } from 'react'
import { getUsers, addUser, setUserTools, removeUser } from '../api'
import { changed, isAdmin, toggle, toolName, toolsLine } from '../users'
import f from './CardsPage.module.css'
import s from './Aw26Page.module.css'
import u from './UsersPage.module.css'

// Users (on me, held only by a "*" account; the register, 25 Nov 2026): add a
// user, pick their tools, remove a user. The tick boxes are the API's
// `grantable` list; an admin ("*") account has every tool and no controls.

const BAND = '#004833'

function Ticks({ grantable, ticked, onToggle, disabled }) {
  return (
    <div className={u.ticks} role="group" aria-label="Tools">
      {grantable.map(t => (
        <label key={t} className={`${f.chip} ${u.tick}`}>
          <input type="checkbox" checked={ticked.includes(t)} disabled={disabled}
            onChange={() => onToggle(t)} />
          {toolName(t)}
        </label>
      ))}
    </div>
  )
}

function AddUser({ grantable, onAdded }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [ticked, setTicked] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await addUser(username, password, ticked)
      setUsername('')
      setPassword('')
      setTicked([])
      onAdded()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className={`${f.panel} ${u.form}`} onSubmit={submit}>
      <label className={s.field}>
        <span className={s.label}>Username</span>
        <input className={u.input} value={username} autoComplete="off"
          onChange={e => setUsername(e.target.value)} />
      </label>
      <label className={s.field}>
        <span className={s.label}>Password</span>
        <input className={u.input} type="password" value={password} autoComplete="new-password"
          onChange={e => setPassword(e.target.value)} />
      </label>
      <Ticks grantable={grantable} ticked={ticked} disabled={busy}
        onToggle={t => setTicked(cur => toggle(cur, t, grantable))} />
      <div className={`${s.actions} ${u.actions}`}>
        <button type="submit" className={`${s.save} ${u.right}`} disabled={busy}>Add</button>
      </div>
      {error && <p className={`${f.error} ${u.error}`}>{error}</p>}
    </form>
  )
}

function UserRow({ user, grantable, onChange }) {
  const admin = isAdmin(user)
  const [ticked, setTicked] = useState(user.tools)
  const [busy, setBusy] = useState(false)
  const [sure, setSure] = useState(false)
  const [error, setError] = useState(null)

  async function run(call) {
    setBusy(true)
    setError(null)
    try {
      await call()
      onChange()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={u.user}>
      <span className={f.what}>
        <span className={f.pick}>{user.username}</span>
        <span className={f.match}>{toolsLine(user)}</span>
      </span>
      {!admin && (
        <>
          <Ticks grantable={grantable} ticked={ticked} disabled={busy}
            onToggle={t => setTicked(cur => toggle(cur, t, grantable))} />
          <div className={`${s.actions} ${u.actions}`}>
            {sure ? (
              <>
                <button type="button" className={s.danger} disabled={busy}
                  onClick={() => run(() => removeUser(user.username))}>Remove it</button>
                <button type="button" className={s.ghost} disabled={busy}
                  onClick={() => setSure(false)}>Cancel</button>
              </>
            ) : (
              <>
                <button type="button" className={s.danger} disabled={busy}
                  onClick={() => setSure(true)}>Remove</button>
                <button type="button" className={`${s.save} ${u.right}`}
                  disabled={busy || !ticked.length || !changed(ticked, user.tools)}
                  onClick={() => run(() => setUserTools(user.username, ticked))}>Save</button>
              </>
            )}
          </div>
          {error && <p className={`${f.error} ${u.error}`}>{error}</p>}
        </>
      )}
    </div>
  )
}

export default function UsersPage() {
  const [data, setData] = useState(null)      // { users, grantable }: the last good load
  const [error, setError] = useState(null)    // the last load failed

  const load = useCallback(() => getUsers()
    .then(d => { setData(d); setError(null) }, err => setError(err.message)), [])
  useEffect(() => { load() }, [load])

  // The tool switcher below takes the band's colour while this page is open.
  useEffect(() => {
    const root = document.documentElement.style
    root.setProperty('--tool-band', BAND)
    root.setProperty('--dock-bg', '#fff')
    return () => { root.removeProperty('--tool-band'); root.removeProperty('--dock-bg') }
  }, [])

  return (
    <div className={f.page} style={{ '--band': BAND }}>
      <div className={f.band}>
        <div className={f.title}>
          <span className={f.name}>Users</span>
        </div>
      </div>
      <div className={f.list}>
        {error && <p className={f.error}>{error}</p>}
        {!data && !error && <p className={f.empty}>Loading…</p>}
        {data && (
          <>
            <AddUser grantable={data.grantable} onAdded={load} />
            {data.users.map(user => (
              // Keyed by the grant too, so a saved change resets the ticks.
              <UserRow key={`${user.username}|${user.permissions}`} user={user}
                grantable={data.grantable} onChange={load} />
            ))}
          </>
        )}
      </div>
    </div>
  )
}
