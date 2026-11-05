import { useState, useEffect } from 'react'
import styles from './KellyPage.module.css'

/**
 * Kelly staking calculator — pure client-side maths, no API.
 *
 * For a single win/lose bet at decimal odds d with true win probability p, the
 * Kelly-optimal fraction of bankroll to stake is
 *
 *     f* = (p·d − 1) / (d − 1)
 *
 * which is the edge (p·d − 1, the EV per £1 staked) divided by the net odds
 * (d − 1, the profit per £1 on a win). f* ≤ 0 means the price is no better than
 * fair — no bet. A fractional-Kelly multiplier (½, ¼, …) scales the stake down
 * to trade growth for a smoother ride; the calculator reports the number, the
 * staking decision stays with the user.
 *
 * The true chance is entered either as fair odds (p = 1/fair — the form the
 * coupon tracker already produces after de-vig) or as a win %, whichever is to
 * hand. Bankroll and the chosen fraction persist locally so they survive a
 * reload; nothing here is sent anywhere.
 */

// Digits and a single dot, capped decimals and length. Shared by every field;
// the caller bounds the value's meaning (odds, %, pounds), this only keeps the
// text well-formed.
function numeric(v, decimals = 2) {
  v = String(v).replace(/[^\d.]/g, '')
  v = v.replace(/^0+(?=\d)/, '')
  const parts = v.split('.')
  if (parts.length > 2) v = parts[0] + '.' + parts.slice(1).join('')
  const dot = v.indexOf('.')
  if (dot !== -1) v = v.slice(0, dot + 1) + v.slice(dot + 1).replace(/\./g, '').slice(0, decimals)
  return v.slice(0, 9)
}

const load = (key, fallback) => {
  try { const v = localStorage.getItem(key); return v == null ? fallback : v } catch { return fallback }
}
const save = (key, v) => { try { localStorage.setItem(key, v) } catch { /* private mode */ } }

const money = v =>
  '£' + v.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const pct = (v, dp = 1) => (v * 100).toLocaleString('en-GB', { minimumFractionDigits: dp, maximumFractionDigits: dp }) + '%'
const signedPct = v => (v > 0 ? '+' : v < 0 ? '−' : '') + pct(Math.abs(v))

// Preset multipliers. Full Kelly is the raw criterion; halves and quarters are
// the common risk-tempered choices.
const FRACTIONS = [
  { label: 'Full', mult: 1 },
  { label: '½', mult: 0.5 },
  { label: '⅓', mult: 1 / 3 },
  { label: '¼', mult: 0.25 },
]

export default function KellyPage() {
  const [bankroll, setBankroll] = useState(() => load('kelly.bankroll', ''))
  const [odds, setOdds] = useState('')            // decimal odds you can take
  const [mode, setMode] = useState('fair')        // 'fair' | 'prob'
  const [fair, setFair] = useState('')            // fair (true) decimal odds
  const [prob, setProb] = useState('')            // true win %
  const [mult, setMult] = useState(() => parseFloat(load('kelly.mult', '1')))

  useEffect(() => { save('kelly.bankroll', bankroll) }, [bankroll])
  useEffect(() => { save('kelly.mult', String(mult)) }, [mult])

  const d = parseFloat(odds)
  const bank = parseFloat(bankroll)
  // True probability from whichever estimate mode is active.
  const p = mode === 'fair'
    ? (parseFloat(fair) > 1 ? 1 / parseFloat(fair) : NaN)
    : parseFloat(prob) / 100

  const validOdds = Number.isFinite(d) && d > 1
  const validP = Number.isFinite(p) && p > 0 && p <= 1
  const ready = validOdds && validP

  // Edge = EV per £1 staked; f* the full-Kelly fraction; applied is after the
  // multiplier and floored at zero (a −EV price stakes nothing).
  const edge = ready ? p * d - 1 : null
  const fullK = ready ? (p * d - 1) / (d - 1) : null
  const applied = fullK == null ? null : Math.max(0, fullK) * mult
  const stake = (applied != null && Number.isFinite(bank) && bank > 0) ? applied * bank : null
  const impliedP = validOdds ? 1 / d : null
  const noEdge = fullK != null && fullK <= 0

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <span>Kelly staking</span><span className={styles.tag}>Calculator</span>
      </div>

      <div className={styles.fields}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Bankroll</span>
          <div className={styles.display}>
            <span className={styles.currency}>£</span>
            <input
              className={styles.input} value={bankroll}
              onChange={e => setBankroll(numeric(e.target.value))}
              inputMode="decimal" type="text" placeholder="0.00"
              aria-label="Bankroll" autoComplete="off"
            />
          </div>
        </label>

        <label className={styles.field}>
          <span className={styles.fieldLabel}>Odds taken</span>
          <div className={styles.display}>
            <input
              className={styles.input} value={odds}
              onChange={e => setOdds(numeric(e.target.value))}
              inputMode="decimal" type="text" placeholder="2.00"
              aria-label="Decimal odds taken" autoComplete="off"
            />
          </div>
        </label>
      </div>

      {/* How the true chance is expressed — a fair price (de-vigged) or a raw %. */}
      <div className={styles.seg} role="group" aria-label="Estimate the true chance as">
        <button
          type="button" aria-pressed={mode === 'fair'}
          className={`${styles.segBtn} ${mode === 'fair' ? styles.on : ''}`}
          onClick={() => setMode('fair')}
        >Fair odds</button>
        <button
          type="button" aria-pressed={mode === 'prob'}
          className={`${styles.segBtn} ${mode === 'prob' ? styles.on : ''}`}
          onClick={() => setMode('prob')}
        >Win %</button>
      </div>

      <label className={styles.field}>
        <span className={styles.fieldLabel}>
          {mode === 'fair' ? 'Fair odds (your true price)' : 'True win chance'}
        </span>
        <div className={styles.display}>
          {mode === 'fair' ? (
            <input
              className={styles.input} value={fair}
              onChange={e => setFair(numeric(e.target.value))}
              inputMode="decimal" type="text" placeholder="1.80"
              aria-label="Fair decimal odds" autoComplete="off"
            />
          ) : (
            <>
              <input
                className={styles.input} value={prob}
                onChange={e => setProb(numeric(e.target.value, 1))}
                inputMode="decimal" type="text" placeholder="55"
                aria-label="True win percentage" autoComplete="off"
              />
              <span className={styles.suffix}>%</span>
            </>
          )}
        </div>
      </label>

      {/* Fractional Kelly. */}
      <div className={styles.fracRow} role="group" aria-label="Kelly fraction">
        {FRACTIONS.map(f => (
          <button
            key={f.label} type="button"
            aria-pressed={Math.abs(mult - f.mult) < 1e-9}
            className={`${styles.fracBtn} ${Math.abs(mult - f.mult) < 1e-9 ? styles.on : ''}`}
            onClick={() => setMult(f.mult)}
          >{f.label}</button>
        ))}
      </div>

      {/* Result. */}
      <div className={styles.result}>
        <div className={styles.stakeLabel}>
          Stake · {FRACTIONS.find(f => Math.abs(mult - f.mult) < 1e-9)?.label ?? pct(mult, 0)} Kelly
        </div>
        <div className={`${styles.stake} ${noEdge ? styles.flat : ''}`}>
          {stake == null ? '—' : money(stake)}
        </div>
        {noEdge
          ? <div className={styles.note}>No edge — the price is no better than fair, so Kelly stakes nothing.</div>
          : stake == null && ready
            ? <div className={styles.note}>Enter a bankroll to size the stake.</div>
            : null}

        <div className={styles.stats}>
          <div className={styles.stat}>
            <div className={styles.statLabel}>Full Kelly</div>
            <div className={styles.statVal}>{fullK == null ? '—' : pct(Math.max(0, fullK))}</div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statLabel}>Applied</div>
            <div className={styles.statVal}>{applied == null ? '—' : pct(applied)}</div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statLabel}>Edge</div>
            <div className={`${styles.statVal} ${edge == null ? '' : edge > 0 ? styles.pos : styles.neg}`}>
              {edge == null ? '—' : signedPct(edge)}
            </div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statLabel}>Your chance</div>
            <div className={styles.statVal}>{validP ? pct(p) : '—'}</div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statLabel}>Price implies</div>
            <div className={styles.statVal}>{impliedP == null ? '—' : pct(impliedP)}</div>
          </div>
        </div>
      </div>

      <p className={styles.foot}>
        Kelly maximises long-run growth at full stake; a fraction trades growth
        for lower swings. Figures only — the call is yours.
      </p>
    </div>
  )
}
