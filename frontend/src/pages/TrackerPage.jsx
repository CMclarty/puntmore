import { useState, useEffect, useCallback, useMemo, memo, Fragment } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
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

/**
 * Rows worth looking at are shown; the rest collapse behind one button.
 *
 * The EV column is an index where 100 is break-even, so 95 is "5% worse than
 * fair". A long coupon is ~156 rows and most of them are priced against you or
 * unpriced altogether — scrolling past them to reach the handful above the line
 * is the whole cost of the list.
 *
 * An UNPRICED row (no fair odds, so no EV) collapses too: it carries no result
 * to read. It is still one tap away, which matters because unpriced usually
 * means an unmatched fixture worth correcting.
 */
const EV_FLOOR = 95

function evIndex(leg) {
  return leg.value == null ? null : 100 + leg.value * 100
}

function isAboveFloor(leg) {
  const ev = evIndex(leg)
  return ev != null && ev >= EV_FLOOR
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

const kickoffMs = leg =>
  leg.kickoff ? new Date(leg.kickoff + (leg.kickoff.endsWith('Z') ? '' : 'Z')).getTime() : Infinity

/**
 * Sections for the list: one per (status, coupon window).
 *
 * Splitting on the WINDOW as well as the status is what lets two coupons be
 * live at once. The windows deliberately overlap — Mon–Wed and Wed–Fri both
 * contain Wednesday, which is how the coupons are issued — so on a Wednesday
 * both are "active". Grouping on status alone merged them into a single list
 * with the labels concatenated and the ranks running across both, so there was
 * no way to see which set a row belonged to or to work one set at a time.
 *
 * Within a status the sets are ordered by their soonest remaining kickoff, so
 * the one closest to going off leads.
 */
function groupLegsByPeriod(legs) {
  const out = []
  for (const { status, label } of PERIOD_SECTIONS) {
    const items = legs.filter(l => (l.period_status ?? null) === status)
    if (!items.length) continue

    const byWindow = new Map()
    for (const l of items) {
      const key = l.period_label ?? ''
      if (!byWindow.has(key)) byWindow.set(key, [])
      byWindow.get(key).push(l)
    }

    const groups = [...byWindow.entries()].map(([window, ls]) => ({
      // Unique per section, so each set's expand state is its own.
      key: `${status ?? 'none'}|${window}`,
      status, label, window, legs: ls,
      soonest: Math.min(...ls.map(kickoffMs)),
    }))
    groups.sort((a, b) => a.soonest - b.soonest)
    out.push(...groups)
  }
  return out
}

const STATUS_LABEL = { active: 'Live now', upcoming: 'Next set', past: 'Ended' }

/** Short date range, e.g. "29–31 Jul". */
function fmtWindow(from, to) {
  if (!from) return ''
  const d = s => new Date(s + 'T00:00:00')
  const a = d(from)
  const mon = s => s.toLocaleString('en-GB', { month: 'short' })
  if (!to || to === from) return `${a.getDate()} ${mon(a)}`
  const b = d(to)
  return a.getMonth() === b.getMonth()
    ? `${a.getDate()}–${b.getDate()} ${mon(b)}`
    : `${a.getDate()} ${mon(a)} – ${b.getDate()} ${mon(b)}`
}

/**
 * The tracker's front door: every coupon type, and for each the issues that are
 * live or still to come.
 *
 * Two windows of one type can be live at once (Mon–Wed and Wed–Fri both contain
 * Wednesday), so each issue is its own row with its own status — that is the
 * thing the old dropdown could not show, since it named only the type.
 */
function TrackerLanding({ types, loading, onOpen }) {
  if (loading) return <p className={styles.loading}>Loading…</p>

  const withCoupons = types.filter(t => t.periods.length > 0)
  const empty = types.filter(t => t.periods.length === 0)

  return (
    <div className={styles.page}>
      <div className={styles.landingHead}>
        <span className={styles.landingTitle}>COUPONS</span>
        <Link to="/upload" className={styles.landingUpload}>Upload</Link>
      </div>

      <div className={styles.landingList}>
        {withCoupons.map(t => (
          <button key={t.code} className={styles.typeCard} onClick={() => onOpen(t.code)}>
            <span className={styles.typeTop}>
              <span className={styles.typeName}>{t.label}</span>
              <span className={`${styles.typeCount} ${styles.mono}`}>{t.selection_count}</span>
            </span>
            <span className={styles.typePeriods}>
              {t.periods.map(p => (
                <span key={p.coupon_id} className={styles.periodChip}>
                  {/* Status in WORDS as well as colour — a dot alone says
                      nothing on a phone in daylight. */}
                  <span className={`${styles.chipStatus} ${styles['st_' + (p.status ?? 'none')]}`}>
                    {STATUS_LABEL[p.status] ?? 'Unscheduled'}
                  </span>
                  <span className={styles.chipLabel}>{p.label ?? '—'}</span>
                  <span className={styles.chipWindow}>{fmtWindow(p.match_day, p.period_ends)}</span>
                  <span className={`${styles.chipCount} ${styles.mono}`} title="priced / total selections">
                    {p.priced_count}/{p.leg_count}
                  </span>
                </span>
              ))}
            </span>
          </button>
        ))}

        {empty.length > 0 && (
          <div className={styles.landingEmpty}>
            <span className={styles.landingEmptyHead}>No live coupon</span>
            {empty.map(t => (
              <button key={t.code} className={styles.emptyRow} onClick={() => onOpen(t.code)}>
                {t.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
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
    <div className={styles.legCard}>
      {editing ? (
        <LegEditForm leg={leg} saving={saving} onCancel={onCancelEdit} onSave={onSave} />
      ) : (
        <>
          <div className={styles.legTop}>
            <span className={`${styles.rank} ${styles.mono}`}>{rank}</span>
            <button className={styles.nameCell} onClick={() => onStartEdit(leg)}>
              <span className={styles.nameLine}>
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
  //
  // The tracker OPENS on the landing page (no code in the URL): which coupons
  // exist, which of their windows are live, and a button into each. Picking one
  // pushes /t/<code>, so the browser back button returns to the landing page
  // and a set can be linked to directly.
  const { code: routeCode } = useParams()
  const navigate = useNavigate()
  const activeCode = routeCode ?? null
  const [types, setTypes] = useState([])
  const [legs, setLegs] = useState([])
  const [pickerOpen, setPickerOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [editingId, setEditingId] = useState(null)
  // Which period sections have had their below-the-line rows revealed.
  const [expanded, setExpanded] = useState({})
  const [savingId, setSavingId] = useState(null)
  const [error, setError] = useState(null)

  const loadTypes = useCallback(
    () => getCouponTypes().then(setTypes).finally(() => setLoading(false)), [],
  )
  useEffect(() => { loadTypes() }, [loadTypes])

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
    setPickerOpen(false)
    navigate(`/t/${code}`)
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

  // No coupon chosen: the landing page. Which coupons exist, which of their
  // windows are live right now, and a way into each.
  if (!activeCode) {
    return <TrackerLanding types={types} loading={loading} onOpen={selectType} />
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <Link to="/" className={styles.backBtn} aria-label="All coupons">‹</Link>
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
          {legGroups.map(g => (
            <Fragment key={g.key}>
              <div className={`${styles.periodHead} ${styles['ph_' + (g.status ?? 'none')]}`}>
                <span className={styles.phStatus}>{g.label}</span>
                {g.window && <span className={styles.phWindow}>{g.window}</span>}
                <span className={`${styles.phCount} ${styles.mono}`}>{g.legs.length}</span>
              </div>
              {(() => {
                // A ladder coupon has no per-row EV to filter on — its rows
                // show fair odds instead — so it is never collapsed.
                const filterable = activeType?.prices_each_selection !== false
                const shown = (!filterable || expanded[g.key])
                  ? g.legs
                  : g.legs.filter(isAboveFloor)
                const hidden = g.legs.length - shown.length
                return (
                  <>
                    {shown.map((leg, i) => (
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
                    {hidden > 0 && (
                      <button
                        className={styles.expandRow}
                        onClick={() => setExpanded(e => ({ ...e, [g.key]: true }))}
                      >
                        Show {hidden} more — under {EV_FLOOR} EV or unpriced
                      </button>
                    )}
                  </>
                )
              })()}
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
