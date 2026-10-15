import { useState, useEffect, useRef, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getCardsFeed, getCardsDiagnostics } from '../api'
import ThresholdControl from '../components/ThresholdControl'
import styles from './FeedPage.module.css'
import f from './CardsPage.module.css'
import { DEFAULTS as ACCA_DEFAULTS, bestAcca, growthOf, legAcca, legScore, trueProb } from '../accaInsurance'
import { bestBoostAcca, boostLegScore } from '../accaBoost'

/**
 * Paddy — Paddy Power's corners, cards, goals and saves against fair prices.
 *
 * THREE TABS, one per job (Oct 2026; it had grown eight presets and a
 * seven-row filter panel that mixed offers with plain filters):
 *   Value        single bets. Sort by value (grouped by match), by growth as
 *                a single (Kelly, so a long value bet ranks on its merits),
 *                or shortest odds first. Its own EV threshold and odds range.
 *   Acca Refund  the acca insurance offer (5-fold, 1.20+, stake back as a
 *                bonus if exactly one leg loses) — accaInsurance.js.
 *   Acca Boost   winnings +10/20/30% on a 4/5/6-fold, 1.20+ — accaBoost.js.
 * Both acca tabs list every option ranked by GROWTH — the expected log-growth
 * of an acca with that leg at the acca's own optimal stake — under the best
 * qualifying acca. Money in the pocket over theoretical EV (Callum's call).
 *
 * ONE chip row under the tabs filters all three: markets, kick-off, league.
 */

const MARKETS = ['corners', 'cards', 'goals', 'saves']
const MAX_ODDS = [null, 1.5, 2, 3, 5]
const MIN_ODDS = [null, 1.2]
const ACCA_FLOOR = ACCA_DEFAULTS.minOdds          // both offers: every leg 1.20+
// The acca tabs fetch down to 95% of fair: the refund / boost carries value of
// its own, so a leg a little under fair can still belong in a good acca.
const ACCA_EV = 95
const DEFAULT_EV = 98

// Kick-off window: within 1h or 3h, the rest of TODAY (local), or LATER.
const WITHIN = [[null, 'Any'], [1, '< 1h'], [3, '< 3h'], ['today', 'Today'], ['later', 'Later']]
function inWindow(kickoff, within, now) {
  if (within == null) return true
  const ko = new Date(kickoff).getTime()
  const midnight = new Date(now); midnight.setHours(24, 0, 0, 0)
  if (within === 'today') return ko < midnight.getTime()
  if (within === 'later') return ko >= midnight.getTime()
  return ko - now <= within * 3600_000
}

const TABS = [['value', 'Value'], ['refund', 'Acca Refund'], ['boost', 'Acca Boost']]
const SORTS = [['value', 'Value'], ['single', 'Growth'], ['shortest', 'Shortest']]

const VIEW_KEY = 'paddy.view'
const DEFAULT_VIEW = {
  tab: 'value',
  markets: MARKETS, within: null, comp: null,               // shared
  value: { ev: DEFAULT_EV, sort: 'value', minOdds: null, maxOdds: null },
}
function loadView() {
  try {
    const v = JSON.parse(localStorage.getItem(VIEW_KEY))
    if (v && Array.isArray(v.markets) && v.value) return { ...DEFAULT_VIEW, ...v }
  } catch { /* none saved, or private mode */ }
  return DEFAULT_VIEW
}
function loadRefundSettings() {
  try { return { ...ACCA_DEFAULTS, ...JSON.parse(localStorage.getItem('paddy.acca')) } } catch { return ACCA_DEFAULTS }
}

function valueTier(v) {
  if (v >= 0.06) return 'hi'
  if (v >= 0.04) return 'mid'
  if (v > 0) return 'lo'
  return 'neg'
}
const fmt = v => `${((1 + v) * 100).toFixed(1)}%`                 // edge → %EV
const pct = v => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'
const growthPct = v => (v * 100).toFixed(2) + '%'                // a few %: 2dp
const shortPick = p => p.replace(/^Over /, 'O').replace(/^Under /, 'U')
const compLabel = k => (k || 'other').replace(/^soccer_?/, '').replace(/_/g, ' ')
  .replace(/\b\w/g, c => c.toUpperCase()) || 'Other'
// A bet's growth placed on its own, at its Kelly stake.
const singleGrowth = b => {
  const p = trueProb(b)
  return growthOf([[p, b.odds - 1], [1 - p, -1]])
}

export default function CardsPage() {
  const navigate = useNavigate()
  const [view, setView] = useState(loadView)
  const [refundCfg] = useState(loadRefundSettings)
  const [showOdds, setShowOdds] = useState(false)
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [fetchedAt, setFetchedAt] = useState(null)
  const [openId, setOpenId] = useState(null)
  // Fetched only when the feed comes back empty: an empty screen has several
  // causes, and "nothing near kickoff" or "no value" must not read as broken.
  const [why, setWhy] = useState(null)
  const debounceRef = useRef(null)

  const tab = view.tab
  const acca = tab !== 'value'
  const ev = acca ? ACCA_EV : view.value.ev

  const fetchFeed = useCallback(async (evPct) => {
    setLoading(true)
    setError(null)
    try {
      const data = await getCardsFeed(evPct / 100 - 1)
      setItems(data)
      setFetchedAt(new Date())
      setWhy(null)
      if (data.length === 0) {
        try { setWhy((await getCardsDiagnostics()).verdict) } catch { /* plain empty state */ }
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  // The server applies the EV floor, so a tab with a different floor refetches.
  useEffect(() => {
    clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => fetchFeed(ev), fetchedAt ? 400 : 0)
  }, [ev]) // eslint-disable-line

  useEffect(() => {
    try { localStorage.setItem(VIEW_KEY, JSON.stringify(view)) } catch { /* private mode */ }
  }, [view])

  const set = patch => setView(v => ({ ...v, ...patch }))
  const setValue = patch => setView(v => ({ ...v, value: { ...v.value, ...patch } }))
  const toggleMarket = m => set({
    markets: view.markets.includes(m) ? view.markets.filter(x => x !== m) : [...view.markets, m],
  })

  // --- What this tab shows ---------------------------------------------------
  const now = Date.now()
  const comps = [...new Set(items.map(i => i.sport_key || ''))].sort()
  const minOdds = acca ? ACCA_FLOOR : view.value.minOdds
  const maxOdds = acca ? null : view.value.maxOdds
  const shown = items.map(item => {
    if (view.comp != null && (item.sport_key || '') !== view.comp) return null
    if (!inWindow(item.commence_time, view.within, now)) return null
    const bets = item.bets.filter(b =>
      (!b.market || view.markets.includes(b.market)) &&
      (maxOdds == null || b.odds <= maxOdds) &&
      (minOdds == null || b.odds >= minOdds))
    if (!bets.length) return null
    return { ...item, bets, best_value: Math.max(...bets.map(b => b.value)) }
  }).filter(Boolean).sort((a, b) => b.best_value - a.best_value)

  // Every view but "Value by value" is one row per option.
  const sort = acca ? tab : view.value.sort
  const flat = sort !== 'value'
  const rows = !flat ? [] : shown
    .flatMap(item => item.bets.map(bet => {
      if (sort === 'boost') {
        const r = boostLegScore(bet)                 // paid = the acca wins
        return { bet, item, score: r.growth, paid: r.pWin }
      }
      if (sort === 'refund') {
        const r = legAcca(bet, refundCfg)            // paid = win or refund
        return { bet, item, score: legScore(bet, refundCfg), paid: r.pWin + r.pRefund }
      }
      return { bet, item, score: singleGrowth(bet).growth, paid: trueProb(bet) }
    }))
    .sort(sort === 'shortest'
      ? (x, y) => x.bet.odds - y.bet.odds || y.bet.value - x.bet.value
      : (x, y) => y.score - x.score)
  const candidates = rows.map(({ bet, item }) => ({ ...bet, matchId: item.event_id, match: item.match }))
  const best = tab === 'refund' ? bestAcca(candidates, refundCfg)
    : tab === 'boost' ? bestBoostAcca(candidates)
    : null
  const bestKeys = new Set((best?.picks || []).map(p => p.matchId + p.pick))
  const paidTip = tab === 'refund' ? 'How often an acca with this leg pays back (win or refund)'
    : tab === 'boost' ? 'How often an acca with this leg wins'
    : 'Chance this bet wins'
  const scoreTip = acca
    ? 'Growth per bet of an acca with this leg, at its optimal stake — higher is better'
    : 'Growth per bet placed on its own, at its optimal (Kelly) stake'
  const options = shown.reduce((k, i) => k + i.bets.length, 0)

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <span className={styles.title}>Paddy</span>
        {tab === 'value' && (
          <ThresholdControl key="value" value={view.value.ev} onChange={v => setValue({ ev: v })} />
        )}
        {tab === 'refund' && (
          <Link className={f.gear} to="/paddy/acca" aria-label="Refund settings and calculator">⚙</Link>
        )}
      </div>

      <nav className={f.tabs} aria-label="Paddy views">
        {TABS.map(([k, label]) => (
          <button key={k} type="button" className={`${f.tab} ${tab === k ? f.tabOn : ''}`}
            aria-pressed={tab === k} onClick={() => set({ tab: k })}>{label}</button>
        ))}
      </nav>

      {/* Shared by all three tabs. */}
      <div className={f.chips} role="group" aria-label="Filters">
        {MARKETS.map(m => (
          <button key={m} type="button" className={`${f.chip} ${view.markets.includes(m) ? f.on : ''}`}
            aria-pressed={view.markets.includes(m)} onClick={() => toggleMarket(m)}>{m}</button>
        ))}
        <span className={f.sep} />
        {WITHIN.map(([w, label]) => (
          <button key={label} type="button" className={`${f.chip} ${view.within === w ? f.on : ''}`}
            aria-pressed={view.within === w} onClick={() => set({ within: w })}>{label}</button>
        ))}
        <span className={f.sep} />
        <select className={f.select} value={view.comp ?? ''} aria-label="League"
          onChange={e => set({ comp: e.target.value === '' ? null : e.target.value })}>
          <option value="">All leagues</option>
          {comps.map(c => <option key={c} value={c}>{compLabel(c)}</option>)}
        </select>
      </div>

      {tab === 'value' && (
        <div className={f.subbar}>
          {SORTS.map(([k, label]) => (
            <button key={k} type="button" className={`${f.chip} ${view.value.sort === k ? f.on : ''}`}
              aria-pressed={view.value.sort === k} onClick={() => setValue({ sort: k })}>{label}</button>
          ))}
          <button type="button" className={f.more} onClick={() => setShowOdds(s => !s)} aria-expanded={showOdds}>
            Odds{view.value.minOdds || view.value.maxOdds
              ? ` ${view.value.minOdds ?? ''}–${view.value.maxOdds ?? ''}` : ''} {showOdds ? '▴' : '▾'}
          </button>
        </div>
      )}
      {tab === 'value' && showOdds && (
        <div className={f.subbar}>
          <span className={f.label}>Min</span>
          {MIN_ODDS.map(o => (
            <button key={o ?? 'any'} type="button" className={`${f.chip} ${view.value.minOdds === o ? f.on : ''}`}
              onClick={() => setValue({ minOdds: o })}>{o ?? 'any'}</button>
          ))}
          <span className={f.label}>Max</span>
          {MAX_ODDS.map(o => (
            <button key={o ?? 'any'} type="button" className={`${f.chip} ${view.value.maxOdds === o ? f.on : ''}`}
              onClick={() => setValue({ maxOdds: o })}>{o ?? 'any'}</button>
          ))}
        </div>
      )}

      <p className={styles.meta}>
        {loading && !fetchedAt ? 'Loading prices…'
          : loading ? 'Refreshing…'
          : fetchedAt ? `${flat ? `${options} option${options !== 1 ? 's' : ''} · ` : ''}${shown.length} match${shown.length !== 1 ? 'es' : ''} · updated ${fetchedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
          : ''}
        {acca && fetchedAt && !loading ? ` · legs ${ACCA_FLOOR}+` : ''}
      </p>
      {error && <p className={styles.error}>{error}</p>}
      {!loading && !error && fetchedAt && items.length === 0 && (
        <p className={styles.empty}>{why ?? 'No value bets at this threshold.'}</p>
      )}

      <div className={styles.feed}>
        {best && (
          <button type="button" className={`${f.flatRow} ${f.bestRow}`} disabled={tab === 'boost'}
            onClick={() => navigate('/paddy/acca', {
              state: { legs: best.picks.map(l => ({ odds: l.odds, fair: l.odds / (1 + l.value), name: `${l.match} · ${l.pick}` })) },
            })}>
            <span className={f.flatScore}>{growthPct(best.growth)}</span>
            <span className={f.flatWhat}>
              <span className={f.flatPick}>★ Best {best.picks.length}-fold{best.boost ? ` · +${Math.round(best.boost * 100)}% boost` : ''}</span>
              <span className={f.flatMatch}>EV {pct(best.evPct)} · Kelly {Math.round(best.kelly * 100)}%</span>
            </span>
            <span className={f.oddsCell}>
              <span className={`${styles.odds} ${styles.mono}`}>{best.accaOdds.toFixed(2)}</span>
              <span className={f.paid}>{Math.round((best.pWin + (best.pRefund || 0)) * 100)}%</span>
            </span>
            <span className={f.right}>{tab === 'refund' ? '›' : ''}</span>
          </button>
        )}

        {flat && rows.length > 0 && (
          <div className={`${f.flatRow} ${f.colHead}`}>
            <span title={scoreTip}>{sort === 'shortest' ? 'Single' : 'Growth'}</span>
            <span>Bet</span><span title={paidTip}>Odds</span><span className={f.right}>Value</span>
          </div>
        )}
        {flat && rows.map(({ bet, item, score, paid }) => (
          <div key={item.event_id + bet.pick} className={f.flatRow}>
            <span className={f.flatScore} title={scoreTip}>{growthPct(score)}</span>
            <span className={f.flatWhat}>
              <span className={f.flatPick}>{bestKeys.has(item.event_id + bet.pick) ? '★ ' : ''}{shortPick(bet.pick)}</span>
              <span className={f.flatMatch}>{item.match}</span>
            </span>
            <span className={f.oddsCell}>
              <span className={`${styles.odds} ${styles.mono}`}>{bet.odds.toFixed(2)}</span>
              <span className={f.paid} title={paidTip}>{Math.round(paid * 100)}%</span>
            </span>
            <span className={`${styles.badge} ${styles[valueTier(bet.value)]}`}>{fmt(bet.value)}</span>
          </div>
        ))}

        {!flat && shown.length > 0 && (
          <div className={f.matchHead}><span>Match</span><span>Best value</span></div>
        )}
        {!flat && shown.map(item => {
          const isOpen = openId === item.event_id
          return (
            <div key={item.event_id} className={styles.matchBlock}>
              <button className={`${styles.matchRow} ${isOpen ? styles.matchRowOpen : ''}`}
                onClick={() => setOpenId(id => id === item.event_id ? null : item.event_id)}>
                <span className={styles.match}>{item.match}</span>
                <span className={`${styles.badge} ${styles[valueTier(item.best_value)]}`}>{fmt(item.best_value)}</span>
                <span className={`${styles.chevron} ${isOpen ? styles.chevronOpen : ''}`}>›</span>
              </button>
              {isOpen && (
                <div className={styles.bets}>
                  {[...item.bets].sort((a, b) => b.value - a.value).map((bet, i) => (
                    <div key={i} className={styles.betRow}>
                      <span className={styles.pick}>{shortPick(bet.pick)}</span>
                      <span className={`${styles.odds} ${styles.mono}`}>{bet.odds.toFixed(2)}</span>
                      <span className={`${styles.badge} ${styles[valueTier(bet.value)]}`}>{fmt(bet.value)}</span>
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
