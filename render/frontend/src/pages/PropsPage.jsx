import { useState, useEffect, useRef, useCallback } from 'react'
import { getPropsFeed, getPropsDiagnostics } from '../api'
import f from './CardsPage.module.css'
import useAutoRefresh from '../hooks/useAutoRefresh'
import { age, kickoff } from '../paddy'
import {
  MAX_ODDS, STATS, STAT_LABELS, WITHIN, allMarkets, filterRows, loadFilters, oldestPricedAt,
  rowsOf, sortRows,
} from '../props'

/**
 * Props — Paddy Power's player props against Pinnacle, on the Paddy board's
 * look (CardsPage.module.css). One list: every bet at or above 103% of fair
 * (props.js, VALUE_FLOOR), best value first; sort is the Odds and Value
 * headings. Each row: the pick, the match and kick-off, Paddy's odds, the
 * value % in Paddy's tiers and the fair.
 */

const BAND = '#004833'   // Paddy's green
const FILTER_KEY = 'props.filters'
// Paddy's value tiers: 110%+ green, 120%+ purple.
const GOOD = 0.10
const GREAT = 0.20
const tier = v => `${v > 0 ? f.over : ''} ${v >= GREAT ? f.great : v >= GOOD ? f.good : ''}`
const evPct = v => `${((1 + v) * 100).toFixed(1)}%`

function marketsLabel(markets) {
  if (allMarkets(markets)) return 'Markets'
  if (!markets.length) return 'No markets'
  return markets.length === 1 ? STAT_LABELS[markets[0]] : `${markets.length} markets`
}
const withinLabel = w => (w == null ? 'Any time' : w === 'today' ? 'Today'
  : w === 1 ? 'Next 1h' : `Next ${w}h`)
const oddsLabel = o => (o == null ? 'Any odds' : `≤ ${o.toFixed(1)}`)

function Chips({ options, isOn, onPick }) {
  return options.map(([k, label]) => (
    <button key={label} type="button" className={`${f.chip} ${isOn(k) ? f.chipOn : ''}`}
      aria-pressed={isOn(k)} onClick={() => onPick(k)}>{label}</button>
  ))
}

function Ko({ iso, now }) {
  const { day, time } = kickoff(iso, now)
  return <span className={f.ko}>{day && <span>{day}</span>}<span>{time}</span></span>
}

export default function PropsPage() {
  const [fl, setFl] = useState(() => loadFilters(() => localStorage.getItem(FILTER_KEY)))
  const [open, setOpen] = useState(null)   // which filter's choices are showing
  const [items, setItems] = useState(null)
  const [error, setError] = useState(null)
  const [why, setWhy] = useState(null)
  const [now, setNow] = useState(() => Date.now())   // kick-off clock, advanced per refresh
  // "Updated 3m" counts up between refreshes.
  const [clock, setClock] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setClock(Date.now()), 30_000)
    return () => clearInterval(t)
  }, [])
  // The tool switcher below takes the band's colour while this page is open.
  useEffect(() => {
    const root = document.documentElement.style
    root.setProperty('--tool-band', BAND)
    root.setProperty('--dock-bg', '#fff')
    return () => { root.removeProperty('--tool-band'); root.removeProperty('--dock-bg') }
  }, [])

  useEffect(() => {
    try { localStorage.setItem(FILTER_KEY, JSON.stringify(fl)) } catch { /* private mode */ }
  }, [fl])
  const setF = patch => setFl(v => ({ ...v, ...patch }))

  // Load, then refresh in place (useAutoRefresh). A failed refresh hides the
  // list rather than leave old prices on screen.
  const live = useRef(true)
  const seq = useRef(0)   // refreshes overlap (interval + focus): only the latest may land
  const load = useCallback(() => {
    const id = ++seq.current
    return getPropsFeed(0)
      .then(async data => {
        if (!live.current || id !== seq.current) return
        setItems(data)
        setError(null)
        setNow(Date.now())
        if (rowsOf(data).length) { setWhy(null); return }
        try { setWhy((await getPropsDiagnostics()).verdict) } catch { /* plain empty state */ }
      })
      .catch(err => live.current && id === seq.current && setError(err.message))
  }, [])
  useEffect(() => {
    live.current = true
    load()
    return () => { live.current = false }
  }, [load])
  useAutoRefresh(load)

  const all = rowsOf(items)
  const list = sortRows(filterRows(all, fl, now), fl.sort)
  const updated = age(oldestPricedAt(items), clock)
  const pill = (key, label, on) => (
    <button type="button" className={`${f.pill} ${on ? f.pillOn : ''} ${open === key ? f.pillOpen : ''}`}
      aria-expanded={open === key} onClick={() => setOpen(o => (o === key ? null : key))}>{label}</button>
  )
  const sortBy = k => () => setF({ sort: k })

  return (
    <div className={f.page} style={{ '--band': BAND }}>
      <div className={f.band}>
        <div className={f.title}>
          <span className={f.name}>PROPS</span>
          {updated && <span className={f.updated}>Updated {updated}</span>}
        </div>
        <div className={f.pills} role="group" aria-label="Filters">
          {pill('markets', marketsLabel(fl.markets), !allMarkets(fl.markets))}
          {pill('within', withinLabel(fl.within), fl.within != null)}
          {pill('odds', oddsLabel(fl.maxOdds), fl.maxOdds != null)}
        </div>
      </div>
      {open && (
        <div className={f.panel}>
          {open === 'markets' && (
            <div className={f.group} role="group" aria-label="Market">
              <Chips options={STATS.map(s => [s, STAT_LABELS[s]])} isOn={s => fl.markets.includes(s)}
                onPick={s => setF({ markets: fl.markets.includes(s) ? fl.markets.filter(x => x !== s) : [...fl.markets, s] })} />
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
        </div>
      )}

      {error && <p className={f.error}>{error}</p>}
      {!error && items === null && <p className={f.empty}>Loading prices…</p>}
      {!error && items && all.length === 0 && <p className={f.empty}>{why ?? 'No prices yet.'}</p>}
      {!error && all.length > 0 && (
        <div className={f.list}>
          <div className={`${f.row} ${f.colHead}`}>
            <span>Bet</span><span>KO</span>
            <button type="button" className={fl.sort === 'shortest' ? f.sorted : ''} aria-pressed={fl.sort === 'shortest'}
              onClick={sortBy('shortest')}>Odds</button>
            <button type="button" className={fl.sort === 'best' ? f.sorted : ''} aria-pressed={fl.sort === 'best'}
              onClick={sortBy('best')}>Value</button>
            <span>Fair</span>
          </div>
          {list.length ? list.map(b => (
            <div className={f.row} key={b.key}>
              <span className={f.what}>
                <span className={f.pick}>{b.pick}</span>
                <span className={f.match}>{b.match}</span>
              </span>
              <Ko iso={b.commence_time} now={now} />
              <span className={f.odds}>{b.odds.toFixed(2)}</span>
              <span className={`${f.value} ${tier(b.value)}`}>{evPct(b.value)}</span>
              <span className={f.fair}>{b.fair.toFixed(2)}</span>
            </div>
          )) : <p className={f.empty}>Nothing matches these filters.</p>}
        </div>
      )}
    </div>
  )
}
