import { useState, useEffect, useRef, useCallback } from 'react'
import { getCardsFeed, getCardsDiagnostics } from '../api'
import f from './CardsPage.module.css'
import MarketIcon from '../components/MarketIcon'
import useAutoRefresh from '../hooks/useAutoRefresh'
import { DEFAULTS as ACCA_DEFAULTS, evaluate, legScore, trueProb } from '../accaInsurance'
import { boostEval, boostLegScore } from '../accaBoost'

/**
 * Paddy — Paddy Power's corners, cards, goals and saves against fair prices.
 *
 * One feed. The offer (Value / Refund / Boost) and every filter live in a single
 * folded options pane; the collapsed line names the active offer and what is
 * applied:
 *   Value   every bet at or above fair, best value first.
 *   Refund  the acca insurance offer (5-fold, legs 1.20+, stake back as a
 *           bonus if exactly one leg loses): every qualifying leg, best down.
 *   Boost   winnings +10/20/30% on a 4/5/6-fold, legs 1.20+: same.
 * Order is Kelly growth (accaInsurance.js / accaBoost.js). Each row shows the
 * Paddy odds, the value %, and the fair odds. No acca is built or recommended —
 * the list is the tool, the picking is Callum's.
 */

// One fetch serves all three offers: 95% of fair, because the acca offers carry
// value of their own and a leg a little under fair can still belong. The
// Value offer shows only fair-or-better from it.
const FETCH_EV = 0.95
const ACCA_FLOOR = ACCA_DEFAULTS.minOdds
const OFFERS = [['value', 'Value'], ['refund', 'Refund'], ['boost', 'Boost']]
const OFFER_KEY = 'paddy.tab'
// Boost's suggested stake: a tenth of full Kelly on a £50k bankroll (Callum).
const BOOST_BANKROLL = 50_000
const BOOST_KELLY = 0.10

// Filters: folded away under one line that says what is applied, so the usual
// visit is open → set the offer → bet. Shared across offers, remembered.
const MARKETS = ['corners', 'cards', 'goals', 'saves', 'team_corners', 'team_cards']
const MARKET_LABELS = { team_corners: 'Team corners', team_cards: 'Team cards' }
const marketLabel = m => MARKET_LABELS[m] || m[0].toUpperCase() + m.slice(1)
const WITHIN = [[null, 'Any'], [1, '< 1h'], [3, '< 3h'], ['today', 'Today'], [24, '24h'], [48, '48h'], [72, '72h']]
const MAX_ODDS = [null, 1.5, 2, 3, 5]
const MIN_VALUE = [0, 0.02, 0.04, 0.06]               // Value offer only
const SORTS = [['best', 'Best'], ['shortest', 'Shortest']]
const PER_MATCH = [[false, 'All legs'], [true, '1 per match']]   // Refund / Boost only
// Free-bet EV rate: a free bet's stake isn't returned, so you keep (odds-1)/odds
// of a win — higher odds retain more. Scaled by the value edge. Value offer only.
const freeBetScore = b => (1 + b.value) * (b.odds - 1) / b.odds
const FILTER_KEY = 'paddy.filters'
const NO_FILTERS = { markets: MARKETS, within: null, maxOdds: null, minValue: 0, sort: 'best', onePerMatch: false }
function loadFilters() {
  try {
    const v = JSON.parse(localStorage.getItem(FILTER_KEY))
    // `comp` was a league filter (removed); a saved one must not hide rows.
    if (v && Array.isArray(v.markets)) { const { comp, ...rest } = v; return { ...NO_FILTERS, ...rest } } // eslint-disable-line no-unused-vars
  } catch { /* none saved, or private mode */ }
  return NO_FILTERS
}
function inWindow(iso, within, now) {
  if (within == null) return true
  const ko = new Date(iso).getTime()
  const midnight = new Date(now); midnight.setHours(24, 0, 0, 0)
  if (within === 'today') return ko < midnight.getTime()
  return ko - now <= within * 3600_000
}
// What is applied, in a few words; empty when nothing is.
function summary(fl, offer) {
  const bits = []
  if (fl.markets.length < MARKETS.length) bits.push(fl.markets.length ? fl.markets.map(marketLabel).join(', ') : 'no markets')
  if (fl.within != null) bits.push(WITHIN.find(([w]) => w === fl.within)[1])
  if (fl.maxOdds != null) bits.push(`≤ ${fl.maxOdds.toFixed(2)}`)
  if (offer === 'value' && fl.minValue > 0) bits.push(`${(100 + fl.minValue * 100).toFixed(0)}%+`)
  if (fl.sort === 'shortest') bits.push('shortest first')
  if (fl.sort === 'freebet' && offer === 'value') bits.push('free bet')
  if (offer !== 'value' && fl.onePerMatch) bits.push('1 per match')
  return bits.join(' · ')
}

function Chips({ options, isOn, onPick, icons = false }) {
  return options.map(([k, label]) => (
    <button key={label} type="button" className={`${f.chip} ${isOn(k) ? f.chipOn : ''}`}
      aria-pressed={isOn(k)} onClick={() => onPick(k)}>
      {icons && <MarketIcon market={k} size={15} />}{label}
    </button>
  ))
}

const shortPick = p => p.replace(/^Over /, 'O').replace(/^Under /, 'U')
const evPct = v => `${((1 + v) * 100).toFixed(1)}%`
function kickoff(iso) {
  const d = new Date(iso)
  const day = d.toDateString() === new Date().toDateString() ? ''
    : d.toLocaleDateString([], { weekday: 'short' }) + ' '
  return day + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function Row({ bet }) {
  return (
    <div className={f.row}>
      <MarketIcon market={bet.market} size={22} />
      <span className={f.what}>
        <span className={f.pick}>{shortPick(bet.pick)}</span>
        <span className={f.match}>{bet.match} · {kickoff(bet.commence_time)}</span>
      </span>
      <span className={f.odds}>{bet.odds.toFixed(2)}</span>
      <span className={`${f.value} ${bet.value >= 0.04 ? f.strong : ''}`}>{evPct(bet.value)}</span>
      <span className={f.fair}>{bet.fair.toFixed(2)}</span>
    </div>
  )
}

export default function CardsPage() {
  const [offer, setOffer] = useState(() => {
    try { return localStorage.getItem(OFFER_KEY) || 'value' } catch { return 'value' }
  })
  const [fl, setFl] = useState(loadFilters)
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState(null)
  const [error, setError] = useState(null)
  const [why, setWhy] = useState(null)

  useEffect(() => {
    try { localStorage.setItem(OFFER_KEY, offer) } catch { /* private mode */ }
  }, [offer])
  useEffect(() => {
    try { localStorage.setItem(FILTER_KEY, JSON.stringify(fl)) } catch { /* private mode */ }
  }, [fl])
  const setF = patch => setFl(v => ({ ...v, ...patch }))

  // Load, then refresh in place (useAutoRefresh). A failed refresh hides the
  // list rather than leave old prices on screen.
  const live = useRef(true)
  const load = useCallback(() => getCardsFeed(FETCH_EV - 1)
    .then(async data => {
      if (!live.current) return
      setItems(data)
      setError(null)
      if (!data.length) {
        try { setWhy((await getCardsDiagnostics()).verdict) } catch { /* plain empty state */ }
      }
    })
    .catch(err => live.current && setError(err.message)), [])
  useEffect(() => {
    live.current = true
    load()
    return () => { live.current = false }
  }, [load])
  useAutoRefresh(load)

  // Every bet, flat, carrying its match and kick-off, through the filters.
  const now = Date.now()
  const bets = (items || [])
    .filter(item => inWindow(item.commence_time, fl.within, now))
    .flatMap(item => item.bets.map(b => ({
      ...b, match: item.match, matchId: item.event_id, commence_time: item.commence_time,
    })))
    .filter(b => (!b.market || fl.markets.includes(b.market))
      && (fl.maxOdds == null || b.odds <= fl.maxOdds))

  // Best down: value on the Value offer, Kelly growth in the offer on the others.
  const refund = offer === 'refund'
  const list = offer === 'value'
    ? bets.filter(b => b.value >= fl.minValue).map(b => ({ b, s: b.value }))
    : bets.filter(b => b.odds >= ACCA_FLOOR)
      .map(b => ({ b, s: refund ? legScore(b, ACCA_DEFAULTS) : boostLegScore(b).growth }))
  // Refund / Boost: one selection per match — the highest-scoring one, since
  // an acca takes a single leg from each game.
  if (offer !== 'value' && fl.onePerMatch) {
    const top = new Map()
    for (const x of list) if (!top.has(x.b.matchId) || x.s > top.get(x.b.matchId).s) top.set(x.b.matchId, x)
    list.splice(0, list.length, ...top.values())
  }
  list.sort(
    fl.sort === 'shortest' ? (x, y) => x.b.odds - y.b.odds || y.s - x.s
    : fl.sort === 'freebet' && offer === 'value' ? (x, y) => freeBetScore(y.b) - freeBetScore(x.b)
    : (x, y) => y.s - x.s)

  const applied = summary(fl, offer)
  const offerLabel = OFFERS.find(([k]) => k === offer)[1]

  // Refund / Boost: the acca of the top 5 by score, one per match (asked for:
  // "a number to see at a glance if it's worth betting").
  let top5 = null
  if (offer !== 'value') {
    const seen = new Set(), picks = []
    for (const x of [...list].sort((a, b) => b.s - a.s)) {
      if (seen.has(x.b.matchId)) continue
      seen.add(x.b.matchId); picks.push(x.b)
      if (picks.length === 5) break
    }
    if (picks.length === 5) {
      const odds = picks.map(b => b.odds), probs = picks.map(trueProb)
      const r = refund ? evaluate({ odds, probs }, ACCA_DEFAULTS) : boostEval(odds, probs)
      top5 = { odds: r.accaOdds, ev: r.evPct,
        stake: refund ? null : Math.round(BOOST_KELLY * r.kelly * BOOST_BANKROLL) }
    }
  }
  const body = list.length
    ? list.map(({ b }) => <Row key={b.matchId + b.pick} bet={b} />)
    : <p className={f.empty}>{applied ? 'Nothing matches these options.'
      : offer === 'value' ? 'No bets at or above fair right now.' : `No legs at ${ACCA_FLOOR}+ right now.`}</p>

  return (
    <div className={f.page}>
      <div className={`${f.filterBar} ${applied ? f.filtered : ''}`}>
        <button type="button" className={f.summary} aria-expanded={open} onClick={() => setOpen(o => !o)}>
          {applied ? `${offerLabel} · ${applied}` : offerLabel}
        </button>
        <button type="button" className={f.filterToggle} aria-expanded={open} onClick={() => setOpen(o => !o)}>
          Options {open ? '▴' : '▾'}
        </button>
      </div>
      {open && (
        <div className={f.panel}>
          <div className={f.group} role="group" aria-label="Offer">
            <span className={f.label}>Offer</span>
            <Chips options={OFFERS} isOn={k => offer === k} onPick={setOffer} />
          </div>
          <div className={f.group} role="group" aria-label="Market">
            <Chips icons options={MARKETS.map(m => [m, marketLabel(m)])} isOn={m => fl.markets.includes(m)}
              onPick={m => setF({ markets: fl.markets.includes(m) ? fl.markets.filter(x => x !== m) : [...fl.markets, m] })} />
          </div>
          <div className={f.group}><span className={f.label}>Kick-off</span>
            <Chips options={WITHIN} isOn={w => fl.within === w} onPick={w => setF({ within: w })} />
          </div>
          <div className={f.group}><span className={f.label}>Max odds</span>
            <Chips options={MAX_ODDS.map(o => [o, o == null ? 'Any' : o.toFixed(1)])}
              isOn={o => fl.maxOdds === o} onPick={o => setF({ maxOdds: o })} />
          </div>
          {offer === 'value' && (
            <div className={f.group}><span className={f.label}>Min value</span>
              <Chips options={MIN_VALUE.map(v => [v, v ? `${100 + v * 100}%` : 'Fair'])}
                isOn={v => fl.minValue === v} onPick={v => setF({ minValue: v })} />
            </div>
          )}
          {offer !== 'value' && (
            <div className={f.group}><span className={f.label}>Per match</span>
              <Chips options={PER_MATCH} isOn={v => fl.onePerMatch === v} onPick={v => setF({ onePerMatch: v })} />
            </div>
          )}
          <div className={f.group}><span className={f.label}>Sort</span>
            <Chips options={offer === 'value' ? [...SORTS, ['freebet', 'Free bet']] : SORTS}
              isOn={k => fl.sort === k} onPick={k => setF({ sort: k })} />
            {applied && <button type="button" className={f.reset} onClick={() => setF(NO_FILTERS)}>Reset</button>}
          </div>
        </div>
      )}

      {error && <p className={f.error}>{error}</p>}
      {!error && items === null && <p className={f.empty}>Loading prices…</p>}
      {!error && items && items.length === 0 && <p className={f.empty}>{why ?? 'No prices yet.'}</p>}
      {!error && items && items.length > 0 && (
        <div className={f.list}>
          {top5 && (
            <div className={f.top5}>
              <span>Top 5</span>
              <span className={f.top5Odds}>{top5.odds.toFixed(2)}</span>
              <span className={f.top5Ev}>EV {top5.ev >= 0 ? '+' : '−'}{Math.abs(top5.ev * 100).toFixed(1)}%</span>
              {top5.stake != null && <span className={f.top5Stake}>£{top5.stake.toLocaleString('en-GB')}</span>}
            </div>
          )}
          <div className={`${f.row} ${f.colHead}`}>
            <span /><span>Bet</span><span>Odds</span><span>Value</span><span>Fair</span>
          </div>
          {body}
        </div>
      )}
    </div>
  )
}
