import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { getCardsFeed, getCardsDiagnostics } from '../api'
import f from './CardsPage.module.css'
import MarketIcon from '../components/MarketIcon'
import { DEFAULTS as ACCA_DEFAULTS, legScore } from '../accaInsurance'
import { boostLegScore } from '../accaBoost'

/**
 * Paddy — Paddy Power's corners, cards, goals and saves against fair prices.
 *
 * Three tabs, one list each (Callum, Oct 2026: "this is a tool — keep it clean
 * and informative"; open, tap one of three, bet in seconds). Filters are
 * folded under one line that names what is applied:
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

// Filters: folded away under one line that says what is applied, so the usual
// visit is open → tap a tab → bet. Shared by all three tabs, remembered.
const MARKETS = ['corners', 'cards', 'goals', 'saves']
const WITHIN = [[null, 'Any'], [1, '< 1h'], [3, '< 3h'], ['today', 'Today'], ['later', 'Later']]
const MAX_ODDS = [null, 1.5, 2, 3, 5]
const MIN_VALUE = [0, 0.02, 0.04, 0.06]               // Value tab only
const SORTS = [['best', 'Best'], ['shortest', 'Shortest']]
const FILTER_KEY = 'paddy.filters'
const NO_FILTERS = { markets: MARKETS, within: null, comp: null, maxOdds: null, minValue: 0, sort: 'best' }
function loadFilters() {
  try {
    const v = JSON.parse(localStorage.getItem(FILTER_KEY))
    if (v && Array.isArray(v.markets)) return { ...NO_FILTERS, ...v }
  } catch { /* none saved, or private mode */ }
  return NO_FILTERS
}
function inWindow(iso, within, now) {
  if (within == null) return true
  const ko = new Date(iso).getTime()
  const midnight = new Date(now); midnight.setHours(24, 0, 0, 0)
  if (within === 'today') return ko < midnight.getTime()
  if (within === 'later') return ko >= midnight.getTime()
  return ko - now <= within * 3600_000
}
const compLabel = k => (k || 'other').replace(/^soccer_?/, '').replace(/_/g, ' ')
  .replace(/\b\w/g, c => c.toUpperCase()) || 'Other'
// What is applied, in a few words; empty when nothing is.
function summary(fl, tab) {
  const bits = []
  if (fl.markets.length < MARKETS.length) bits.push(fl.markets.length ? fl.markets.join(', ') : 'no markets')
  if (fl.within != null) bits.push(WITHIN.find(([w]) => w === fl.within)[1])
  if (fl.comp != null) bits.push(compLabel(fl.comp))
  if (fl.maxOdds != null) bits.push(`≤ ${fl.maxOdds.toFixed(2)}`)
  if (tab === 'value' && fl.minValue > 0) bits.push(`${(100 + fl.minValue * 100).toFixed(0)}%+`)
  if (fl.sort === 'shortest') bits.push('shortest first')
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
function loadRefundSettings() {
  try { return { ...ACCA_DEFAULTS, ...JSON.parse(localStorage.getItem('paddy.acca')) } } catch { return ACCA_DEFAULTS }
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
    </div>
  )
}

export default function CardsPage() {
  const [tab, setTab] = useState(() => {
    try { return localStorage.getItem(TAB_KEY) || 'value' } catch { return 'value' }
  })
  const [refundCfg] = useState(loadRefundSettings)
  const [fl, setFl] = useState(loadFilters)
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState(null)
  const [error, setError] = useState(null)
  const [why, setWhy] = useState(null)
  const [updated, setUpdated] = useState(null)

  useEffect(() => {
    try { localStorage.setItem(TAB_KEY, tab) } catch { /* private mode */ }
  }, [tab])
  useEffect(() => {
    try { localStorage.setItem(FILTER_KEY, JSON.stringify(fl)) } catch { /* private mode */ }
  }, [fl])
  const setF = patch => setFl(v => ({ ...v, ...patch }))

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

  // Every bet, flat, carrying its match and kick-off, through the filters.
  const now = Date.now()
  const comps = [...new Set((items || []).map(i => i.sport_key || ''))].sort()
  const bets = (items || [])
    .filter(item => (fl.comp == null || (item.sport_key || '') === fl.comp)
      && inWindow(item.commence_time, fl.within, now))
    .flatMap(item => item.bets.map(b => ({
      ...b, match: item.match, matchId: item.event_id, commence_time: item.commence_time,
    })))
    .filter(b => (!b.market || fl.markets.includes(b.market))
      && (fl.maxOdds == null || b.odds <= fl.maxOdds))

  // Best down: value on the Value tab, Kelly growth in the offer on the others.
  const refund = tab === 'refund'
  const list = tab === 'value'
    ? bets.filter(b => b.value >= fl.minValue).map(b => ({ b, s: b.value }))
    : bets.filter(b => b.odds >= ACCA_FLOOR)
      .map(b => ({ b, s: refund ? legScore(b, refundCfg) : boostLegScore(b).growth }))
  list.sort(fl.sort === 'shortest'
    ? (x, y) => x.b.odds - y.b.odds || y.s - x.s
    : (x, y) => y.s - x.s)

  const applied = summary(fl, tab)
  const body = list.length
    ? list.map(({ b }) => <Row key={b.matchId + b.pick} bet={b} />)
    : <p className={f.empty}>{applied ? 'Nothing matches these filters.'
      : tab === 'value' ? 'No bets at or above fair right now.' : `No legs at ${ACCA_FLOOR}+ right now.`}</p>

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

      <button type="button" className={`${f.filterBar} ${applied ? f.filtered : ''}`}
        aria-expanded={open} onClick={() => setOpen(o => !o)}>
        <span>{applied || 'All bets'}</span>
        <span className={f.filterToggle}>Filters {open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div className={f.panel}>
          <div className={f.group} role="group" aria-label="Market">
            <Chips icons options={MARKETS.map(m => [m, m])} isOn={m => fl.markets.includes(m)}
              onPick={m => setF({ markets: fl.markets.includes(m) ? fl.markets.filter(x => x !== m) : [...fl.markets, m] })} />
          </div>
          <div className={f.group}><span className={f.label}>Kick-off</span>
            <Chips options={WITHIN} isOn={w => fl.within === w} onPick={w => setF({ within: w })} />
          </div>
          <div className={f.group}><span className={f.label}>Max odds</span>
            <Chips options={MAX_ODDS.map(o => [o, o == null ? 'Any' : o.toFixed(1)])}
              isOn={o => fl.maxOdds === o} onPick={o => setF({ maxOdds: o })} />
          </div>
          {tab === 'value' && (
            <div className={f.group}><span className={f.label}>Min value</span>
              <Chips options={MIN_VALUE.map(v => [v, v ? `${100 + v * 100}%` : 'Fair'])}
                isOn={v => fl.minValue === v} onPick={v => setF({ minValue: v })} />
            </div>
          )}
          <div className={f.group}><span className={f.label}>Sort</span>
            <Chips options={SORTS} isOn={k => fl.sort === k} onPick={k => setF({ sort: k })} />
          </div>
          <div className={f.group}><span className={f.label}>League</span>
            <select className={f.select} value={fl.comp ?? ''} aria-label="League"
              onChange={e => setF({ comp: e.target.value === '' ? null : e.target.value })}>
              <option value="">All</option>
              {comps.map(c => <option key={c} value={c}>{compLabel(c)}</option>)}
            </select>
            {applied && <button type="button" className={f.reset} onClick={() => setF(NO_FILTERS)}>Reset</button>}
          </div>
        </div>
      )}

      {error && <p className={f.error}>{error}</p>}
      {!error && items === null && <p className={f.empty}>Loading prices…</p>}
      {!error && items && items.length === 0 && <p className={f.empty}>{why ?? 'No prices yet.'}</p>}
      {!error && items && items.length > 0 && (
        <div className={f.list}>
          <div className={`${f.row} ${f.colHead}`}><span /><span>Bet</span><span>Odds</span><span>Value</span></div>
          {body}
        </div>
      )}
    </div>
  )
}
