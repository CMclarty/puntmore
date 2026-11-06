import { useState, useEffect, useCallback, useRef } from 'react'
import {
  getTwoUpDiagnostics, getTwoUpFeed, getTwoUpLedger, killTwoUpClient, resumeTwoUpClient,
} from '../api'
import useAutoRefresh from '../hooks/useAutoRefresh'
import { betLabel, betNote, edgePct, gbp, kickoffLabel, signedGbp } from '../twoup'
import styles from './FeedPage.module.css'
import f from './CardsPage.module.css'
import t from './TwoUpPage.module.css'

/**
 * 2-Up — the engine's tab on the 365 page. Two lists behind one switch:
 * Feed, every side passing the price band and edge floor, best edge first;
 * Ledger, one client's bets newest first with its exposure and P&L, and the
 * per-client kill switch (anyone turns it on; resuming asks). Render decides
 * and the Hub places; nothing here places a bet.
 */
const VIEWS = [['feed', 'Feed'], ['ledger', 'Ledger']]
const VIEW_KEY = 'twoup.view'

function edgeTier(edge) {
  if (edge >= 0.03) return 'hi'
  if (edge >= 0.015) return 'mid'
  return 'lo'
}

export default function TwoUpPage() {
  const [view, setView] = useState(() => {
    try { return localStorage.getItem(VIEW_KEY) === 'ledger' ? 'ledger' : 'feed' } catch { return 'feed' }
  })
  const pick = (v) => { setView(v); try { localStorage.setItem(VIEW_KEY, v) } catch { /* fine */ } }

  return (
    <div className={styles.page}>
      {view === 'feed' ? <Feed /> : <Ledger />}
      <div className={f.offerSwitch} role="tablist">
        {VIEWS.map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={view === k}
            className={`${f.offerBtn} ${view === k ? f.offerOn : ''}`} onClick={() => pick(k)}>
            {label}
          </button>
        ))}
      </div>
    </div>
  )
}

function Feed() {
  const [feed, setFeed] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const load = useCallback(async (quiet = false) => {
    if (quiet !== true) setLoading(true)
    setError(null)
    try {
      setFeed(await getTwoUpFeed())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])
  useAutoRefresh(() => load(true))

  const rows = feed?.candidates || []
  const cannot = feed?.status?.cannot_price || []
  return (
    <>
      <div className={styles.header}>
        <span className={styles.title}>2-Up</span>
        <button className={styles.refresh} onClick={load} disabled={loading}>{loading ? '…' : '↻'}</button>
      </div>
      {error && <p className={styles.error}>{error}</p>}
      {feed && !feed.available && <p className={styles.error}>Feed unavailable: no odds client on this deploy</p>}
      {cannot.length > 0 && <p className={styles.error}>Cannot price: {cannot.join(' · ')}</p>}
      <div className={styles.feed}>
        {rows.map(c => (
          <div key={`${c.event_id}-${c.side}`} className={styles.card}>
            <div className={styles.cardRow}>
              <span className={styles.match}>{c.home} v {c.away}</span>
              <span className={styles.sub}>{kickoffLabel(c.kickoff)}</span>
            </div>
            <div className={styles.cardRow}>
              <span className={styles.pick}>{c.selection}</span>
              <span className={`${styles.odds} ${styles.mono}`}>{c.price.toFixed(2)}</span>
              <span className={`${styles.badge} ${styles[edgeTier(c.edge)]}`}>{edgePct(c.edge)}</span>
            </div>
            <div className={styles.cardRow}>
              <span className={styles.sub}>
                fair {c.fair_price != null ? c.fair_price.toFixed(2) : '—'} · rescue {(c.rescue * 100).toFixed(1)}%
                {c.source_scope === 'owner' ? ' · tip' : ''}
              </span>
            </div>
          </div>
        ))}
        {!loading && feed?.available && rows.length === 0 && !error && (
          <p className={styles.empty}>Nothing qualifies right now.</p>
        )}
      </div>
    </>
  )
}

function Ledger() {
  const [clients, setClients] = useState([])
  const [clientId, setClientId] = useState(null)
  const [ledger, setLedger] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  // The chosen client, readable inside `load` without making it a dependency
  // (which would re-run the first fetch every time the choice changed).
  const chosen = useRef(null)

  const load = useCallback(async (quiet = false) => {
    if (quiet !== true) setLoading(true)
    setError(null)
    try {
      const list = (await getTwoUpDiagnostics()).clients || []
      setClients(list)
      const id = list.some(c => c.client_id === chosen.current)
        ? chosen.current
        : (list.find(c => c.is_owner) || list[0])?.client_id || null
      chosen.current = id
      setClientId(id)
      setLedger(id ? await getTwoUpLedger(id) : null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])
  useAutoRefresh(() => load(true))
  const choose = (id) => { chosen.current = id; setClientId(id); load(true) }

  const client = ledger?.client
  const setKill = async (on) => {
    if (!client) return
    if (!on && !window.confirm(`Resume ${client.name || client.client_id}? Bets will be decided for it again.`)) return
    setBusy(true)
    setError(null)
    try {
      await (on ? killTwoUpClient(client.client_id) : resumeTwoUpClient(client.client_id))
      await load(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className={styles.header}>
        {clients.length > 1 ? (
          <select className={t.client} value={clientId || ''} onChange={e => choose(e.target.value)} aria-label="Client">
            {clients.map(c => <option key={c.client_id} value={c.client_id}>{c.name || c.client_id}</option>)}
          </select>
        ) : (
          <span className={styles.title}>{client?.name || client?.client_id || 'Ledger'}</span>
        )}
        <span className={t.actions}>
          {client && (client.kill_switch
            ? <button type="button" className={`${t.kill} ${t.killOn}`} onClick={() => setKill(false)} disabled={busy}>Killed · resume</button>
            : <button type="button" className={t.kill} onClick={() => setKill(true)} disabled={busy}>Kill</button>)}
          <button className={styles.refresh} onClick={load} disabled={loading}>{loading ? '…' : '↻'}</button>
        </span>
      </div>
      {error && <p className={styles.error}>{error}</p>}
      {ledger && (
        <p className={styles.meta}>
          {client.dry_run ? 'dry run · ' : ''}open {gbp(ledger.open_exposure)} · staked {gbp(ledger.staked)} · returned {gbp(ledger.returned)} · {signedGbp(ledger.pnl)}
        </p>
      )}
      <div className={styles.feed}>
        {(ledger?.bets || []).map(b => (
          <div key={b.bet_id} className={styles.card}>
            <div className={styles.cardRow}>
              <span className={styles.match}>{betLabel(b)}</span>
              <span className={`${styles.odds} ${styles.mono}`}>{gbp(b.stake)}</span>
              <span className={`${styles.badge} ${t[b.status] || styles.lo}`}>{b.status.replace('_', ' ')}</span>
            </div>
            <div className={styles.cardRow}>
              <span className={styles.sub}>
                {b.account_id} · {b.day}{b.source_scope === 'owner' ? ' · tip' : ''}
                {betNote(b) ? ` · ${betNote(b)}` : ''}
              </span>
            </div>
          </div>
        ))}
        {!loading && ledger && ledger.bets.length === 0 && !error && (
          <p className={styles.empty}>No bets yet.</p>
        )}
        {!loading && !ledger && !error && clients.length === 0 && (
          <p className={styles.empty}>No clients configured.</p>
        )}
      </div>
    </>
  )
}
