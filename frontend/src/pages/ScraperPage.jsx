import { useState, useEffect, useCallback } from 'react'
import {
  getScraper, putScraperLeague, putScraperMarket, putScraperTiming, postScraperLeague, deleteScraperLeague,
} from '../api'
import useAutoRefresh from '../hooks/useAutoRefresh'
import styles from './ScraperPage.module.css'

/**
 * The scraper's controls (routers/scraper.py): leagues, markets, per-site
 * timings, and the scraper's last status report. The scraper box re-reads
 * the config every 5 minutes and reports once a minute.
 *
 * Edits save on change — a toggle at once, a text or number field when it is
 * left (or on Enter). Every write answers with the whole state, so what is on
 * screen is what the server holds: timings come back clamped to the
 * scraper's floors, and a refused url is said out loud.
 */

const TIMINGS = [
  ['every_min', 'Every (m)', false],
  ['stale_min', 'Stale (m)', false],
  ['max_tabs', 'Tabs', true],
  ['open_gap_min', 'Gap (m)', true],
  ['relist_min', 'Relist (m)', true],
  ['tab_max_hours', 'Life (h)', true],
]

function when(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  const opts = { timeZone: 'Europe/London', hour: '2-digit', minute: '2-digit' }
  const today = new Date().toLocaleDateString('en-GB', { timeZone: 'Europe/London' })
  if (d.toLocaleDateString('en-GB', { timeZone: 'Europe/London' }) !== today) {
    opts.day = 'numeric'
    opts.month = 'short'
  }
  return d.toLocaleString('en-GB', opts)
}

export default function ScraperPage() {
  const [state, setState] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    getScraper()
      .then(s => { setState(s); setError(null) })
      .catch(e => setError(e?.message || 'Could not load'))
  }, [])
  useEffect(load, [load])
  useAutoRefresh(load, { everyMs: 60_000 })

  const save = (fn) => {
    setError(null)
    return fn().then(setState).catch(e => { setError(e?.message || 'Not saved'); load() })
  }

  if (!state) return <div className={styles.page}>{error ? <p className={styles.error}>{error}</p> : null}</div>
  const { leagues, markets, timings, status } = state

  return (
    <div className={styles.page}>
      {error && <p className={styles.error}>{error}</p>}

      <ul className={styles.rows}>
        {leagues.map(l => (
          <li key={l.id} className={styles.row}>
            <label className={styles.toggle}>
              <input type="checkbox" checked={l.enabled}
                onChange={e => save(() => putScraperLeague(l.id, { enabled: e.target.checked }))} />
              <span>{l.name}</span>
            </label>
            <Field value={l.url} aria-label={`${l.name} url`}
              onSave={v => save(() => putScraperLeague(l.id, { url: v }))} />
            <button type="button" className={styles.remove} aria-label={`Remove ${l.name}`}
              onClick={() => save(() => deleteScraperLeague(l.id))}>×</button>
          </li>
        ))}
        <li className={styles.row}>
          <AddLeague onAdd={fields => save(() => postScraperLeague(fields))} />
        </li>
      </ul>

      <ul className={styles.rows}>
        {markets.map(m => (
          <li key={m.key} className={styles.row}>
            <label className={styles.toggle}>
              <input type="checkbox" checked={m.enabled}
                onChange={e => save(() => putScraperMarket(m.key, { enabled: e.target.checked }))} />
              <span>{m.label}</span>
            </label>
            {m.page
              ? <Field value={m.url || ''} placeholder="url" aria-label={`${m.label} url`}
                  onSave={v => save(() => putScraperMarket(m.key, { url: v }))} />
              : <Field value={m.tabs.join(', ')} placeholder="tabs" aria-label={`${m.label} tabs`}
                  onSave={v => save(() => putScraperMarket(m.key, {
                    tabs: v.split(',').map(t => t.trim()).filter(Boolean),
                  }))} />}
          </li>
        ))}
      </ul>

      {timings.map(t => (
        <section key={t.site} className={styles.site}>
          <div className={styles.siteName}>{t.site}</div>
          <div className={styles.grid}>
            {TIMINGS.filter(([, , listing]) => !listing || t.listings).map(([k, label]) => (
              <label key={k} className={styles.num}>
                <span>{label}</span>
                <Field type="number" value={t[k] == null ? '' : String(t[k])}
                  onSave={v => save(() => putScraperTiming(t.site, {
                    [k]: v === '' ? null : Number(v),
                  }))} />
              </label>
            ))}
          </div>
        </section>
      ))}

      <table className={styles.table}>
        <thead>
          <tr><th>Target</th><th>State</th><th>Load</th><th>Parse</th><th className={styles.r}>Posted</th></tr>
        </thead>
        <tbody>
          {status.targets.map(t => (
            <tr key={t.name}>
              <td>
                <div>{t.name}</div>
                {t.detail && <div className={styles.detail}>{t.detail}</div>}
              </td>
              <td>{t.state}</td>
              <td className={styles.mono}>{when(t.last_load)}</td>
              <td className={styles.mono}>{when(t.last_parse)}</td>
              <td className={`${styles.mono} ${styles.r}`}>{t.rows_posted}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <table className={styles.table}>
        <thead>
          <tr><th>Site</th><th className={styles.r}>Hour</th><th className={styles.r}>Day</th><th>Cool-off</th></tr>
        </thead>
        <tbody>
          {status.sites.map(s => (
            <tr key={s.site}>
              <td>{s.site}</td>
              <td className={`${styles.mono} ${styles.r}`}>{s.loads_hour}/{s.per_hour}</td>
              <td className={`${styles.mono} ${styles.r}`}>{s.loads_day}/{s.per_day}</td>
              <td>{s.cooloff
                ? `${s.cooloff}, ${s.cooloff_left_h.toFixed(1)} h${s.needs_resume ? ', needs resume' : ''}`
                : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// A league to watch: its name and the Paddy competition listing url. Adds on
// Enter or the button; the box picks it up on its next config poll.
function AddLeague({ onAdd }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const ready = name.trim() && url.trim()
  const submit = (e) => {
    e.preventDefault()
    if (!ready) return
    onAdd({ name: name.trim(), url: url.trim() }).then(() => { setName(''); setUrl('') })
  }
  return (
    <form className={styles.add} onSubmit={submit}>
      <input value={name} onChange={e => setName(e.target.value)} placeholder="League" aria-label="New league name" />
      <input value={url} onChange={e => setUrl(e.target.value)} placeholder="Paddy listing url" aria-label="New league url" />
      <button type="submit" disabled={!ready}>Add</button>
    </form>
  )
}

/**
 * A text or number input that saves when it is left or on Enter, and only if
 * it changed. While it has focus a refresh does not overwrite what is being
 * typed; once it loses focus it shows the server's value again.
 */
function Field({ value, onSave, type = 'text', ...rest }) {
  const [draft, setDraft] = useState(value)
  const [focused, setFocused] = useState(false)
  const [prev, setPrev] = useState(value)
  if (!focused && value !== prev) {
    setPrev(value)
    setDraft(value)
  }
  const commit = () => {
    setFocused(false)
    if (draft === value) return
    // Held as if saved; whatever the server answers (the value, a clamped
    // value, or the old one after a refusal) differs from it and is shown.
    setPrev(draft)
    onSave(draft.trim())
  }
  return (
    <input
      className={styles.input}
      type={type}
      inputMode={type === 'number' ? 'decimal' : undefined}
      value={draft}
      onFocus={() => setFocused(true)}
      onChange={e => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}
      {...rest}
    />
  )
}
