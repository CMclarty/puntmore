import { useState, useEffect, useRef, useCallback } from 'react'
import { getHillsFixtures, getHillsTarget, setHillsTarget, clearHillsTarget, getHillsBuilder } from '../api'
import OddsWheel, { LADDER } from '../components/OddsWheel'
import useAutoRefresh from '../hooks/useAutoRefresh'
import s from './HillsBuilder.module.css'

/**
 * BB Boost — Hills' third offer: William Hill's boosted bet builder on the
 * team's ONE target match (25% on winnings, 50% on some cup finals).
 *
 *   Target   picked from this weekend's Premier League games and the cup /
 *            Champions League finals; one for the whole team, so it is shown
 *            with who set it and anyone can change it.
 *   Fair ≥   a wheel: only builders whose fair price is at least this.
 *   Builders every 2–4 leg combination, one leg per stat, with its fair price
 *            and the product of William Hill's singles, best singles/fair first.
 *   Price    a wheel: the builder price the William Hill app quotes for the
 *            tapped combination (no feed carries builder prices). The boost
 *            is applied to its winnings and valued against the fair price.
 *
 * The fair price treats different stats as independent — a guide, not a
 * quote (api/app/hills/builder.py).
 */

// Fair-price floor: the prices a builder is usually wanted at.
const FAIR_FLOORS = [1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 7, 8, 10, 12, 15, 20, 25, 30, 40, 50]
const FAIR_LADDER = FAIR_FLOORS.map(d => ({ label: d.toFixed(1), dec: d }))
const DEFAULT_FLOOR = 3
// William Hill quotes builders fractionally and often long: the board ladder
// carried on past 25/1.
const PRICE_LADDER = [
  ...LADDER,
  ...[28, 33, 40, 50, 66, 80, 100, 125, 150, 200].map(n => ({ n, d: 1, label: `${n}/1`, dec: n + 1 })),
]

const boosted = (price, pct) => 1 + (price - 1) * (1 + pct / 100)
const signed = v => `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)}%`
function kickoff(iso) {
  const d = new Date(iso)
  return d.toLocaleDateString([], { weekday: 'short' }) + ' '
    + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
const comboKey = c => c.legs.map(l => l.pick).join(' + ')

function TargetPicker({ current, onSet, onCancel }) {
  const [fixtures, setFixtures] = useState(null)
  const [boosts, setBoosts] = useState([25, 50])
  const [pick, setPick] = useState(current?.fixture_id ?? '')
  const [boost, setBoost] = useState(current?.boost_pct ?? 25)
  const [error, setError] = useState(null)
  useEffect(() => {
    getHillsFixtures()
      .then(d => { setFixtures(d.fixtures); setBoosts(d.boosts) })
      .catch(e => setError(e.message))
  }, [])
  const submit = () => setHillsTarget(pick, boost).then(onSet).catch(e => setError(e.message))
  return (
    <div className={s.picker}>
      <select className={s.select} value={pick} onChange={e => setPick(e.target.value)}
        aria-label="Target match">
        <option value="">{fixtures === null ? 'Loading fixtures…'
          : fixtures.length ? 'Pick the boosted match' : 'No boosted fixtures in the next 7 days'}</option>
        {(fixtures || []).map(fx => (
          <option key={fx.fixture_id} value={fx.fixture_id}>
            {fx.home} v {fx.away} · {kickoff(fx.kickoff)}{fx.competition === 'Premier League' ? '' : ` · ${fx.competition}`}
          </option>
        ))}
      </select>
      <div className={s.pickRow}>
        {boosts.map(b => (
          <button key={b} type="button" className={`${s.chip} ${boost === b ? s.chipOn : ''}`}
            aria-pressed={boost === b} onClick={() => setBoost(b)}>+{b}%</button>
        ))}
        <button type="button" className={s.primary} disabled={!pick} onClick={submit}>Set target</button>
        {current && <button type="button" className={s.link} onClick={onCancel}>Cancel</button>}
      </div>
      {error && <p className={s.error}>{error}</p>}
    </div>
  )
}

export default function HillsBuilder() {
  const [tgt, setTgt] = useState(undefined)            // undefined = loading, null = none
  const [picking, setPicking] = useState(false)
  const [floor, setFloor] = useState(DEFAULT_FLOOR)
  const [result, setResult] = useState(null)          // {fixtureId, body} of the last fetch
  const [error, setError] = useState(null)
  const [chosen, setChosen] = useState(null)           // comboKey of the tapped builder
  const [price, setPrice] = useState(null)             // William Hill's quoted builder price

  // The target is the team's: re-read it on every refresh so a change made on
  // another phone lands here too.
  const live = useRef(true)
  const loadTarget = useCallback(() => getHillsTarget()
    .then(d => live.current && setTgt(d.target))
    .catch(e => live.current && setError(e.message)), [])
  useEffect(() => {
    live.current = true
    loadTarget()
    return () => { live.current = false }
  }, [loadTarget])
  useAutoRefresh(loadTarget)

  const fixtureId = tgt?.fixture_id
  useEffect(() => {
    if (!fixtureId) return
    let on = true
    getHillsBuilder(floor)
      .then(body => { if (on) { setResult({ fixtureId, body }); setError(null) } })
      .catch(e => on && setError(e.message))
    return () => { on = false }
  }, [fixtureId, floor])
  // Only the current target's builders: a fetch for an old target never shows.
  const data = result && result.fixtureId === fixtureId ? result.body : null

  const combos = data?.combos || []
  const sel = combos.find(c => comboKey(c) === chosen) || null
  const pickCombo = c => {
    const k = comboKey(c)
    setChosen(k === chosen ? null : k)
    setPrice(null)
  }
  const pct = tgt?.boost_pct ?? 25
  const paid = sel && price != null ? boosted(price, pct) : null
  const ev = paid != null ? paid / sel.fair - 1 : null

  const done = () => { setPicking(false); loadTarget() }
  const clear = () => clearHillsTarget().then(() => { setChosen(null); loadTarget() })

  if (tgt === undefined) return <p className={s.empty}>{error ?? 'Loading…'}</p>
  if (!tgt || picking) {
    return (
      <div className={s.wrap}>
        <p className={s.intro}>{tgt ? 'Change the target match for the whole team.'
          : 'No target match. Pick the one William Hill has boosted builders on — the whole team sees it.'}</p>
        <TargetPicker current={tgt} onSet={done} onCancel={() => setPicking(false)} />
      </div>
    )
  }

  return (
    <div className={s.wrap}>
      <div className={s.target}>
        <span className={s.targetMatch}>{tgt.match}</span>
        <span className={s.targetMeta}>
          {kickoff(tgt.kickoff)} · {tgt.competition} · <b>+{pct}%</b> · set by {tgt.set_by}
        </span>
        <span className={s.targetActions}>
          <button type="button" className={s.link} onClick={() => setPicking(true)}>Change</button>
          <button type="button" className={s.link} onClick={clear}>Clear</button>
        </span>
      </div>

      <div className={s.wheels}>
        <OddsWheel id="bb-floor" label="Fair ≥" ladder={FAIR_LADDER} decimalOnly
          value={floor} seat={DEFAULT_FLOOR} onChange={setFloor} />
        <OddsWheel id="bb-price" label="WH price" ladder={PRICE_LADDER}
          value={price} seat={sel ? sel.singles : 6} onChange={setPrice} />
      </div>

      <div className={`${s.result} ${ev == null ? '' : ev >= 0 ? s.good : s.bad}`}>
        {!sel ? 'Tap a builder, then spin its William Hill price.'
          : paid == null ? `Fair ${sel.fair.toFixed(2)} — spin the price William Hill quotes.`
          : <>Pays {paid.toFixed(2)} boosted · fair {sel.fair.toFixed(2)} · <b>EV {signed(ev)}</b></>}
      </div>

      {error && <p className={s.error}>{error}</p>}
      {data && combos.length === 0 && (
        <p className={s.empty}>{data.legs === 0
          ? 'William Hill has no priced lines on this match yet.'
          : `No builders at fair ${floor.toFixed(1)} or longer.`}</p>
      )}
      <div className={s.list}>
        {combos.length > 0 && (
          <div className={`${s.row} ${s.colHead}`}><span>Builder</span><span>Singles</span><span>Fair</span></div>
        )}
        {combos.map(c => {
          const k = comboKey(c)
          return (
            <button key={k} type="button" className={`${s.row} ${k === chosen ? s.rowOn : ''}`}
              aria-pressed={k === chosen} onClick={() => pickCombo(c)}>
              <span className={s.legs}>{c.legs.map(l => <span key={l.pick}>{l.pick}</span>)}</span>
              <span className={s.num}>{c.singles.toFixed(2)}</span>
              <span className={s.num}>{c.fair.toFixed(2)}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
