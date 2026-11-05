import { useState, useEffect, useCallback } from 'react'
import { getGoalscorerFeed } from '../api'
import useAutoRefresh from '../hooks/useAutoRefresh'
import styles from './FeedPage.module.css'

function valueTier(v) {
  if (v >= 0.06) return 'hi'
  if (v >= 0.04) return 'mid'
  if (v > 0) return 'lo'
  return 'neg'
}

// Edge fraction → %EV (bet odds as % of fair). 0.02 → "102.0%"
function fmt(v) {
  return `${((1 + v) * 100).toFixed(1)}%`
}

export default function GoalscorersPage() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [fetchedAt, setFetchedAt] = useState(null)

  // `quiet`: an automatic refresh updates in place without the loading state.
  const fetchFeed = useCallback(async (quiet = false) => {
    if (quiet !== true) setLoading(true)
    setError(null)
    try {
      const data = await getGoalscorerFeed()
      setItems(data)
      setFetchedAt(new Date())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchFeed() }, [fetchFeed])
  useAutoRefresh(() => fetchFeed(true))

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <span className={styles.title}>First Goalscorer</span>
        <button className={styles.refresh} onClick={fetchFeed} disabled={loading}>
          {loading ? '…' : '↻'}
        </button>
      </div>

      {fetchedAt && (
        <p className={styles.meta}>
          {loading ? 'Refreshing…' : `${items.length} value pick${items.length !== 1 ? 's' : ''}`}
          {' · '}{fetchedAt.toLocaleTimeString()}
          {' · '}bet365 vs fair Pinnacle
        </p>
      )}
      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.feed}>
        {items.map((item, i) => (
          <div key={i} className={styles.card}>
            <div className={styles.cardRow}>
              <span className={styles.match}>{item.match}</span>
              {item.inplay && <span className={styles.arbChip}>LIVE</span>}
            </div>
            <div className={styles.cardRow}>
              <span className={styles.pick}>
                {item.player}
                {item.low_confidence && (
                  <span
                    className={styles.warn}
                    title={`Uncertain name match to Pinnacle's "${item.matched_player}" (${Math.round((item.match_score ?? 0) * 100)}%)`}
                  > ⚠</span>
                )}
              </span>
              <span className={`${styles.odds} ${styles.mono}`}>
                {item.bet365_price.toFixed(2)}
              </span>
              <span className={`${styles.badge} ${styles[valueTier(item.edge)]}`}>
                {fmt(item.edge)}
              </span>
            </div>
            <div className={styles.cardRow}>
              <span className={styles.sub}>
                fair {item.fair_price.toFixed(2)} · pin {item.pinnacle_price.toFixed(2)}
              </span>
            </div>
          </div>
        ))}
        {!loading && items.length === 0 && !error && (
          <p className={styles.empty}>
            No value picks right now. Fixtures are scanned automatically ~45 min
            before kickoff — check back near a match.
          </p>
        )}
      </div>
    </div>
  )
}
