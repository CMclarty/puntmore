import { useState, useEffect, useRef, useCallback } from 'react'
import {
  getHillsFixtures, getHillsTarget, setHillsTarget, clearHillsTarget,
  getHillsBets, logHillsBet, settleHillsBet, removeHillsBet,
} from '../api'
import OddsWheel, { LADDER } from '../components/OddsWheel'
import useAutoRefresh from '../hooks/useAutoRefresh'
import s from './HillsBuilder.module.css'

/**
 * BB Boost — Hills' third offer: William Hill's boosted bet builder on the
 * team's ONE target match (25% on winnings, 50% on some cup finals).
 *
 *   Target    picked from this weekend's Premier League games and the cup /
 *             Champions League finals; one for the whole team, so it is shown
 *             with who set it and anyone can change it.
 *   WH price  a wheel: the builder price the William Hill app quotes.
 *   Fair      a wheel: the fair price for the same builder, from wherever it
 *             is found. Builders are priced by hand on purpose — their legs are
 *             correlated, so multiplying single prices gets them wrong, and no
 *             feed carries builder prices. An on-the-spot check: never saved.
 *   Log       the builders the team has placed on the target: legs as typed,
 *             who and when, and a result set by hand. Anyone opening the page
 *             sees what has been backed already. No prices or fairs are kept.
 *             Anyone can remove a bet: the first tap arms a warning, the
 *             second removes it.
 *
 * The boost is paid on winnings: a price P pays 1 + (P - 1) * (1 + boost),
 * and its EV is that over the fair price, less 1.
 */

// Builders are quoted fractionally and often long: the board ladder carried
// on past 25/1. Both wheels use it.
const PRICE_LADDER = [
  ...LADDER,
  ...[28, 33, 40, 50, 66, 80, 100, 125, 150, 200].map(n => ({ n, d: 1, label: `${n}/1`, dec: n + 1 })),
]
const SEAT = 7          // 6/1, where a builder price usually is
const RESULTS = [['open', 'Open'], ['won', 'Won'], ['lost', 'Lost']]
const placedAt = iso => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

const boosted = (price, pct) => 1 + (price - 1) * (1 + pct / 100)
const signed = v => `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)}%`
function kickoff(iso) {
  const d = new Date(iso)
  return d.toLocaleDateString([], { weekday: 'short' }) + ' '
    + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

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
  const [error, setError] = useState(null)
  const [price, setPrice] = useState(null)             // William Hill's quoted builder price
  const [fair, setFair] = useState(null)               // the fair price for the same builder
  const [bets, setBets] = useState([])
  const [legs, setLegs] = useState('')
  const [arming, setArming] = useState(null)           // id of the bet whose Remove shows the warning

  // The target is the team's: re-read it on every refresh so a change made on
  // another phone lands here too.
  const live = useRef(true)
  const loadTarget = useCallback(() => Promise.all([getHillsTarget(), getHillsBets()])
    .then(([t, b]) => { if (live.current) { setTgt(t.target); setBets(b.bets); setError(null) } })
    .catch(e => live.current && setError(e.message)), [])
  useEffect(() => {
    live.current = true
    loadTarget()
    return () => { live.current = false }
  }, [loadTarget])
  useAutoRefresh(loadTarget)

  const pct = tgt?.boost_pct ?? 25
  const paid = price != null ? boosted(price, pct) : null
  const ev = paid != null && fair != null ? paid / fair - 1 : null

  const done = () => { setPicking(false); loadTarget() }
  const clear = () => clearHillsTarget().then(loadTarget)
  const fail = e => setError(e.message)
  const log = e => {
    e.preventDefault()
    if (!legs.trim()) return
    logHillsBet(legs).then(() => { setLegs(''); loadTarget() }).catch(fail)
  }
  const settle = (id, result) => settleHillsBet(id, result).then(loadTarget).catch(fail)
  // Two taps: the first arms a warning (disarmed after a few seconds), the second removes.
  const remove = id => {
    if (arming !== id) { setArming(id); return }
    setArming(null)
    removeHillsBet(id).then(loadTarget).catch(fail)
  }
  useEffect(() => {
    if (arming == null) return
    const t = setTimeout(() => setArming(null), 4000)
    return () => clearTimeout(t)
  }, [arming])

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
        <OddsWheel id="bb-price" label="WH price" ladder={PRICE_LADDER}
          value={price} seat={SEAT} onChange={setPrice} />
        <OddsWheel id="bb-fair" label="Fair" ladder={PRICE_LADDER}
          value={fair} seat={price ?? SEAT} onChange={setFair} />
      </div>

      <div className={`${s.result} ${ev == null ? '' : ev >= 0 ? s.good : s.bad}`}>
        {paid == null ? 'Spin the builder price William Hill quotes.'
          : ev == null ? <>Pays <b>{paid.toFixed(2)}</b> boosted — spin its fair price.</>
          : <>Pays <b>{paid.toFixed(2)}</b> boosted · fair {fair.toFixed(2)} · <b>EV {signed(ev)}</b></>}
      </div>
      {error && <p className={s.error}>{error}</p>}

      <form className={s.logForm} onSubmit={log}>
        <input className={s.logInput} value={legs} onChange={e => setLegs(e.target.value)}
          maxLength={300} placeholder="Legs, e.g. O9.5 corners + Saka anytime" aria-label="Builder legs" />
        <button type="submit" className={s.primary} disabled={!legs.trim()}>Log bet</button>
      </form>

      <div className={s.logHead}>
        {bets.length ? `${bets.length} builder${bets.length === 1 ? '' : 's'} logged on this match`
          : 'No builders logged on this match yet.'}
      </div>
      <div className={s.list}>
        {bets.map(b => (
          <div key={b.id} className={`${s.bet} ${s[b.result] || ''}`}>
            <span className={s.betLegs}>{b.legs}</span>
            <span className={s.betMeta}>{b.placed_by} · {placedAt(b.placed_at)}</span>
            <span className={s.betResult} role="group" aria-label="Result">
              {RESULTS.map(([k, label]) => (
                <button key={k} type="button" className={`${s.chip} ${b.result === k ? s.chipOn : ''}`}
                  aria-pressed={b.result === k} onClick={() => settle(b.id, k)}>{label}</button>
              ))}
              <button type="button" className={arming === b.id ? s.warn : s.link}
                aria-label={arming === b.id ? 'Tap again to remove this bet' : 'Remove bet'}
                onClick={() => remove(b.id)}>
                {arming === b.id ? '⚠ Tap again to remove' : 'Remove'}
              </button>
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
