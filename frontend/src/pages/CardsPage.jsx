import { useState, useEffect, useRef, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { getCardsFeed, getCardsDiagnostics } from '../api'
import ThresholdControl from '../components/ThresholdControl'
import styles from './FeedPage.module.css'
import f from './CardsPage.module.css'
import { DEFAULTS as ACCA_DEFAULTS, legScore } from '../accaInsurance'

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

// "Over 2.5 goals" → "O2.5 goals": the board's own shorthand, and narrower.
const shortPick = p => p.replace(/^Over /, 'O').replace(/^Under /, 'U')

const MARKETS = ['corners', 'cards', 'goals', 'saves']
const MAX_ODDS = [null, 2, 3, 5]          // null = any
// Kick-off window: within 1h or 3h, the rest of TODAY (local), or LATER than
// today. null = any.
const WITHIN = [[null, 'any'], [1, '< 1h'], [3, '< 3h'], ['today', 'Today'], ['later', 'Later']]
function inWindow(kickoff, within, now) {
  if (within == null) return true
  const ko = new Date(kickoff).getTime()
  const midnight = new Date(now); midnight.setHours(24, 0, 0, 0)
  if (within === 'today') return ko < midnight.getTime()
  if (within === 'later') return ko >= midnight.getTime()
  return ko - now <= within * 3600_000
}

// One tap sets the lot. EV is the server-side threshold; the rest filter the
// rows that come back. Touching any filter afterwards makes it "Custom".
// The "Acca Min" preset is for the acca insurance offer: a 5-fold, every leg
// 1.2 or longer, stake back (as a bonus) if exactly one leg loses. It ranks
// legs by the offer's own EV (accaInsurance.js::legScore) — value AND hit
// rate, because a short winner keeps the acca inside the one-loser refund —
// and lists every option by it, best first. The 1.2 floor
// is this preset's alone; every other preset shows shorter prices too.
// Its EV floor is LOW (95) on purpose: the refund carries value of its own,
// so a leg a little under fair can still make a positive insured acca.
const LOW_ODDS_FLOOR = ACCA_DEFAULTS.minOdds
const ACCA_KEY = 'paddy.acca'
function loadAcca() {
  try { return { ...ACCA_DEFAULTS, ...JSON.parse(localStorage.getItem(ACCA_KEY)) } } catch { return ACCA_DEFAULTS }
}
const pct = v => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'

const PRESETS = [
  { id: 'all', label: 'All', ev: DEFAULT_EV, markets: MARKETS, maxOdds: null, within: null },
  { id: 'low', label: 'Acca Min', ev: 95, markets: MARKETS, maxOdds: null, within: null,
    minOdds: LOW_ODDS_FLOOR, sort: 'combo' },
  { id: 'best', label: 'Best', ev: 105, markets: MARKETS, maxOdds: 5, within: null },
  { id: 'cc', label: 'Corners & cards', ev: 103, markets: ['corners', 'cards'], maxOdds: null, within: null },
  { id: 'goals', label: 'Goals', ev: 102, markets: ['goals'], maxOdds: null, within: null },
  { id: 'saves', label: 'Saves', ev: 103, markets: ['saves'], maxOdds: null, within: null },
  { id: 'soon', label: 'Starts in 3h', ev: 102, markets: MARKETS, maxOdds: null, within: 3 },
]
const STORE_KEY = 'cards.filters'

function loadFilters() {
  try {
    const v = JSON.parse(localStorage.getItem(STORE_KEY))
    if (v && Array.isArray(v.markets) && typeof v.ev === 'number') return v
  } catch { /* none saved, or private mode */ }
  return { ...PRESETS[0], preset: 'all', comp: null }
}

// "soccer_uefa_champs_league" → "Uefa Champs League"
const compLabel = k => (k || 'other').replace(/^soccer_?/, '').replace(/_/g, ' ')
  .replace(/\b\w/g, c => c.toUpperCase()) || 'Other'

export default function CardsPage() {
  const [filters, setFilters] = useState(loadFilters)
  const threshold = filters.ev
  const [showFilters, setShowFilters] = useState(false)
  // The offer's settings (legs, refund worth, cap) come from the calculator
  // page at /paddy/acca, which owns editing them.
  const [acca] = useState(loadAcca)
  const comboScore = b => legScore(b, acca)
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [fetchedAt, setFetchedAt] = useState(null)
  const [openId, setOpenId] = useState(null)
  // Only fetched when the feed comes back empty. An empty screen has several
  // causes and they look identical here — "no fixtures near kickoff" and "the
  // model found no value" are both normal, and neither should read as broken.
  const [why, setWhy] = useState(null)
  const debounceRef = useRef(null)

  const fetchFeed = useCallback(async (ev) => {
    setLoading(true)
    setError(null)
    try {
      const data = await getCardsFeed(ev / 100 - 1)
      setItems(data)
      setFetchedAt(new Date())
      setWhy(null)
      if (data.length === 0) {
        // Best effort: an explanation is a nicety, so its failure must not
        // surface as an error over a feed that loaded perfectly well.
        try {
          setWhy((await getCardsDiagnostics()).verdict)
        } catch { /* leave the plain empty state */ }
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchFeed(threshold) }, []) // eslint-disable-line

  useEffect(() => {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(filters)) } catch { /* private mode */ }
  }, [filters])


  function refetch(ev) {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => fetchFeed(ev), DEBOUNCE_MS)
  }

  function handleThresholdChange(val) {
    setFilters(x => ({ ...x, ev: val, preset: 'custom' }))
    refetch(val)
  }

  function applyPreset(p) {
    setFilters(x => ({ ...p, preset: p.id, comp: x.comp }))
    if (p.ev !== threshold) refetch(p.ev)
  }

  const edit = patch => setFilters(x => ({ ...x, ...patch, preset: 'custom' }))
  const toggleMarket = m => edit({
    markets: filters.markets.includes(m) ? filters.markets.filter(x => x !== m) : [...filters.markets, m],
  })

  // Client-side: market, odds, time to kick-off and competition. A match
  // stays only if one of its bets survives, and its badge is the best of those.
  const now = Date.now()
  const comps = [...new Set(items.map(i => i.sport_key || ''))].sort()
  const shown = items.map(item => {
    if (filters.comp != null && (item.sport_key || '') !== filters.comp) return null
    if (!inWindow(item.commence_time, filters.within, now)) return null
    const combo = filters.sort === 'combo'
    const bets = item.bets.filter(b =>
      (!b.market || filters.markets.includes(b.market)) &&
      (filters.maxOdds == null || b.odds <= filters.maxOdds) &&
      (filters.minOdds == null || b.odds >= filters.minOdds))
    if (!bets.length) return null
    if (combo) bets.sort((x, y) => comboScore(y) - comboScore(x))
    return {
      ...item, bets,
      best_value: Math.max(...bets.map(b => b.value)),
      rank: combo ? Math.max(...bets.map(comboScore)) : Math.max(...bets.map(b => b.value)),
    }
  }).filter(Boolean).sort((a, b) => b.rank - a.rank)


  function toggleRow(id) {
    setOpenId(prev => prev === id ? null : id)
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <span className={styles.title}>Paddy</span>
        {/* Remounted per preset so its text box shows the preset's figure. */}
        <ThresholdControl key={filters.preset} value={threshold} onChange={handleThresholdChange} />
      </div>

      <div className={f.presets} role="group" aria-label="Filter presets">
        {PRESETS.map(p => (
          <button key={p.id} type="button"
            className={`${f.chip} ${filters.preset === p.id ? f.on : ''}`}
            aria-pressed={filters.preset === p.id} onClick={() => applyPreset(p)}
          >{p.label}</button>
        ))}
        {filters.preset === 'custom' && <span className={`${f.chip} ${f.on} ${f.custom}`}>Custom</span>}
        <button type="button" className={f.more} onClick={() => setShowFilters(s => !s)}
          aria-expanded={showFilters}>
          {showFilters ? 'Hide filters ▴' : 'Filters ▾'}
        </button>
      </div>

      {showFilters && (
        <div className={f.panel}>
          <div className={f.row}>
            <span className={f.label}>Market</span>
            {MARKETS.map(m => (
              <button key={m} type="button" className={`${f.chip} ${filters.markets.includes(m) ? f.on : ''}`}
                aria-pressed={filters.markets.includes(m)} onClick={() => toggleMarket(m)}>{m}</button>
            ))}
          </div>
          <div className={f.row}>
            <span className={f.label}>Max odds</span>
            {MAX_ODDS.map(o => (
              <button key={o ?? 'any'} type="button" className={`${f.chip} ${filters.maxOdds === o ? f.on : ''}`}
                aria-pressed={filters.maxOdds === o} onClick={() => edit({ maxOdds: o })}>{o ?? 'any'}</button>
            ))}
          </div>
          <div className={f.row}>
            <span className={f.label}>Min odds</span>
            {[null, LOW_ODDS_FLOOR].map(o => (
              <button key={o ?? 'any'} type="button" className={`${f.chip} ${(filters.minOdds ?? null) === o ? f.on : ''}`}
                aria-pressed={(filters.minOdds ?? null) === o} onClick={() => edit({ minOdds: o })}>{o ?? 'any'}</button>
            ))}
          </div>
          <div className={f.row}>
            <span className={f.label}>Sort by</span>
            {[['value', 'Value'], ['combo', 'Acca insurance EV']].map(([k, l]) => (
              <button key={k} type="button" className={`${f.chip} ${(filters.sort ?? 'value') === k ? f.on : ''}`}
                aria-pressed={(filters.sort ?? 'value') === k} onClick={() => edit({ sort: k })}>{l}</button>
            ))}
          </div>
          <div className={f.row}>
            <span className={f.label}>Kick-off</span>
            {WITHIN.map(([w, label]) => (
              <button key={label} type="button" className={`${f.chip} ${filters.within === w ? f.on : ''}`}
                aria-pressed={filters.within === w} onClick={() => edit({ within: w })}>{label}</button>
            ))}
          </div>
          <Link className={f.calcLink} to="/paddy/acca">Acca insurance calculator & settings ›</Link>
          <div className={f.row}>
            <span className={f.label}>Competition</span>
            <select className={f.select} value={filters.comp ?? ''}
              onChange={e => setFilters(x => ({ ...x, comp: e.target.value === '' ? null : e.target.value }))}>
              <option value="">All</option>
              {comps.map(c => <option key={c} value={c}>{compLabel(c)}</option>)}
            </select>
          </div>
        </div>
      )}

      {fetchedAt && (
        <p className={styles.meta}>
          {loading ? 'Refreshing…' : filters.sort === 'combo'
            ? (n => `${n} option${n !== 1 ? 's' : ''} · `)(shown.reduce((k, i) => k + i.bets.length, 0))
            : ''}
          {!loading && `${shown.length} match${shown.length !== 1 ? 'es' : ''}`}
          {!loading && shown.length !== items.length ? ` of ${items.length}` : ''}
          {' · '}
          {fetchedAt.toLocaleTimeString()}
          {' · '}cached 24 h
        </p>
      )}
      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.feed}>
        {/* Acca insurance mode: every option as its own row, best score first
            — the ranking IS the answer, so it is not hidden inside matches. */}
        {filters.sort === 'combo' && shown.length > 0 && (
          <div className={`${f.flatRow} ${f.colHead}`}>
            <span title={`EV of an insured ${acca.legs}-fold of legs like this`}>Score</span>
            <span>Bet</span><span>Odds</span><span className={f.right}>Value</span>
          </div>
        )}
        {filters.sort === 'combo' && shown
          .flatMap(item => item.bets.map(bet => ({ bet, item, score: comboScore(bet) })))
          .sort((x, y) => y.score - x.score)
          .map(({ bet, item, score }) => (
            <div key={item.event_id + bet.pick} className={f.flatRow}>
              <span className={f.flatScore} title={`EV of an insured ${acca.legs}-fold of legs like this`}>
                {pct(score)}
              </span>
              <span className={f.flatWhat}>
                <span className={f.flatPick}>{shortPick(bet.pick)}</span>
                <span className={f.flatMatch}>{item.match}</span>
              </span>
              <span className={`${styles.odds} ${styles.mono}`}>{bet.odds.toFixed(2)}</span>
              <span className={`${styles.badge} ${styles[valueTier(bet.value)]}`}>{fmt(bet.value)}</span>
            </div>
          ))}
        {filters.sort !== 'combo' && shown.length > 0 && (
          <div className={f.matchHead}>
            <span>Match</span><span>Best value</span>
          </div>
        )}
        {filters.sort !== 'combo' && shown.map((item) => {
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
                      <span className={styles.pick}>{shortPick(bet.pick)}</span>
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
        {!loading && items.length > 0 && shown.length === 0 && (
          <p className={styles.empty}>Nothing matches these filters.</p>
        )}
      </div>
    </div>
  )
}
