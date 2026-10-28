import { useState, useEffect, useRef, useCallback } from 'react'
import { getCardsFeed, getCardsDiagnostics, getHillsFeed, getHillsDiagnostics } from '../api'
import f from './CardsPage.module.css'
import MarketIcon from '../components/MarketIcon'
import useAutoRefresh from '../hooks/useAutoRefresh'
import { DEFAULTS as ACCA_DEFAULTS, evaluate, legScore, trueProb } from '../accaInsurance'
import { boostEval, boostLegScore } from '../accaBoost'

/**
 * Paddy — Paddy Power's corners, cards, goals and saves against fair prices.
 *
 * One feed. The offer (Value / Refund / Boost) is a floating switch at the
 * bottom; every filter lives in a single folded options pane whose collapsed
 * line names what is applied:
 *   Value   every bet at or above fair, best value first.
 *   Refund  the acca insurance offer (5-fold, legs 1.20+, stake back as a
 *           bonus if exactly one leg loses): every qualifying leg, best down.
 *   Boost   winnings +10/20/30% on a 4/5/6-fold, legs 1.20+: same.
 * Order is Kelly growth (accaInsurance.js / accaBoost.js). Each row shows the
 * Paddy odds, the value %, and the fair odds (the one the value % is measured
 * against, so odds ÷ fair − 1 is the value shown). No acca is built or recommended —
 * the list is the tool, the picking is Callum's.
 *
 * Hills (`book="hills"`) is the same page against William Hill: its own feed
 * (/hills), its own remembered filters, the Value offer only (Refund and Boost
 * are Paddy's offers) and no "most" rows (those are read off Paddy's terminal).
 */

// One fetch serves all three offers: down to 95% of fair (min_edge -0.05),
// because the acca offers carry value of their own and a leg a little under
// fair can still belong. The Value offer shows only fair-or-better from it.
const FETCH_MIN_EDGE = -0.05
// Value at or above this is set in the accent colour.
const STRONG = 0.04
const ACCA_FLOOR = ACCA_DEFAULTS.minOdds
const OFFERS = [['value', 'Value'], ['refund', 'Refund'], ['boost', 'Boost']]
// Boost's suggested stake: a tenth of full Kelly on a £50k bankroll (Callum).
const BOOST_BANKROLL = 50_000
const BOOST_KELLY = 0.10

// Filters: folded away under one line that says what is applied, so the usual
// visit is open → glance → bet. Shared across offers, remembered.
const MARKETS = ['corners', 'cards', 'goals', 'saves', 'team_corners', 'team_cards', 'most_corners', 'most_cards']
const MARKET_LABELS = { team_corners: 'Team corners', team_cards: 'Team cards',
  most_corners: 'Most corners', most_cards: 'Most cards' }
// "Most corners / cards" rows carry a FAIR price only — the vendor feed has no
// Paddy quote for them; the terminal does. Read against the shop screen, so
// off by default.
const FAIR_ONLY = ['most_corners', 'most_cards']
const DEFAULT_MARKETS = MARKETS.filter(m => !FAIR_ONLY.includes(m))
const marketLabel = m => MARKET_LABELS[m] || m[0].toUpperCase() + m.slice(1)
const WITHIN = [[null, 'Any'], [1, '< 1h'], ['today', 'Today'], [24, '24h'], [48, '48h'], [72, '72h']]
const MAX_ODDS = [null, 1.5, 2, 3, 5]
const MIN_VALUE = [0, 0.02, 0.04, 0.06]               // Value offer only
const SORTS = [['best', 'Best'], ['shortest', 'Shortest']]
const PER_MATCH = [[false, 'All legs'], [true, '1 per match']]   // Refund / Boost only
// Free-bet EV rate: a free bet's stake isn't returned, so you keep (odds-1)/odds
// of a win — higher odds retain more. Scaled by the value edge. Value offer only.
const freeBetScore = b => (b.odds == null ? -Infinity : (1 + b.value) * (b.odds - 1) / b.odds)
const NO_FILTERS = { markets: DEFAULT_MARKETS, within: null, maxOdds: null, minValue: 0, sort: 'best', onePerMatch: false }
// A saved filter must never hold a value its option list no longer offers (the
// retired "< 3h" kick-off, the old league filter …): summary() looks the value
// up by option and would throw, taking the whole page down.
function loadFilters(cfg) {
  try {
    const v = JSON.parse(localStorage.getItem(cfg.filterKey))
    if (!v || !Array.isArray(v.markets)) return NO_FILTERS
    const oneOf = (x, opts, dflt) => (opts.includes(x) ? x : dflt)
    return {
      markets: v.markets.filter(m => cfg.markets.includes(m)),
      within: oneOf(v.within, WITHIN.map(([w]) => w), null),
      maxOdds: oneOf(v.maxOdds, MAX_ODDS, null),
      minValue: oneOf(v.minValue, MIN_VALUE, 0),
      sort: oneOf(v.sort, [...SORTS.map(([k]) => k), 'freebet'], 'best'),
      onePerMatch: v.onePerMatch === true,
    }
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
  // Markets: "+ Most corners" when only the fair-only ones were switched on;
  // the full list once anything usual is switched off.
  const added = fl.markets.filter(m => !DEFAULT_MARKETS.includes(m))
  const removed = DEFAULT_MARKETS.filter(m => !fl.markets.includes(m))
  if (!removed.length && added.length) bits.push('+ ' + added.map(marketLabel).join(', '))
  else if (removed.length) bits.push(fl.markets.length ? fl.markets.map(marketLabel).join(', ') : 'no markets')
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
const lineLabel = b => (b.side === 'Over' ? 'O' : 'U') + b.line.toFixed(1)
// A selection = one match · market · side · (team): its lines differ only by the
// number. Drop the line from the pick (always " X.X ", space-padded) to get a
// key shared across the ladder — for plain Over/Under and team picks alike.
const selKey = b => `${b.matchId}|${b.pick.replace(` ${b.line.toFixed(1)} `, ' ')}`
// The best-scoring leg from each match (an acca takes one leg per game).
const bestPerMatch = xs => {
  const top = new Map()
  for (const x of xs) if (!top.has(x.b.matchId) || x.s > top.get(x.b.matchId).s) top.set(x.b.matchId, x)
  return [...top.values()]
}
function kickoff(iso) {
  const d = new Date(iso)
  const day = d.toDateString() === new Date().toDateString() ? ''
    : d.toLocaleDateString([], { weekday: 'short' }) + ' '
  return day + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// One selection: the best value line, tappable to drop down its other value
// lines (the ladder) when there are any.
function Selection({ lead, others, expanded, onToggle }) {
  const has = others.length > 0
  return (
    <>
      <div className={`${f.row} ${has ? f.tappable : ''}`}
        role={has ? 'button' : undefined} aria-expanded={has ? expanded : undefined}
        onClick={has ? onToggle : undefined}>
        <MarketIcon market={lead.market} size={22} />
        <span className={f.what}>
          <span className={f.pick}>
            {shortPick(lead.pick)}
            {lead.basis === 'hybrid' && <span className={f.more} title="Fair from the team's one Pinnacle line plus the match ladder's shape">~</span>}
            {has && <span className={f.more}> {expanded ? '▴' : '▾'} +{others.length}</span>}
          </span>
          <span className={f.match}>{lead.match} · {kickoff(lead.commence_time)}</span>
        </span>
        <span className={f.odds}>{lead.odds == null ? '—' : lead.odds.toFixed(2)}</span>
        <span className={`${f.value} ${lead.value != null && lead.value >= STRONG ? f.strong : ''}`}>
          {lead.value == null ? '—' : evPct(lead.value)}
        </span>
        <span className={f.fair}>{lead.fair.toFixed(2)}</span>
      </div>
      {has && expanded && (
        <div className={f.sub}>
          {others.map(o => (
            <div className={f.subRow} key={o.pick}>
              <span /><span className={f.subLine}>{lineLabel(o)}{o.basis === 'hybrid' && <span className={f.more}>~</span>}</span>
              <span className={f.odds}>{o.odds == null ? '—' : o.odds.toFixed(2)}</span>
              <span className={`${f.value} ${o.value != null && o.value >= STRONG ? f.strong : ''}`}>
                {o.value == null ? '—' : evPct(o.value)}
              </span>
              <span className={f.fair}>{o.fair.toFixed(2)}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

// Per book: where its feed comes from, where its choices are remembered, and
// which offers and markets it has.
const BOOKS = {
  paddy: { feed: getCardsFeed, why: getCardsDiagnostics, offerKey: 'paddy.tab',
    filterKey: 'paddy.filters', offers: OFFERS, markets: MARKETS },
  hills: { feed: getHillsFeed, why: getHillsDiagnostics, offerKey: 'hills.tab',
    filterKey: 'hills.filters', offers: OFFERS.filter(([k]) => k === 'value'),
    markets: MARKETS.filter(m => !FAIR_ONLY.includes(m)) },
}

export default function CardsPage({ book = 'paddy' }) {
  const cfg = BOOKS[book]
  const [offer, setOffer] = useState(() => {
    try {
      const saved = localStorage.getItem(cfg.offerKey)
      return cfg.offers.some(([k]) => k === saved) ? saved : 'value'
    } catch { return 'value' }
  })
  const [fl, setFl] = useState(() => loadFilters(cfg))
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState(null)
  const [error, setError] = useState(null)
  const [why, setWhy] = useState(null)
  const [now, setNow] = useState(() => Date.now())   // kick-off clock, advanced per refresh
  const [expanded, setExpanded] = useState(() => new Set())
  const toggle = k => setExpanded(s => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n })

  useEffect(() => {
    try { localStorage.setItem(cfg.offerKey, offer) } catch { /* private mode */ }
  }, [offer, cfg])
  useEffect(() => {
    try { localStorage.setItem(cfg.filterKey, JSON.stringify(fl)) } catch { /* private mode */ }
  }, [fl, cfg])
  const setF = patch => setFl(v => ({ ...v, ...patch }))

  // Load, then refresh in place (useAutoRefresh). A failed refresh hides the
  // list rather than leave old prices on screen.
  const live = useRef(true)
  const seq = useRef(0)   // refreshes overlap (interval + focus): only the latest may land
  const load = useCallback(() => {
    const id = ++seq.current
    return cfg.feed(FETCH_MIN_EDGE)
      .then(async data => {
        if (!live.current || id !== seq.current) return
        setItems(data)
        setError(null)
        setNow(Date.now())
        if (data.length) { setWhy(null); return }
        try { setWhy((await cfg.why()).verdict) } catch { /* plain empty state */ }
      })
      .catch(err => live.current && id === seq.current && setError(err.message))
  }, [cfg])
  useEffect(() => {
    live.current = true
    load()
    return () => { live.current = false }
  }, [load])
  useAutoRefresh(load)

  // Every bet, flat, carrying its match and kick-off, through the filters.
  const bets = (items || [])
    .filter(item => inWindow(item.commence_time, fl.within, now))
    .flatMap(item => [
      ...item.bets.map(b => ({
        ...b, match: item.match, matchId: item.event_id, commence_time: item.commence_time,
      })),
      // Fair-only rows (most corners / cards): no Paddy price, no value.
      ...(item.fairs || []).map(fr => ({
        ...fr, odds: null, value: null, line: 0, side: '',
        match: item.match, matchId: item.event_id, commence_time: item.commence_time,
      })),
    ])
    .filter(b => (!b.market || fl.markets.includes(b.market))
      && (fl.maxOdds == null || b.odds <= fl.maxOdds))

  // Best down: value on the Value offer, Kelly growth in the offer on the others.
  const refund = offer === 'refund'
  const list = offer === 'value'
    ? bets.filter(b => b.value == null || b.value >= fl.minValue).map(b => ({ b, s: b.value ?? -Infinity }))
    : bets.filter(b => b.odds >= ACCA_FLOOR)
      .map(b => ({ b, s: refund ? legScore(b, ACCA_DEFAULTS) : boostLegScore(b).growth }))
  // Refund / Boost: one selection per match — the highest-scoring one.
  if (offer !== 'value' && fl.onePerMatch) list.splice(0, list.length, ...bestPerMatch(list))
  list.sort(
    fl.sort === 'shortest' ? (x, y) => (x.b.odds ?? Infinity) - (y.b.odds ?? Infinity) || y.s - x.s
    : fl.sort === 'freebet' && offer === 'value' ? (x, y) => freeBetScore(y.b) - freeBetScore(x.b)
    : (x, y) => y.s - x.s)

  const applied = summary(fl, offer)

  // Refund / Boost: the acca of the top 5 by score, one per match (asked for:
  // "a number to see at a glance if it's worth betting").
  let top5 = null
  if (offer !== 'value') {
    const picks = bestPerMatch(list).sort((a, b) => b.s - a.s).slice(0, 5).map(x => x.b)
    if (picks.length === 5) {
      const odds = picks.map(b => b.odds), probs = picks.map(trueProb)
      const r = refund ? evaluate({ odds, probs }, ACCA_DEFAULTS) : boostEval(odds, probs)
      top5 = { odds: r.accaOdds, ev: r.evPct,
        stake: refund ? null : Math.round(BOOST_KELLY * r.kelly * BOOST_BANKROLL) }
    }
  }
  // Group each selection's value lines: the best line leads, the rest drop down.
  const groups = new Map()
  for (const { b } of list) {
    const k = selKey(b)
    if (groups.has(k)) groups.get(k).others.push(b)
    else groups.set(k, { key: k, lead: b, others: [] })
  }
  for (const g of groups.values()) g.others.sort((a, b) => b.line - a.line)
  const body = groups.size
    ? [...groups.values()].map(g => (
        <Selection key={g.key} lead={g.lead} others={g.others}
          expanded={expanded.has(g.key)} onToggle={() => toggle(g.key)} />))
    : <p className={f.empty}>{applied ? 'Nothing matches these options.'
      : offer === 'value' ? 'No bets at or above fair right now.' : `No legs at ${ACCA_FLOOR}+ right now.`}</p>

  return (
    <div className={f.page}>
      <div className={`${f.filterBar} ${applied ? f.filtered : ''}`}>
        <button type="button" className={f.summary} aria-expanded={open} onClick={() => setOpen(o => !o)}>
          {applied || 'All bets'}
        </button>
        <button type="button" className={f.filterToggle} aria-expanded={open} onClick={() => setOpen(o => !o)}>
          Options {open ? '▴' : '▾'}
        </button>
      </div>
      {open && (
        <div className={f.panel}>
          <div className={f.group} role="group" aria-label="Market">
            <Chips icons options={cfg.markets.map(m => [m, marketLabel(m)])} isOn={m => fl.markets.includes(m)}
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

      {cfg.offers.length > 1 && (
        <div className={f.offerSwitch} role="group" aria-label="Offer">
          {cfg.offers.map(([k, label]) => (
            <button key={k} type="button" className={`${f.offerBtn} ${offer === k ? f.offerOn : ''}`}
              aria-pressed={offer === k} onClick={() => setOffer(k)}>{label}</button>
          ))}
        </div>
      )}
    </div>
  )
}
