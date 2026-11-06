import { useState, useEffect, useCallback } from 'react'
import { getTwoUpFeed } from '../api'
import useAutoRefresh from '../hooks/useAutoRefresh'
import { edgePct, emptyMeansNothingOnOffer, kickoffLabel, timeLabel } from '../twoup'
import styles from './FeedPage.module.css'
import t from './TwoUpPage.module.css'

/**
 * 2-Up — the engine's tab on the 365 page: the Feed, every side passing the
 * price band and edge floor, best edge first, with when the board was built
 * and when each row was priced. A one-way read of the server's scheduled
 * board; nothing here places a bet, keeps an account or configures the engine.
 */
function edgeTier(edge) {
  if (edge >= 0.03) return 'hi'
  if (edge >= 0.015) return 'mid'
  return 'lo'
}

export default function TwoUpPage() {
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

  // Optional chaining throughout: an older API mid-deploy sends no `status`
  // keys, no `generated_at` and no `priced_at`, and the page must not throw.
  const rows = feed?.candidates || []
  const status = feed?.status
  const cannot = status?.cannot_price || []
  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <span className={styles.title}>2-Up</span>
        <span className={t.actions}>
          {feed && <span className={styles.sub}>updated {timeLabel(feed.generated_at)}</span>}
          <button className={styles.refresh} onClick={load} disabled={loading}>{loading ? '…' : '↻'}</button>
        </span>
      </div>
      {error && <p className={styles.error}>{error}</p>}
      {feed && !feed.available && <p className={styles.error}>Feed unavailable: no odds client on this deploy</p>}
      {status?.killed && <p className={styles.error}>Feed stopped by the kill switch</p>}
      {status?.not_yet_refreshed && <p className={styles.error}>First refresh pending</p>}
      {status?.stale && (
        <p className={styles.error}>Prices from {timeLabel(status.served_at)} · OddsPapi not answering</p>
      )}
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
                {' · '}priced {timeLabel(c.priced_at)}
              </span>
            </div>
          </div>
        ))}
        {!loading && feed?.available && emptyMeansNothingOnOffer(status) && rows.length === 0 && !error && (
          <p className={styles.empty}>Nothing qualifies right now.</p>
        )}
      </div>
    </div>
  )
}
