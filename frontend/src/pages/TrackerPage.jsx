import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { getCoupons, getCouponLegs, refreshCoupon } from '../api'
import styles from './TrackerPage.module.css'

function valueTier(v) {
  if (v == null) return 'none'
  if (v >= 0.06) return 'hi'
  if (v >= 0.04) return 'mid'
  if (v > 0) return 'lo'
  return 'neg'
}

function fmtValue(v) {
  if (v == null) return '—'
  const s = (v * 100).toFixed(1)
  return v > 0 ? `+${s}%` : `${s}%`
}

function fmtDate(d) {
  if (!d) return ''
  const dt = new Date(d)
  return dt.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
}

export default function TrackerPage() {
  const [coupons, setCoupons] = useState([])
  const [activeCouponId, setActiveCouponId] = useState(null)
  const [legs, setLegs] = useState([])
  const [pickerOpen, setPickerOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)

  useEffect(() => {
    getCoupons().then(cs => {
      setCoupons(cs)
      if (cs.length > 0) setActiveCouponId(cs[0].id)
    }).finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!activeCouponId) { setLegs([]); return }
    getCouponLegs(activeCouponId).then(setLegs)
  }, [activeCouponId])

  const activeCoupon = coupons.find(c => c.id === activeCouponId) ?? null

  function selectCoupon(id) {
    setActiveCouponId(id)
    setPickerOpen(false)
  }

  async function handleRefresh() {
    if (!activeCouponId || refreshing) return
    setRefreshing(true)
    try {
      setLegs(await refreshCoupon(activeCouponId))
    } catch { /* leave existing legs */ } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <button className={styles.couponPill} onClick={() => setPickerOpen(true)}>
          <span className={styles.pillKey}>COUPON</span>
          <span className={styles.pillName}>{activeCoupon?.name ?? '—'}</span>
          <span className={styles.chevron}>▾</span>
        </button>
        {activeCoupon && (
          <span className={`${styles.statusChip} ${activeCoupon.status === 'live' ? styles.live : styles.pending}`}>
            {activeCoupon.status === 'live' ? '● LIVE' : '⏳ PENDING'}
          </span>
        )}
      </div>

      {activeCoupon && (
        <div className={styles.subheader}>
          <span className={styles.subMeta}>
            {fmtDate(activeCoupon.match_day)}
            {' · '}
            {legs.length} leg{legs.length !== 1 ? 's' : ''}
          </span>
          <button
            className={styles.refreshBtn}
            onClick={handleRefresh}
            disabled={refreshing}
          >
            {refreshing ? 'Refreshing…' : '↻ Refresh odds'}
          </button>
        </div>
      )}

      <div className={styles.legs}>
        {!loading && coupons.length === 0 && (
          <div className={styles.empty}>
            <p>No coupons yet.</p>
            <Link to="/upload" className={styles.uploadLink}>Upload a coupon →</Link>
          </div>
        )}
        {legs.map(leg => (
          <div key={leg.id} className={styles.legCard}>
            <div className={styles.legTop}>
              <span className={styles.legMatch}>{leg.match_name}</span>
              <span className={`${styles.valueBadge} ${styles[valueTier(leg.value)]}`}>
                {fmtValue(leg.value)}
              </span>
            </div>
            <div className={styles.legBottom}>
              <span className={styles.legPick}>{leg.pick}</span>
              <span className={`${styles.legOdds} ${styles.mono}`}>{leg.odds.toFixed(2)}</span>
              {leg.fair_odds != null && (
                <span className={`${styles.legFair} ${styles.mono}`}>fair {leg.fair_odds.toFixed(2)}</span>
              )}
            </div>
          </div>
        ))}
      </div>

      {pickerOpen && (
        <div className={styles.pickerBackdrop} onClick={() => setPickerOpen(false)}>
          <div className={styles.pickerSheet} onClick={e => e.stopPropagation()}>
            <div className={styles.pickerHandle} />
            <div className={styles.pickerList}>
              {coupons.map(c => (
                <button
                  key={c.id}
                  className={`${styles.pickerRow} ${c.id === activeCouponId ? styles.pickerRowActive : ''}`}
                  onClick={() => selectCoupon(c.id)}
                >
                  <span className={styles.pickerName}>{c.name}</span>
                  <span className={styles.pickerMeta}>{c.leg_count} legs · {fmtDate(c.match_day)}</span>
                  {c.id === activeCouponId && <span className={styles.tick}>✓</span>}
                </button>
              ))}
            </div>
            <Link to="/upload" className={styles.pickerUploadBtn} onClick={() => setPickerOpen(false)}>
              ↑ UPLOAD COUPONS
            </Link>
          </div>
        </div>
      )}
    </div>
  )
}
