import { useState, useMemo } from 'react'
import { FRACTIONAL_LADDER, fractionToDecimal } from '../odds'
import { FIELDS, RUNNER_COUNTS, priceBoost } from '../boost'
import styles from './BoostPage.module.css'

/**
 * Virtual racing boost calculator — should this boosted price be backed, and
 * each-way or win only? Pure client-side maths (see boost.js), built to be
 * answered in a few taps: runners, then a common boost or the two prices.
 */

// Prices a virtual boost realistically starts from or lands on.
const PRICES = FRACTIONAL_LADDER.filter(f => {
  const d = fractionToDecimal(f)
  return d >= 2 && d <= 26
})

// The boosts that come round most often, one tap each.
const PRESETS = [
  ['6/4', '2/1'], ['7/4', '5/2'], ['2/1', '3/1'], ['3/1', '4/1'],
  ['3/1', '5/1'], ['11/2', '7/1'], ['7/1', '9/1'],
]

// EV below this is inside the model's ±1% noise — say so rather than call it.
const THIN = 0.02

const pct = (v, dp = 0) => (v * 100).toFixed(dp) + '%'
const signed = v => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'
// Break-even prices rarely land on a board price, so show them unrounded.
const toFraction = d => (d - 1).toFixed(1) + '/1'

export default function BoostPage() {
  const [runners, setRunners] = useState(null)
  const [price, setPrice] = useState('')     // fractional strings from the ladder
  const [boosted, setBoosted] = useState('')
  const [places, setPlaces] = useState(null) // null = the field's usual terms
  const [terms, setTerms] = useState(null)

  const pickRunners = n => { setRunners(n); setPlaces(null); setTerms(null) }
  const pickPreset = ([a, b]) => { setPrice(a); setBoosted(b) }

  const r = useMemo(() => {
    if (!runners || !price || !boosted) return null
    return priceBoost({
      runners,
      price: fractionToDecimal(price),
      boosted: fractionToDecimal(boosted),
      places: places ?? undefined,
      terms: terms ?? undefined,
    })
  }, [runners, price, boosted, places, terms])

  const field = runners ? FIELDS[runners] : null
  const k = places ?? field?.places
  const t = terms ?? field?.terms
  const customTerms = places != null || terms != null

  let verdict = null
  if (r) {
    const ew = r.evEw >= r.evWin
    const best = Math.max(r.evEw, r.evWin)
    verdict = best <= 0
      ? { word: 'Skip', tone: 'neg', ev: best }
      : { word: ew ? 'Each way' : 'Win only', tone: best < THIN ? 'thin' : 'pos', ev: best, thin: best < THIN }
  }
  const noBoost = price && boosted && fractionToDecimal(boosted) <= fractionToDecimal(price)

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <span>Virtual boost</span><span className={styles.tag}>Calculator</span>
      </div>

      <section className={styles.block}>
        <div className={styles.label}>Runners</div>
        <div className={styles.runners} role="group" aria-label="Runners">
          {RUNNER_COUNTS.map(n => (
            <button
              key={n} type="button" aria-pressed={runners === n}
              className={`${styles.chip} ${runners === n ? styles.on : ''}`}
              onClick={() => pickRunners(n)}
            >{n}</button>
          ))}
        </div>
      </section>

      <section className={styles.block}>
        <div className={styles.label}>Boost</div>
        <div className={styles.presets} role="group" aria-label="Common boosts">
          {PRESETS.map(p => {
            const on = price === p[0] && boosted === p[1]
            return (
              <button
                key={p.join('-')} type="button" aria-pressed={on}
                className={`${styles.chip} ${styles.preset} ${on ? styles.on : ''}`}
                onClick={() => pickPreset(p)}
              >{p[0]}<span className={styles.arrow}>→</span>{p[1]}</button>
            )
          })}
        </div>
        <div className={styles.prices}>
          <select
            className={styles.select} value={price} aria-label="Price before boost"
            onChange={e => setPrice(e.target.value)}
          >
            <option value="" disabled>Price</option>
            {PRICES.map(f => <option key={f} value={f}>{f}</option>)}
          </select>
          <span className={styles.arrowBig}>→</span>
          <select
            className={styles.select} value={boosted} aria-label="Boosted price"
            onChange={e => setBoosted(e.target.value)}
          >
            <option value="" disabled>Boosted</option>
            {PRICES.map(f => <option key={f} value={f}>{f}</option>)}
          </select>
        </div>
      </section>

      <div className={`${styles.result} ${verdict ? styles[verdict.tone] : ''}`}>
        {!r ? (
          <div className={styles.waiting}>
            {noBoost ? 'Boosted price is no higher than the price.' : 'Pick runners and a boost.'}
          </div>
        ) : (
          <>
            <div className={styles.verdictRow}>
              <span className={styles.verdict}>{verdict.word}</span>
              <span className={styles.verdictEv}>{signed(verdict.ev)}</span>
            </div>
            <div className={styles.sub}>
              {verdict.thin
                ? 'Within the model’s ±1% — marginal.'
                : verdict.word === 'Skip'
                  ? `Needs ${toFraction(r.breakEvenEw)} each way, ${toFraction(r.fairWin)} win only.`
                  : 'EV per £1 staked in total.'}
            </div>
            <div className={styles.stats}>
              <Stat label="Each way" v={r.evEw} />
              <Stat label="Win only" v={r.evWin} />
              <Stat label="Place part" v={r.evPlace} />
              <div className={styles.stat}>
                <div className={styles.statLabel}>Win · place</div>
                <div className={styles.statVal}>{pct(r.pWin)} · {pct(r.pPlace)}</div>
              </div>
            </div>
          </>
        )}
      </div>

      {field && (
        <section className={styles.terms}>
          <span className={styles.label}>
            Terms {customTerms ? '(changed)' : '(usual)'}
          </span>
          <div className={styles.termRow}>
            {[2, 3, 4].map(p => (
              <button
                key={p} type="button" aria-pressed={k === p}
                className={`${styles.mini} ${k === p ? styles.on : ''}`}
                onClick={() => setPlaces(p === field.places ? null : p)}
              >{p} pl</button>
            ))}
            <span className={styles.at}>@</span>
            {[4, 5].map(d => (
              <button
                key={d} type="button" aria-pressed={t === d}
                className={`${styles.mini} ${t === d ? styles.on : ''}`}
                onClick={() => setTerms(d === field.terms ? null : d)}
              >1/{d}</button>
            ))}
          </div>
        </section>
      )}

      <p className={styles.foot}>
        Priced from 1,449 settled A McLean races (Sep 2026): win chance is the
        price with the {field ? pct(field.overround - 1) : 'book'} overround
        stripped, places by Harville. Assumes Betfred pays the same place terms
        — change them above if not.
      </p>
    </div>
  )
}

function Stat({ label, v }) {
  return (
    <div className={styles.stat}>
      <div className={styles.statLabel}>{label}</div>
      <div className={`${styles.statVal} ${v > 0 ? styles.posTxt : styles.negTxt}`}>{signed(v)}</div>
    </div>
  )
}
