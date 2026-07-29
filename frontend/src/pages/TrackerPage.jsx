import { useState, useEffect, useCallback, useMemo, memo, Fragment } from 'react'
import { Link } from 'react-router-dom'
import { getCouponTypes, getTypeSelections, editLeg } from '../api'
import { FRACTIONAL_LADDER, fractionToDecimal, nearestFraction } from '../odds'
import styles from './TrackerPage.module.css'

// The ladder is static, so build its <option> elements ONCE at module load and
// share them across every row. A long coupon renders ~77 options per leg — 5.7k
// elements on a 74-fixture list — and rebuilding those on each render was the
// bulk of the tracker's render cost. React elements are immutable, so one array
// can safely back every select.
// Each rung carries its own decimal, so the control IS the book price — there
// is no separate "book 1.83" beside it saying the same thing again.
const LADDER_OPTIONS = FRACTIONAL_LADDER.map(f => (
  <option key={f} value={f}>{f} · {fractionToDecimal(f).toFixed(2)}</option>
))

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

const VS_RE = /\s+(?:vs\.?|v\.?|@)\s+/i

/**
 * Split a leg into the fixture's two sides, which one the value is on, and the
 * market — so the row can print each thing exactly once.
 *
 * A pick on a two-sided coupon repeats its own team ("Kairat to Score 2"
 * against "Kairat v Omonia Nicosia"), which said the team twice and wrapped to
 * three lines on a phone. Printing the fixture whole and BOLDING the side the
 * value is on carries the same information in one line, and keeps the opponent
 * visible where the old form hid it.
 *
 * Returns null when the pick names no team (GG is a property of the fixture,
 * not a side) or matches neither — a hand-edited row can disagree, and there
 * the plain pick and fixture are the honest thing to show.
 */
function pickParts(leg) {
  const parts = (leg.match_name || '').split(VS_RE)
  if (parts.length !== 2) return null
  const [home, away] = parts.map(s => s.trim())
  if (!home || !away) return null
  const pick = (leg.pick || '').trim()
  const lower = pick.toLowerCase()
  let isHome = null
  if (lower.startsWith(home.toLowerCase())) isHome = true
  else if (lower.startsWith(away.toLowerCase())) isHome = false
  if (isHome === null) return null
  // Whatever follows the team name is the market ("to Score 2").
  const market = pick.slice((isHome ? home : away).length).trim()
  return { home, away, isHome, market }
}

// Split a type's selections into period sections so the live set reads apart
// from the next one to bet. Order: active (window contains today), upcoming
// (the next set), ended, then any whose window isn't known yet.
const PERIOD_SECTIONS = [
  { status: 'active', label: 'Active now' },
  { status: 'upcoming', label: 'Next set' },
  { status: 'past', label: 'Ended' },
  { status: null, label: 'Unscheduled' },
]

function groupLegsByPeriod(legs) {
  return PERIOD_SECTIONS
    .map(({ status, label }) => {
      const items = legs.filter(l => (l.period_status ?? null) === status)
      if (!items.length) return null
      const windows = [...new Set(items.map(l => l.period_label).filter(Boolean))]
      return { status, label, windows, legs: items }
    })
    .filter(Boolean)
}

/**
 * The inline edit form. Its draft lives HERE, not on the page, so typing
 * re-renders only this one card — holding it on the page re-rendered all 74
 * leg cards (and their selects) on every keystroke, which is what made editing
 * feel laggy. Same reason PnlEntry keeps its own state.
 */
function LegEditForm({ leg, saving, onCancel, onSave }) {
  const [draft, setDraft] = useState({ match_name: leg.match_name, pick: leg.pick })

  return (
    <div className={styles.editCell}>
      <input
        className={styles.editInput}
        value={draft.match_name}
        onChange={e => setDraft(d => ({ ...d, match_name: e.target.value }))}
        placeholder="Match"
        autoFocus
      />
      <input
        className={styles.editInput}
        value={draft.pick}
        onChange={e => setDraft(d => ({ ...d, pick: e.target.value }))}
        placeholder="Pick"
      />
      <div className={styles.editActions}>
        <button className={styles.editCancel} onClick={onCancel}>Cancel</button>
        <button
          className={styles.editSave}
          onClick={() => onSave(leg, draft)}
          disabled={saving}
        >
          {saving ? 'Saving…' : 'Save & reprice'}
        </button>
      </div>
    </div>
  )
}

/**
 * One fixture row. Memoised on primitive props (`editing` / `saving` are
 * booleans, the callbacks are stable), so refreshing or editing one row leaves
 * the other 73 untouched instead of re-rendering the whole list.
 */
const LegCard = memo(function LegCard({
  leg, rank, editing, saving, onStartEdit, onCancelEdit, onSave, onOddsChange,
}) {
  return (
    <div className={`${styles.legCard} ${leg.flags?.length ? styles.flagged : ''}`}>
      {editing ? (
        <LegEditForm leg={leg} saving={saving} onCancel={onCancelEdit} onSave={onSave} />
      ) : (
        <>
          <div className={styles.legTop}>
            <span className={`${styles.rank} ${styles.mono}`}>{rank}</span>
            <button className={styles.nameCell} onClick={() => onStartEdit(leg)}>
              <span className={styles.nameLine}>
                {leg.flags?.length > 0 && (
                  <span
                    className={styles.warnBadge}
                    title={leg.flags.map(f => FLAG_LABELS[f] ?? f).join('\n')}
                  >⚠</span>
                )}
                {/* The full fixture, with the side the value is on in bold and
                    the market stated once. Falls back to pick + fixture when the
                    pick names no team (GG) or matches neither side. */}
                {(() => {
                  const p = pickParts(leg)
                  if (!p) return (
                    <>
                      <span className={styles.pickName}>{leg.pick}</span>
                      <span className={styles.fixture}> – {leg.match_name}</span>
                    </>
                  )
                  return (
                    <span className={styles.fixture}>
                      <span className={p.isHome ? styles.pickName : undefined}>{p.home}</span>
                      {' v '}
                      <span className={p.isHome ? undefined : styles.pickName}>{p.away}</span>
                      {p.market ? ` – ${p.market}` : ''}
                    </span>
                  )
                })()}
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
            {/* A ladder coupon has no per-row EV — value belongs to the
                combination — so the row shows its fair price. */}
            <span className={`${styles.evCell} ${leg.odds == null ? styles.none : styles[valueTier(leg.value)]} ${styles.mono}`}>
              {leg.odds == null
                ? (leg.fair_odds != null ? leg.fair_odds.toFixed(2) : '—')
                : fmtEv(leg.value)}
            </span>
          </div>

          <div className={styles.legBottom}>
            <span className={`${styles.ko} ${styles.mono}`}>{fmtKickoff(leg.kickoff)}</span>

            {/* Flat-payout coupons print no per-selection price, so there is
                nothing to pick and nothing to show. */}
            {leg.odds != null && (
              <>
                {/* Native select = scroll wheel on mobile. Prices move during
                    the day, so this is picked, not typed. The rung shows its own
                    decimal, so this control is the book price — printing it
                    again beside the picker said the same number twice. */}
                <select
                  className={`${styles.oddsWheel} ${styles.mono}`}
                  value={nearestFraction(leg.odds) ?? ''}
                  onChange={e => onOddsChange(leg, e.target.value)}
                  disabled={saving}
                  aria-label={`Odds for ${leg.pick}`}
                >
                  {LADDER_OPTIONS}
                </select>

                <span className={styles.oddsPair}>
                  <span className={styles.oddsLabel}>fair</span>
                  <span className={`${styles.fairOdds} ${styles.mono}`}>
                    {leg.fair_odds != null ? leg.fair_odds.toFixed(2) : '—'}
                  </span>
                </span>
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
})

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

  // Both derive from state that changes far less often than the page renders.
  const activeType = useMemo(
    () => types.find(t => t.code === activeCode) ?? null,
    [types, activeCode],
  )
  const legGroups = useMemo(() => groupLegsByPeriod(legs), [legs])

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

  // These are handed to every memoised row, so they must keep a stable identity
  // — an inline arrow here would re-render all of them on each page render and
  // undo the memo entirely.

  /** Odds picked from the wheel: persist, reprice, and re-sort on the result. */
  const handleOddsChange = useCallback(async (leg, fraction) => {
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
  }, [])

  const startEdit = useCallback(leg => {
    setEditingId(leg.id)
    setError(null)
  }, [])

  const cancelEdit = useCallback(() => setEditingId(null), [])

  const saveEdit = useCallback(async (leg, draft) => {
    setSavingId(leg.id)
    setError(null)
    try {
      const updated = await editLeg(leg.coupon_id, leg.id, draft)
      setLegs(prev => prev.map(l => (l.id === leg.id ? updated : l)))
      setEditingId(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setSavingId(null)
    }
  }, [])

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
            {activeType.best_combination && (
              <>
                {' · best '}
                <span className={activeType.best_combination.value > 0 ? styles.comboGood : styles.comboBad}>
                  {activeType.best_combination.picks}-fold pays{' '}
                  {activeType.best_combination.payout} vs{' '}
                  {activeType.best_combination.fair.toFixed(2)} fair
                  {' ('}{(activeType.best_combination.value * 100).toFixed(1)}%{')'}
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
            <span className={styles.hEv}>
              {activeType?.prices_each_selection === false ? 'FAIR' : 'EV'}
            </span>
          </div>

          {legGroups.map(g => (
            <Fragment key={g.status ?? 'none'}>
              <div className={`${styles.periodHead} ${styles['ph_' + (g.status ?? 'none')]}`}>
                <span className={styles.phStatus}>{g.label}</span>
                {g.windows.length > 0 && (
                  <span className={styles.phWindow}>{g.windows.join(' · ')}</span>
                )}
                <span className={`${styles.phCount} ${styles.mono}`}>{g.legs.length}</span>
              </div>
              {g.legs.map((leg, i) => (
                <LegCard
                  key={leg.id}
                  leg={leg}
                  rank={i + 1}
                  editing={editingId === leg.id}
                  saving={savingId === leg.id}
                  onStartEdit={startEdit}
                  onCancelEdit={cancelEdit}
                  onSave={saveEdit}
                  onOddsChange={handleOddsChange}
                />
              ))}
            </Fragment>
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
            <Link to="/upload" className={styles.pickerUploadBtn}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M12 16V4M7 9l5-5 5 5" />
                <path d="M5 16v2a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-2" />
              </svg>
              UPLOAD COUPONS
            </Link>
          </div>
        </div>
      )}
    </div>
  )
}
