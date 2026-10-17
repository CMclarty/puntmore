import { useState, useMemo, useCallback, useRef } from 'react'
import OddsWheel from '../components/OddsWheel'
import { FIELDS, RUNNER_COUNTS, priceBoost, verdictFor } from '../boost'
import styles from './BoostPage.module.css'

/**
 * Virtual racing boost calculator — should this boosted price be backed, and
 * each-way or win only? Pure client-side maths (see boost.js), built to be
 * answered in a few taps: runners, then a common boost or the two prices.
 *
 * Since Oct 2026 the boost is priced inside the virtuals ledger (/virtuals,
 * PnlPage), where the bet is filed in the same tap. This page is what /virtuals
 * shows an account granted `boost` without `pnl` — the calculator alone.
 */

// The prices virtual cards actually show, in decimal, from evens up. A boost
// starts and lands on one of these, so it's a short wheel.
const DECIMALS = [
  2, 2.1, 2.2, 2.25, 2.38, 2.5, 2.62, 2.75, 2.88, 3, 3.25, 3.5, 3.75, 4, 4.33,
  4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9, 9.5, 10, 11, 12, 13, 15, 17, 19, 21,
]
const LADDER = DECIMALS.map(d => ({ label: d.toFixed(2), dec: d }))
const DEFAULT = 4

const pct = (v, dp = 0) => (v * 100).toFixed(dp) + '%'
const signed = v => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'
const dec = d => d.toFixed(2)

export default function BoostPage() {
  // Shared with the virtuals P&L's runners wheel (same race, one after the
  // other), so a field picked on either page is already set on the other.
  const [runners, setRunners] = useState(() => {
    try {
      const n = Number(localStorage.getItem('virtuals.runners'))
      return FIELDS[n] ? n : null
    } catch { return null }
  })
  const [price, setPrice] = useState(DEFAULT)      // decimal odds
  const [boosted, setBoosted] = useState(DEFAULT)
  const [places, setPlaces] = useState(null) // null = the field's usual terms
  const [terms, setTerms] = useState(null)

  const pickRunners = n => {
    setRunners(n); setPlaces(null); setTerms(null)
    try { localStorage.setItem('virtuals.runners', String(n)) } catch { /* private mode */ }
  }

  // The boosted price is derived FROM the price, so the boosted wheel follows
  // the price wheel: it keeps however many rungs above the price it was set,
  // so an untouched boosted wheel sits on the price and a spin of the price
  // carries a set boost along with it rather than leaving it behind.
  // Read through a ref so the handler stays stable (OddsWheel needs that) and
  // the two updates are plain values, not one setter nested in another.
  const cur = useRef({ price, boosted })
  cur.current = { price, boosted }
  const onPrice = useCallback(next => {
    const at = d => DECIMALS.indexOf(d)
    const { price: prev, boosted: b } = cur.current
    const gap = Math.max(0, at(b) - at(prev))
    setPrice(next)
    setBoosted(DECIMALS[Math.min(DECIMALS.length - 1, at(next) + gap)])
  }, [])

  const r = useMemo(() => {
    if (!runners || !(boosted > price)) return null
    return priceBoost({
      runners,
      price,
      boosted,
      places: places ?? undefined,
      terms: terms ?? undefined,
    })
  }, [runners, price, boosted, places, terms])

  const field = runners ? FIELDS[runners] : null
  const k = places ?? field?.places
  const t = terms ?? field?.terms
  const customTerms = places != null || terms != null

  const verdict = verdictFor(r)
  const noBoost = runners && !(boosted > price)

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <span>Virtual boost</span>
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
        <div className={styles.prices}>
          <OddsWheel
            id="boost-price" label="Price" ladder={LADDER} decimalOnly
            value={price} seat={DEFAULT} onChange={onPrice}
          />
          <span className={styles.arrowBig}>→</span>
          <OddsWheel
            id="boost-boosted" label="Boosted" ladder={LADDER} decimalOnly
            value={boosted} seat={DEFAULT} onChange={setBoosted}
          />
        </div>
      </section>

      <div className={`${styles.result} ${verdict ? styles[verdict.tone] : ''}`}>
        {!r ? (
          <div className={styles.waiting}>
            {noBoost ? 'Spin the boosted price above the price.' : 'Pick the runners.'}
          </div>
        ) : (
          <>
            <div className={styles.verdictRow}>
              <span className={styles.verdict}>{verdict.word}</span>
              <span className={styles.verdictEv}>{signed(verdict.ev)}</span>
            </div>
            <div className={styles.sub}>
              {verdict.tone === 'thin'
                ? 'Within the model’s ±1% — marginal.'
                : verdict.word === 'Skip'
                  ? `Needs ${dec(r.breakEvenEw)} each way, ${dec(r.fairWin)} win only.`
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
