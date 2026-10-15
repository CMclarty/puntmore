import { useState, useEffect, useRef, useCallback } from 'react'
import { getAccaBonusFeed } from '../api'
import ThresholdControl from '../components/ThresholdControl'
import styles from './FeedPage.module.css'

const DEBOUNCE_MS = 400
const DEFAULT_EV = 98  // preset: show everything at or above 98% EV

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

export default function AccaBonusPage() {
  const [threshold, setThreshold] = useState(DEFAULT_EV)
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [fetchedAt, setFetchedAt] = useState(null)
  const debounceRef = useRef(null)

  const fetchFeed = useCallback(async (ev) => {
    setLoading(true)
    setError(null)
    try {
      const data = await getAccaBonusFeed(ev / 100 - 1)
      setItems(data)
      setFetchedAt(new Date())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchFeed(threshold) }, []) // eslint-disable-line

  function handleThresholdChange(val) {
    setThreshold(val)
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => fetchFeed(val), DEBOUNCE_MS)
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <span className={styles.title}>Acca boost</span>
        <ThresholdControl value={threshold} onChange={handleThresholdChange} />
      </div>

      {fetchedAt && (
        <p className={styles.meta}>
          {loading ? 'Refreshing…' : `${items.length} match${items.length !== 1 ? 'es' : ''}`}
          {' · '}
          {fetchedAt.toLocaleTimeString()}
          {' · '}cached 24 h
        </p>
      )}
      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.feed}>
        {items.map((item, i) => (
          <div key={i} className={styles.card}>
            <div className={styles.cardRow}>
              <span className={styles.match}>{item.match}</span>
              {item.sport && <span className={styles.sport}>{item.sport}</span>}
            </div>
            <div className={styles.cardRow}>
              <span className={styles.pick}>{item.pick}</span>
              <span className={`${styles.odds} ${styles.mono}`}>{item.odds.toFixed(2)}</span>
              <span className={`${styles.badge} ${styles[valueTier(item.value)]}`}>
                {fmt(item.value)}
              </span>
            </div>
          </div>
        ))}
        {!loading && items.length === 0 && !error && (
          <p className={styles.empty}>No matches at this threshold.</p>
        )}
      </div>
    </div>
  )
}
