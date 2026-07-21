import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { getCouponTypes, getTypeSelections, editLeg } from '../api'
import { FRACTIONAL_LADDER, fractionToDecimal, nearestFraction } from '../odds'
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

/**
 * EV as an index where 100 is break-even, matching the reference tracker:
 * 1.85 taken against 1.70 fair reads 108.8 rather than +8.8%. Same number,
 * but it sorts and scans as a single magnitude with no sign to parse.
 */
function fmtEv(v) {
  if (v == null) return '—'
  return (100 + v * 100).toFixed(2)
}

/** Time until kickoff, coarse on purpose — this is urgency, not a countdown. */
function fmtKickoff(iso) {
  if (!iso) return '—'
  const ms = new Date(iso + (iso.endsWith('Z') ? '' : 'Z')) - Date.now()
  if (ms <= 0) return 'live'
  const mins = Math.floor(ms / 60000)
  if (mins < 60) return `${mins} min`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'}`
  const days = Math.floor(hours / 24)
  return `${days}d ${hours % 24}h`
}

function fixtureDiffers(leg) {
  if (!leg.matched_fixture) return false
  return leg.matched_fixture.trim().toLowerCase() !== leg.match_name.trim().toLowerCase()
}

export default function TrackerPage() {
  // The seven coupon types are the entities. Each is its own filtered fixture
  // list; an upload is just how selections got into one.
  const [types, setTypes] = useState([])
  const [activeCode, setActiveCode] = useState(null)
  const [legs, setLegs] = useState([])
  const [pickerOpen, setPickerOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft] = useState(null)
  const [savingId, setSavingId] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getCouponTypes().then(ts => {
      setTypes(ts)
      if (ts.length > 0) setActiveCode(ts[0].code)
    }).finally(() => setLoading(false))
  }, [])

  const loadSelections = useCallback(async (code) => {
    if (!code) { setLegs([]); return }
    setLegs(await getTypeSelections(code))
  }, [])

  useEffect(() => { loadSelections(activeCode) }, [activeCode, loadSelections])

  const activeType = types.find(t => t.code === activeCode) ?? null

  function selectType(code) {
    setActiveCode(code)
    setPickerOpen(false)
  }

  async function handleRefresh() {
    if (!activeCode || refreshing) return
    setRefreshing(true)
    try {
      await loadSelections(activeCode)
    } catch { /* leave existing rows */ } finally {
      setRefreshing(false)
    }
  }

  /** Odds picked from the wheel: persist, reprice, and re-sort on the result. */
  async function handleOddsChange(leg, fraction) {
    const decimal = fractionToDecimal(fraction)
    if (decimal == null) return
    setSavingId(leg.id)
    setError(null)
    try {
      const updated = await editLeg(leg.coupon_id, leg.id, { odds: Number(decimal.toFixed(4)) })
      setLegs(prev => prev.map(l => (l.id === leg.id ? updated : l)))
    } catch (e) {
      setError(e.message)
    } finally {
      setSavingId(null)
    }
  }

  function startEdit(leg) {
    setEditingId(leg.id)
    setDraft({ match_name: leg.match_name, pick: leg.pick })
    setError(null)
  }

  function cancelEdit() {
    setEditingId(null)
    setDraft(null)
  }

  async function saveEdit(leg) {
    setSavingId(leg.id)
    setError(null)
    try {
      const updated = await editLeg(leg.coupon_id, leg.id, draft)
      setLegs(prev => prev.map(l => (l.id === leg.id ? updated : l)))
      cancelEdit()
    } catch (e) {
      setError(e.message)
    } finally {
      setSavingId(null)
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <button className={styles.couponPill} onClick={() => setPickerOpen(true)}>
          <span className={styles.pillKey}>COUPON</span>
          <span className={styles.pillName}>{activeType?.label ?? '—'}</span>
          <span className={styles.chevron}>▾</span>
        </button>
        {activeType?.max_fair_odds != null && (
          <span className={`${styles.statusChip} ${styles.live}`}>
            ≤ {activeType.max_fair_odds.toFixed(2)}
          </span>
        )}
      </div>

      {activeType && (
        <div className={styles.subheader}>
          <span className={styles.subMeta}>
            {legs.length} fixture{legs.length !== 1 ? 's' : ''}
            {activeType.max_fair_odds != null && ' · shortest first'}
            {activeType.flat_payout != null && activeType.best_combination_value != null && (
              <>
                {' · best '}{activeType.payout_picks}{': '}
                <span className={activeType.best_combination_value > 0 ? styles.comboGood : styles.comboBad}>
                  {activeType.best_combination_fair?.toFixed(2)} fair vs {activeType.flat_payout} flat
                  {' ('}{(activeType.best_combination_value * 100).toFixed(1)}%{')'}
                </span>
              </>
            )}
          </span>
          <div className={styles.subActions}>
            <button className={styles.refreshBtn} onClick={handleRefresh} disabled={refreshing}>
              {refreshing ? 'Refreshing…' : '↻ Refresh'}
            </button>
          </div>
        </div>
      )}

      {error && <p className={styles.error}>{error}</p>}

      {!loading && legs.length === 0 && (
        <div className={styles.empty}>
          <p>No fixtures on {activeType?.label ?? 'this coupon'} yet.</p>
          <Link to="/upload" className={styles.uploadLink}>
            Upload a {activeType?.label ?? 'coupon'} photo →
          </Link>
        </div>
      )}

      {/* Middle ground: cards, not rows — but each card is laid out on the
          reference table's columns, so rank / EV / odds still line up down the
          list and stay scannable. Nothing scrolls sideways on a phone. */}
      {legs.length > 0 && (
        <div className={styles.legs}>
          <div className={styles.legsHead}>
            <span className={styles.hRank}>R</span>
            <span className={styles.hName}>NAME</span>
            <span className={styles.hEv}>EV</span>
          </div>

          {legs.map((leg, i) => (
            <div
              key={leg.id}
              className={`${styles.legCard} ${leg.flags?.length ? styles.flagged : ''}`}
            >
              {editingId === leg.id ? (
                <div className={styles.editCell}>
                  <input
                    className={styles.editInput}
                    value={draft.match_name}
                    onChange={e => setDraft({ ...draft, match_name: e.target.value })}
                    placeholder="Match"
                    autoFocus
                  />
                  <input
                    className={styles.editInput}
                    value={draft.pick}
                    onChange={e => setDraft({ ...draft, pick: e.target.value })}
                    placeholder="Pick"
                  />
                  <div className={styles.editActions}>
                    <button className={styles.editCancel} onClick={cancelEdit}>Cancel</button>
                    <button
                      className={styles.editSave}
                      onClick={() => saveEdit(leg)}
                      disabled={savingId === leg.id}
                    >
                      {savingId === leg.id ? 'Saving…' : 'Save & reprice'}
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className={styles.legTop}>
                    <span className={`${styles.rank} ${styles.mono}`}>{i + 1}</span>
                    <button className={styles.nameCell} onClick={() => startEdit(leg)}>
                      <span className={styles.nameLine}>
                        {leg.flags?.length > 0 && (
                          <span
                            className={styles.warnBadge}
                            title={leg.flags.map(f => FLAG_LABELS[f] ?? f).join('\n')}
                          >⚠</span>
                        )}
                        <span className={styles.pickName}>{leg.pick}</span>
                        <span className={styles.fixture}> – {leg.match_name}</span>
                        {leg.edited && (
                          <span className={styles.editedMark} title="Edited by hand — a re-scan will not overwrite this row">✎</span>
                        )}
                      </span>
                      {fixtureDiffers(leg) && (
                        <span className={styles.matchedTo} title="Fixture the odds provider matched — check it's the right game">
                          → {leg.matched_fixture}
                        </span>
                      )}
                    </button>
                    <span className={`${styles.evCell} ${styles[valueTier(leg.value)]} ${styles.mono}`}>
                      {leg.odds == null ? '·' : fmtEv(leg.value)}
                    </span>
                  </div>

                  <div className={styles.legBottom}>
                    <span className={`${styles.ko} ${styles.mono}`}>{fmtKickoff(leg.kickoff)}</span>

                    {/* Flat-payout coupons print no per-selection price, so
                        there is nothing to pick and nothing to show. */}
                    {leg.odds != null && (
                      <>
                        {/* Native select = scroll wheel on mobile. Prices move
                            during the day, so this is picked, not typed. */}
                        <select
                          className={`${styles.oddsWheel} ${styles.mono}`}
                          value={nearestFraction(leg.odds) ?? ''}
                          onChange={e => handleOddsChange(leg, e.target.value)}
                          disabled={savingId === leg.id}
                          aria-label={`Odds for ${leg.pick}`}
                        >
                          {FRACTIONAL_LADDER.map(f => (
                            <option key={f} value={f}>{f}</option>
                          ))}
                        </select>

                        <span className={styles.oddsPair}>
                          <span className={styles.oddsLabel}>book</span>
                          <span className={`${styles.bookOdds} ${styles.mono}`}>{leg.odds.toFixed(2)}</span>
                        </span>
                      </>
                    )}
                    <span className={styles.oddsPair}>
                      <span className={styles.oddsLabel}>fair</span>
                      <span className={`${styles.fairOdds} ${styles.mono}`}>
                        {leg.fair_odds != null ? leg.fair_odds.toFixed(2) : '—'}
                      </span>
                    </span>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      )}

      {pickerOpen && (
        <div className={styles.pickerBackdrop} onClick={() => setPickerOpen(false)}>
          <div className={styles.pickerSheet} onClick={e => e.stopPropagation()}>
            <div className={styles.pickerHandle} />
            <div className={styles.pickerList}>
              {types.map(t => (
                <button
                  key={t.code}
                  className={`${styles.pickerRow} ${t.code === activeCode ? styles.pickerActive : ''}`}
                  onClick={() => selectType(t.code)}
                >
                  <span className={styles.pickerName}>{t.label}</span>
                  <span className={styles.pickerMeta}>
                    {t.selection_count} fixture{t.selection_count !== 1 ? 's' : ''}
                  </span>
                </button>
              ))}
            </div>
            <Link to="/upload" className={styles.pickerUpload}>↑ UPLOAD COUPONS</Link>
          </div>
        </div>
      )}
    </div>
  )
}
