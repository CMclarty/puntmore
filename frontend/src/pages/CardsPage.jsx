import { useState, useEffect, useRef, useCallback } from 'react'
import { getCardsFeed } from '../api'
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

export default function CardsPage() {
  const [threshold, setThreshold] = useState(DEFAULT_EV)
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [fetchedAt, setFetchedAt] = useState(null)
  const [openId, setOpenId] = useState(null)
  const debounceRef = useRef(null)

  const fetchFeed = useCallback(async (ev) => {
    setLoading(true)
    setError(null)
    try {
      const data = await getCardsFeed(ev / 100 - 1)
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

  function toggleRow(id) {
    setOpenId(prev => prev === id ? null : id)
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <span className={styles.title}>Cards &amp; Corners</span>
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
        {items.map((item) => {
          const isOpen = openId === item.event_id
          return (
            <div key={item.event_id} className={styles.matchBlock}>
              <button
                className={`${styles.matchRow} ${isOpen ? styles.matchRowOpen : ''}`}
                onClick={() => toggleRow(item.event_id)}
              >
                <span className={styles.match}>{item.match}</span>
                <span className={`${styles.badge} ${styles[valueTier(item.best_value)]}`}>
                  {fmt(item.best_value)}
                </span>
                <span className={`${styles.chevron} ${isOpen ? styles.chevronOpen : ''}`}>›</span>
              </button>

              {isOpen && (
                <div className={styles.bets}>
                  {item.bets.map((bet, i) => (
                    <div key={i} className={styles.betRow}>
                      <span className={styles.pick}>{bet.pick}</span>
                      <span className={`${styles.odds} ${styles.mono}`}>{bet.odds.toFixed(2)}</span>
                      <span className={`${styles.badge} ${styles[valueTier(bet.value)]}`}>
                        {fmt(bet.value)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
        {!loading && items.length === 0 && !error && (
          <p className={styles.empty}>No matches at this threshold.</p>
        )}
      </div>
    </div>
  )
}
