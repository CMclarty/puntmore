import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { getCoupons, getCouponLegs, refreshCoupon, rescanCoupon, editLeg } from '../api'
import styles from './TrackerPage.module.css'

// Flag codes from api/app/checks.py. Keep in sync — an unknown code falls back
// to the raw code rather than rendering nothing, so a missed mapping is visible.
const FLAG_LABELS = {
  odds_range: 'Odds look out of range for this market — check the OCR read',
  no_match: 'No fixture matched — this leg is unpriced',
  low_confidence: 'Fixture matched loosely — check it is the right game',
  value_implausible: 'Value is implausibly large — likely a bad read or wrong match',
}

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

function fixtureDiffers(leg) {
  if (!leg.matched_fixture) return false
  return leg.matched_fixture.trim().toLowerCase() !== leg.match_name.trim().toLowerCase()
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
  const [rescanning, setRescanning] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft] = useState(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

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

  async function handleRescan() {
    if (!activeCouponId || rescanning) return
    setError(null)
    setRescanning(true)
    try {
      const res = await rescanCoupon(activeCouponId)
      setLegs(res.legs)
    } catch (e) {
      setError(e.message)
    } finally {
      setRescanning(false)
    }
  }

  function startEdit(leg) {
    setEditingId(leg.id)
    setDraft({ match_name: leg.match_name, pick: leg.pick, odds: String(leg.odds) })
    setError(null)
  }

  function cancelEdit() {
    setEditingId(null)
    setDraft(null)
  }

  async function saveEdit(legId) {
    if (saving) return
    const odds = Number(draft.odds)
    if (!Number.isFinite(odds) || odds <= 1) {
      setError('Odds must be greater than 1.00')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const updated = await editLeg(activeCouponId, legId, {
        match_name: draft.match_name,
        pick: draft.pick,
        odds,
      })
      setLegs(prev => prev.map(l => (l.id === legId ? updated : l)))
      cancelEdit()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
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
          <span className={`${styles.statusChip} ${activeCoupon.status === 'confirmed' ? styles.live : styles.pending}`}>
            {activeCoupon.status === 'confirmed' ? '● LIVE' : '⏳ PROCESSING'}
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
          <div className={styles.subActions}>
            <button
              className={styles.refreshBtn}
              onClick={handleRescan}
              disabled={rescanning}
              title="Re-read the coupon image. Rows you have edited are kept."
            >
              {rescanning ? 'Re-scanning…' : '⟳ Re-scan'}
            </button>
            <button
              className={styles.refreshBtn}
              onClick={handleRefresh}
              disabled={refreshing}
            >
              {refreshing ? 'Refreshing…' : '↻ Refresh odds'}
            </button>
          </div>
        </div>
      )}

      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.legs}>
        {!loading && coupons.length === 0 && (
          <div className={styles.empty}>
            <p>No coupons yet.</p>
            <Link to="/upload" className={styles.uploadLink}>Upload a coupon →</Link>
          </div>
        )}
        {legs.map(leg => (
          editingId === leg.id ? (
            <div key={leg.id} className={`${styles.legCard} ${styles.editing}`}>
              <input
                className={styles.editInput}
                value={draft.match_name}
                onChange={e => setDraft({ ...draft, match_name: e.target.value })}
                placeholder="Match"
                autoFocus
              />
              <div className={styles.editRow}>
                <input
                  className={styles.editInput}
                  value={draft.pick}
                  onChange={e => setDraft({ ...draft, pick: e.target.value })}
                  placeholder="Pick"
                />
                <input
                  className={`${styles.editInput} ${styles.editOdds} ${styles.mono}`}
                  value={draft.odds}
                  onChange={e => setDraft({ ...draft, odds: e.target.value })}
                  placeholder="1.85"
                  type="number"
                  step="0.01"
                  min="1.01"
                  inputMode="decimal"
                />
              </div>
              <div className={styles.editActions}>
                <button className={styles.editCancel} onClick={cancelEdit}>Cancel</button>
                <button
                  className={styles.editSave}
                  onClick={() => saveEdit(leg.id)}
                  disabled={saving}
                >
                  {saving ? 'Saving…' : 'Save & reprice'}
                </button>
              </div>
            </div>
          ) : (
            <div
              key={leg.id}
              className={`${styles.legCard} ${leg.flags?.length ? styles.flagged : ''}`}
              onClick={() => startEdit(leg)}
            >
              <div className={styles.legTop}>
                <span className={styles.legMatch}>
                  {leg.flags?.length > 0 && (
                    <span
                      className={styles.warnBadge}
                      title={leg.flags.map(f => FLAG_LABELS[f] ?? f).join('\n')}
                    >
                      ⚠
                    </span>
                  )}
                  {leg.match_name}
                  {leg.edited && <span className={styles.editedMark} title="Edited by hand — a re-scan will not overwrite this row">✎</span>}
                </span>
                <span className={`${styles.valueBadge} ${styles[valueTier(leg.value)]}`}>
                  {fmtValue(leg.value)}
                </span>
              </div>
              <div className={styles.legBottom}>
                <span className={styles.legPick}>{leg.pick}</span>
                <span className={`${styles.legOdds} ${styles.mono}`}>{leg.odds.toFixed(2)}</span>
                {leg.fair_odds != null && (
                  <span className={`${styles.legFair} ${styles.mono}`}>
                    fair {leg.fair_odds.toFixed(2)}
                  </span>
                )}
              </div>
              {fixtureDiffers(leg) && (
                <div className={styles.legFixture} title="Fixture the odds provider matched — check it's the right game">
                  → {leg.matched_fixture}
                </div>
              )}
            </div>
          )
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
