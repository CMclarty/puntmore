import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { getCardsFeed, getCardsDiagnostics } from '../api'
import f from './CardsPage.module.css'
import { DEFAULTS as ACCA_DEFAULTS, legScore } from '../accaInsurance'
import { boostLegScore } from '../accaBoost'

/**
 * Paddy — Paddy Power's corners, cards, goals and saves against fair prices.
 *
 * Three tabs, one list each, no controls (Callum, Oct 2026: "this is a tool —
 * keep it clean and informative"):
 *   Value   every bet at or above fair, best value first.
 *   Refund  the acca insurance offer (5-fold, legs 1.20+, stake back as a
 *           bonus if exactly one leg loses): every qualifying leg, best down.
 *   Boost   winnings +10/20/30% on a 4/5/6-fold, legs 1.20+: same.
 * Order is Kelly growth (accaInsurance.js / accaBoost.js). No acca is built or
 * recommended — the list is the tool, the picking is Callum's.
 */

// One fetch serves all three tabs: 95% of fair, because the acca offers carry
// value of their own and a leg a little under fair can still belong. The
// Value tab shows only fair-or-better from it.
const FETCH_EV = 0.95
const ACCA_FLOOR = ACCA_DEFAULTS.minOdds
const TABS = [['value', 'Value'], ['refund', 'Refund'], ['boost', 'Boost']]
const TAB_KEY = 'paddy.tab'

const shortPick = p => p.replace(/^Over /, 'O').replace(/^Under /, 'U')
const evPct = v => `${((1 + v) * 100).toFixed(1)}%`
function kickoff(iso) {
  const d = new Date(iso)
  const day = d.toDateString() === new Date().toDateString() ? ''
    : d.toLocaleDateString([], { weekday: 'short' }) + ' '
  return day + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
function loadRefundSettings() {
  try { return { ...ACCA_DEFAULTS, ...JSON.parse(localStorage.getItem('paddy.acca')) } } catch { return ACCA_DEFAULTS }
}

function Row({ bet }) {
  return (
    <div className={f.row}>
      <span className={f.what}>
        <span className={f.pick}>{shortPick(bet.pick)}</span>
        <span className={f.match}>{bet.match} · {kickoff(bet.commence_time)}</span>
      </span>
      <span className={f.odds}>{bet.odds.toFixed(2)}</span>
      <span className={`${f.value} ${bet.value >= 0.04 ? f.strong : ''}`}>{evPct(bet.value)}</span>
    </div>
  )
}

export default function CardsPage() {
  const [tab, setTab] = useState(() => {
    try { return localStorage.getItem(TAB_KEY) || 'value' } catch { return 'value' }
  })
  const [refundCfg] = useState(loadRefundSettings)
  const [items, setItems] = useState(null)
  const [error, setError] = useState(null)
  const [why, setWhy] = useState(null)
  const [updated, setUpdated] = useState(null)

  useEffect(() => {
    try { localStorage.setItem(TAB_KEY, tab) } catch { /* private mode */ }
  }, [tab])

  useEffect(() => {
    let live = true
    getCardsFeed(FETCH_EV - 1)
      .then(async data => {
        if (!live) return
        setItems(data)
        setUpdated(new Date())
        if (!data.length) {
          try { setWhy((await getCardsDiagnostics()).verdict) } catch { /* plain empty state */ }
        }
      })
      .catch(err => live && setError(err.message))
    return () => { live = false }
  }, [])

  // Every bet, flat, carrying its match and kick-off.
  const bets = (items || []).flatMap(item => item.bets.map(b => ({
    ...b, match: item.match, matchId: item.event_id, commence_time: item.commence_time,
  })))

  let body
  if (tab === 'value') {
    const list = bets.filter(b => b.value >= 0).sort((a, b) => b.value - a.value)
    body = list.length
      ? list.map(b => <Row key={b.matchId + b.pick} bet={b} />)
      : <p className={f.empty}>No bets at or above fair right now.</p>
  } else {
    // Every qualifying leg, best down. No acca is built or recommended.
    const score = tab === 'refund' ? b => legScore(b, refundCfg) : b => boostLegScore(b).growth
    const list = bets.filter(b => b.odds >= ACCA_FLOOR)
      .map(b => ({ b, s: score(b) })).sort((x, y) => y.s - x.s).map(x => x.b)
    body = list.length
      ? list.map(b => <Row key={b.matchId + b.pick} bet={b} />)
      : <p className={f.empty}>No legs at {ACCA_FLOOR}+ right now.</p>
  }

  return (
    <div className={f.page}>
      <header className={f.header}>
        <nav className={f.tabs} aria-label="Paddy">
          {TABS.map(([k, label]) => (
            <button key={k} type="button" className={`${f.tab} ${tab === k ? f.on : ''}`}
              aria-pressed={tab === k} onClick={() => setTab(k)}>{label}</button>
          ))}
        </nav>
        <span className={f.updated}>
          {updated ? updated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
          {tab === 'refund' && <Link className={f.gear} to="/paddy/acca" aria-label="Refund settings">⚙</Link>}
        </span>
      </header>

      {error && <p className={f.error}>{error}</p>}
      {!error && items === null && <p className={f.empty}>Loading prices…</p>}
      {!error && items && items.length === 0 && <p className={f.empty}>{why ?? 'No prices yet.'}</p>}
      {!error && items && items.length > 0 && (
        <div className={f.list}>
          <div className={`${f.row} ${f.colHead}`}><span>Bet</span><span>Odds</span><span>Value</span></div>
          {body}
        </div>
      )}
    </div>
  )
}
