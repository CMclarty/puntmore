import { useState, useEffect, useRef, useCallback } from 'react'
import { getCardsFeed, getCardsDiagnostics, getHillsFeed, getHillsDiagnostics } from '../api'
import f from './CardsPage.module.css'
import MarketIcon from '../components/MarketIcon'
import useAutoRefresh from '../hooks/useAutoRefresh'
import { shortTeams } from '../teams'
import { DEFAULTS as ACCA_DEFAULTS, legScore } from '../accaInsurance'
import { boostLegScore } from '../accaBoost'
import HillsBuilder from './HillsBuilder'
import { age, kickoff } from '../paddy'

/**
 * Paddy — Paddy Power's corners, cards, goals and saves against fair prices.
 *
 * One feed. A band in the bookmaker's colours (Callum, 8 Nov 2026) holds the
 * book's name, how long since the prices were fetched, and one pill per
 * filter showing its setting; a pill opens its own choices. Sort is the Odds
 * and Value headings. The offer (Value / Refund / Boost) is a floating switch
 * at the bottom:
 *   Value   every bet at or above fair, best value first.
 *   Refund  the acca insurance offer (5-fold, legs 1.20+, stake back as a
 *           bonus if exactly one leg loses): every qualifying leg, best down.
 *   Boost   winnings +10/20/30% on a 4/5/6-fold, legs 1.20+: same.
 * Order is Kelly growth (accaInsurance.js / accaBoost.js). Each row shows the
 * bet, the kick-off (the day above it when not today), the Paddy odds, the
 * value % (filled green at 110%+, purple at 120%+), and the fair odds (the one the value % is measured
 * against, so odds ÷ fair − 1 is the value shown). No acca is built or recommended —
 * the list is the tool, the picking is Callum's.
 *
 * Hills (`book="hills"`) is the same page against William Hill: its own feed
 * (/hills), its own remembered filters and no "most" rows (those are read off
 * Paddy's terminal). Its offers are Value · BB Boost · Boost: BB Boost (in
 * Refund's place) is William Hill's boosted bet builder on the team's target
 * match (HillsBuilder.jsx); Value and Boost are Paddy's, over every match.
 */

// One fetch serves all three offers: down to 95% of fair (min_edge -0.05),
// because the acca offers carry value of their own and a leg a little under
// fair can still belong. The Value offer shows only fair-or-better from it.
const FETCH_MIN_EDGE = -0.05
// Value tiers, filled like an EV sheet (Callum, 8 Nov 2026): 110%+ green,
// 120%+ purple.
const GOOD = 0.10
const GREAT = 0.20
const tier = v => (v == null ? '' : v >= GREAT ? f.great : v >= GOOD ? f.good : '')
// Each book's band: the bookmaker's own colours (the register, 8 Nov 2026).
const THEME = {
  paddy: { name: 'PADDY', band: '#004833' },
  hills: { name: 'HILLS', band: '#1F3A5F' },
}
const ACCA_FLOOR = ACCA_DEFAULTS.minOdds
const OFFERS = [['value', 'Value'], ['refund', 'Refund'], ['boost', 'Boost']]

// Filters: one pill each in the band, so the usual
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
const SORTS = ['best', 'shortest']   // by the Value and Odds headings
const PER_MATCH = [[false, 'All legs'], [true, '1 per match']]   // Refund / Boost only
const NO_FILTERS = { markets: DEFAULT_MARKETS, within: null, maxOdds: null, sort: 'best', onePerMatch: false }
// A saved filter must never hold a value its option list no longer offers (the
// retired "< 3h" kick-off, the old league filter …): a pill looks the value
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
      sort: oneOf(v.sort, SORTS, 'best'),
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
// Each filter's pill shows its current setting.
function marketsLabel(markets) {
  const same = markets.length === DEFAULT_MARKETS.length && DEFAULT_MARKETS.every(m => markets.includes(m))
  if (same) return 'Markets'
  if (!markets.length) return 'No markets'
  return markets.length === 1 ? marketLabel(markets[0]) : `${markets.length} markets`
}
const withinLabel = w => (w == null ? 'Any time' : w === 'today' ? 'Today'
  : w === 1 ? 'Next 1h' : `Next ${w}h`)
const oddsLabel = o => (o == null ? 'Any odds' : `≤ ${o.toFixed(1)}`)

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
function Ko({ iso, now }) {
  const { day, time } = kickoff(iso, now)
  return <span className={f.ko}>{day && <span>{day}</span>}<span>{time}</span></span>
}

// One selection: the best value line, tappable to drop down its other value
// lines (the ladder) when there are any.
function Selection({ lead, others, expanded, onToggle, now }) {
  const has = others.length > 0
  return (
    <>
      <div className={`${f.row} ${has ? f.tappable : ''}`}
        role={has ? 'button' : undefined} aria-expanded={has ? expanded : undefined}
        onClick={has ? onToggle : undefined}>
        <span className={f.what}>
          <span className={f.pick}>
            {shortPick(lead.pick)}
            {lead.basis === 'hybrid' && <span className={f.more} title="Fair from the team's one Pinnacle line plus the match ladder's shape">~</span>}
            {has && <span className={f.more}> {expanded ? '▴' : '▾'} +{others.length}</span>}
          </span>
          <span className={f.match}>{lead.match}</span>
        </span>
        <Ko iso={lead.commence_time} now={now} />
        <span className={f.odds}>{lead.odds == null ? '—' : lead.odds.toFixed(2)}</span>
        <span className={`${f.value} ${tier(lead.value)}`}>
          {lead.value == null ? '—' : evPct(lead.value)}
        </span>
        <span className={f.fair}>{lead.fair.toFixed(2)}</span>
      </div>
      {has && expanded && (
        <div className={f.sub}>
          {others.map(o => (
            <div className={f.subRow} key={o.pick}>
              <span className={f.subLine}>{lineLabel(o)}{o.basis === 'hybrid' && <span className={f.more}>~</span>}</span><span />
              <span className={f.odds}>{o.odds == null ? '—' : o.odds.toFixed(2)}</span>
              <span className={`${f.value} ${tier(o.value)}`}>
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
    filterKey: 'hills.filters', offers: [['value', 'Value'], ['bb', 'BB Boost'], ['boost', 'Boost']],
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
  const [open, setOpen] = useState(null)   // which filter's choices are showing
  const [items, setItems] = useState(null)
  const [error, setError] = useState(null)
  const [why, setWhy] = useState(null)
  const [now, setNow] = useState(() => Date.now())   // kick-off clock, advanced per refresh
  const [expanded, setExpanded] = useState(() => new Set())
  const toggle = k => setExpanded(s => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n })
  const theme = THEME[book]
  // "Updated 3m" counts up between refreshes.
  const [clock, setClock] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setClock(Date.now()), 30_000)
    return () => clearInterval(t)
  }, [])
  // The tool switcher below takes the book's colour while this page is open.
  useEffect(() => {
    const root = document.documentElement.style
    root.setProperty('--tool-band', theme.band)
    root.setProperty('--dock-bg', '#fff')
    return () => { root.removeProperty('--tool-band'); root.removeProperty('--dock-bg') }
  }, [theme])

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
        ...b, pick: shortTeams(b.pick), match: shortTeams(item.match), matchId: item.event_id, commence_time: item.commence_time,
      })),
      // Fair-only rows (most corners / cards): no Paddy price, no value.
      ...(item.fairs || []).map(fr => ({
        ...fr, pick: shortTeams(fr.pick), odds: null, value: null, line: 0, side: '',
        match: shortTeams(item.match), matchId: item.event_id, commence_time: item.commence_time,
      })),
    ])
    .filter(b => (!b.market || fl.markets.includes(b.market))
      && (fl.maxOdds == null || b.odds <= fl.maxOdds))

  // Best down: value on the Value offer, Kelly growth in the offer on the others.
  const refund = offer === 'refund'
  const list = offer === 'value'
    ? bets.filter(b => b.value == null || b.value >= 0).map(b => ({ b, s: b.value ?? -Infinity }))
    : bets.filter(b => b.odds >= ACCA_FLOOR)
      .map(b => ({ b, s: refund ? legScore(b, ACCA_DEFAULTS) : boostLegScore(b).growth }))
  // Refund / Boost: one selection per match — the highest-scoring one.
  if (offer !== 'value' && fl.onePerMatch) list.splice(0, list.length, ...bestPerMatch(list))
  list.sort(
    fl.sort === 'shortest' ? (x, y) => (x.b.odds ?? Infinity) - (y.b.odds ?? Infinity) || y.s - x.s
    : (x, y) => y.s - x.s)

  // The oldest fetch behind the board: "updated" never claims fresher prices.
  const pricedAt = (items || []).map(i => i.priced_at).filter(Boolean).sort()[0]
  const updated = age(pricedAt, clock)
  const pill = (key, label, on) => (
    <button type="button" className={`${f.pill} ${on ? f.pillOn : ''} ${open === key ? f.pillOpen : ''}`}
      aria-expanded={open === key} onClick={() => setOpen(o => (o === key ? null : key))}>{label}</button>
  )
  const sameMarkets = fl.markets.length === DEFAULT_MARKETS.length && DEFAULT_MARKETS.every(m => fl.markets.includes(m))
  const sortBy = k => () => setF({ sort: k })
  const offerSwitch = cfg.offers.length > 1 && (
    <div className={f.offerSwitch} role="group" aria-label="Offer">
      {cfg.offers.map(([k, label]) => (
        <button key={k} type="button" className={`${f.offerBtn} ${offer === k ? f.offerOn : ''}`}
          aria-pressed={offer === k} onClick={() => setOffer(k)}>{label}</button>
      ))}
    </div>
  )
  // BB Boost is its own screen: the target match, its wheels and the bet log.
  if (offer === 'bb') return <div className={f.page} style={{ '--band': theme.band }}><HillsBuilder />{offerSwitch}</div>

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
        <Selection key={g.key} lead={g.lead} others={g.others} now={now}
          expanded={expanded.has(g.key)} onToggle={() => toggle(g.key)} />))
    : <p className={f.empty}>{!sameMarkets || fl.within != null || fl.maxOdds != null ? 'Nothing matches these filters.'
      : offer === 'value' ? 'No bets at or above fair right now.' : `No legs at ${ACCA_FLOOR}+ right now.`}</p>

  return (
    <div className={f.page} style={{ '--band': theme.band }}>
      <div className={f.band}>
        <div className={f.title}>
          <span className={f.name}>{theme.name}</span>
          {updated && <span className={f.updated}>Updated {updated}</span>}
        </div>
        <div className={f.pills} role="group" aria-label="Filters">
          {pill('markets', marketsLabel(fl.markets), !sameMarkets)}
          {pill('within', withinLabel(fl.within), fl.within != null)}
          {pill('odds', oddsLabel(fl.maxOdds), fl.maxOdds != null)}
          {offer !== 'value' && pill('match', fl.onePerMatch ? '1 per match' : 'All legs', fl.onePerMatch)}
        </div>
      </div>
      {open && (
        <div className={f.panel}>
          {open === 'markets' && (
            <div className={f.group} role="group" aria-label="Market">
              <Chips icons options={cfg.markets.map(m => [m, marketLabel(m)])} isOn={m => fl.markets.includes(m)}
                onPick={m => setF({ markets: fl.markets.includes(m) ? fl.markets.filter(x => x !== m) : [...fl.markets, m] })} />
            </div>
          )}
          {open === 'within' && (
            <div className={f.group} role="group" aria-label="Kick-off">
              <Chips options={WITHIN} isOn={w => fl.within === w} onPick={w => { setF({ within: w }); setOpen(null) }} />
            </div>
          )}
          {open === 'odds' && (
            <div className={f.group} role="group" aria-label="Max odds">
              <Chips options={MAX_ODDS.map(o => [o, o == null ? 'Any' : o.toFixed(1)])}
                isOn={o => fl.maxOdds === o} onPick={o => { setF({ maxOdds: o }); setOpen(null) }} />
            </div>
          )}
          {open === 'match' && (
            <div className={f.group} role="group" aria-label="Per match">
              <Chips options={PER_MATCH} isOn={v => fl.onePerMatch === v} onPick={v => { setF({ onePerMatch: v }); setOpen(null) }} />
            </div>
          )}
        </div>
      )}

      {error && <p className={f.error}>{error}</p>}
      {!error && items === null && <p className={f.empty}>Loading prices…</p>}
      {!error && items && items.length === 0 && <p className={f.empty}>{why ?? 'No prices yet.'}</p>}
      {!error && items && items.length > 0 && (
        <div className={f.list}>
          <div className={`${f.row} ${f.colHead}`}>
            <span>Bet</span><span>KO</span>
            <button type="button" className={fl.sort === 'shortest' ? f.sorted : ''} aria-pressed={fl.sort === 'shortest'}
              onClick={sortBy('shortest')}>Odds</button>
            <button type="button" className={fl.sort === 'best' ? f.sorted : ''} aria-pressed={fl.sort === 'best'}
              onClick={sortBy('best')}>Value</button>
            <span>Fair</span>
          </div>
          {body}
        </div>
      )}

      {offerSwitch}
    </div>
  )
}
