import { useState, useEffect, useCallback } from 'react'
import { getFinderBoard } from '../api'
import useAutoRefresh from '../hooks/useAutoRefresh'
import { groupRows, stakeLabel, statusLabel } from '../finder'
import { edgePct, kickoffLabel, timeLabel } from '../twoup'
import styles from './FeedPage.module.css'
import t from './TwoUpPage.module.css'

/**
 * Finder — Callum's private Bet Finder on me (docs/finder-spec.md): the tips
 * Hermes judged on the Hub over the last 3 days, as asked, offered, rejected
 * and done (placed, price gone, expired). A read of /api/finder/board; nothing
 * here judges, stakes or places a bet.
 */
const SECTIONS = [['asked', 'Asked'], ['offered', 'Offered'], ['rejected', 'Rejected'], ['done', 'Done']]

export default function FinderPage() {
  const [board, setBoard] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Every state change after the fetch answers, so the first load can run
  // from an effect without a synchronous setState there.
  const fetchBoard = useCallback(() => getFinderBoard()
    .then(b => { setBoard(b); setError(null) }, err => setError(err.message))
    .finally(() => setLoading(false)), [])
  const load = () => { setLoading(true); fetchBoard() }
  useEffect(() => { fetchBoard() }, [fetchBoard])
  useAutoRefresh(fetchBoard)

  const rows = board?.rows || []
  const groups = groupRows(rows)
  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <span className={styles.title}>Finder</span>
        <span className={t.actions}>
          {board && <span className={styles.sub}>updated {timeLabel(board.generated_at)}</span>}
          <button className={styles.refresh} onClick={load} disabled={loading}>{loading ? '…' : '↻'}</button>
        </span>
      </div>
      {error && <p className={styles.error}>{error}</p>}
      <div className={styles.feed}>
        {SECTIONS.filter(([key]) => groups[key].length > 0).map(([key, name]) => (
          <section key={key}>
            <div className={styles.meta}>{name} · {groups[key].length}</div>
            {groups[key].map(r => (
              <div key={r.tip_id} className={styles.card}>
                <div className={styles.cardRow}>
                  <span className={styles.match}>{r.event}</span>
                  <span className={styles.sub}>{kickoffLabel(r.kickoff)}</span>
                </div>
                <div className={styles.cardRow}>
                  <span className={styles.pick}>{r.selection}</span>
                  <span className={`${styles.odds} ${styles.mono}`}>{r.odds.toFixed(2)}</span>
                  <span className={styles.sub}>fair {r.fair != null ? r.fair.toFixed(2) : '—'}</span>
                  <span className={`${styles.badge} ${styles.mid}`}>{r.edge != null ? edgePct(r.edge) : '—'}</span>
                  {key === 'done' && <span className={`${styles.badge} ${styles.lo}`}>{statusLabel(r.status)}</span>}
                </div>
                <div className={styles.cardRow}>
                  <span className={styles.sub}>
                    {r.origin} · stake {stakeLabel(r.advised_stake)}{r.reason ? ` · ${r.reason}` : ''}
                  </span>
                </div>
              </div>
            ))}
          </section>
        ))}
        {!loading && board && rows.length === 0 && !error && (
          <p className={styles.empty}>Nothing from Hermes in the last 3 days.</p>
        )}
      </div>
    </div>
  )
}
