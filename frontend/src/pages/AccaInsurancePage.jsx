import { useState, useEffect, useMemo } from 'react'
import { Link, useLocation } from 'react-router-dom'
import {
  DEFAULTS, GRID_PCTS, kellyGrowth, breakevenProb, evaluate, grid, probsFromAccaPct, probsFromFair,
} from '../accaInsurance'
import styles from './FeedPage.module.css'
import a from './AccaInsurancePage.module.css'

/**
 * Acca insurance calculator: any qualifying acca's EV under the one-loser
 * refund, from per-leg fair odds or one acca %, plus the odds × acca % grid.
 * All maths is accaInsurance.js; this page only collects inputs.
 *
 * Settings are shared with the Paddy page's Acca Min card (same storage key),
 * and the card's "Open in calculator" hands its legs over as route state.
 */
const SETTINGS_KEY = 'paddy.acca'
const pct = (v, dp = 1) => (v * 100).toFixed(dp) + '%'
const signedPct = v => (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'
const gbp = v => (v < 0 ? '−£' : '£') + Math.abs(v).toFixed(2)

function loadSettings() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY)) } } catch { return DEFAULTS }
}
const blankLeg = () => ({ odds: '1.30', fair: '1.30', void: false, name: '' })

export default function AccaInsurancePage() {
  const location = useLocation()
  const [cfg, setCfg] = useState(loadSettings)
  const [mode, setMode] = useState('fair')   // 'fair' | 'pct'
  const [accaPct, setAccaPct] = useState('100')
  const [legs, setLegs] = useState(() =>
    location.state?.legs?.map(l => ({ odds: String(l.odds), fair: String(+l.fair.toFixed(6)), void: false, name: l.name }))
    ?? Array.from({ length: cfg.legs }, blankLeg))

  useEffect(() => {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(cfg)) } catch { /* private mode */ }
  }, [cfg])

  const setCfgField = (k, v) => {
    let n = parseFloat(v)
    if (!Number.isFinite(n) || n < 0) return
    if (k === 'legs') {
      n = Math.min(10, Math.max(2, Math.round(n)))
      setLegs(ls => ls.length >= n ? ls.slice(0, n) : [...ls, ...Array.from({ length: n - ls.length }, blankLeg)])
    }
    setCfg(c => ({ ...c, [k]: n }))
  }
  const setLeg = (i, patch) => setLegs(ls => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)))

  const result = useMemo(() => {
    const odds = legs.map(l => parseFloat(l.odds))
    if (odds.some(o => !(o > 1))) return { error: 'Every leg needs odds above 1.00.' }
    let probs
    if (mode === 'fair') {
      const fair = legs.map(l => parseFloat(l.fair))
      if (fair.some(f => !(f > 1))) return { error: 'Every leg needs fair odds above 1.00.' }
      probs = probsFromFair(fair)
    } else {
      const r = parseFloat(accaPct) / 100
      if (!(r > 0)) return { error: 'Enter the acca % (100 = neutral).' }
      probs = probsFromAccaPct(odds, r)
    }
    return evaluate({ odds, probs, voids: legs.map(l => l.void) }, cfg)
  }, [legs, mode, accaPct, cfg])

  const avgOdds = useMemo(() => {
    const os = legs.map(l => parseFloat(l.odds)).filter(o => o > 1)
    return os.length ? Math.pow(os.reduce((x, o) => x * o, 1), 1 / os.length) : null
  }, [legs])
  const breakeven = avgOdds ? breakevenProb(avgOdds, cfg) : null
  const table = useMemo(() => grid({ ...cfg, stake: 50 }), [cfg])

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <span className={styles.title}>Acca insurance</span>
        <Link className={a.back} to="/paddy">‹ Paddy</Link>
      </div>

      <section className={a.block}>
        <div className={a.cfg}>
          {[['legs', 'Legs', 1], ['stake', 'Stake £', 1], ['refundCap', 'Refund cap £', 1], ['refundValue', 'Refund worth', 0.05]].map(([k, l, step]) => (
            <label key={k}>{l}
              <input type="number" inputMode="decimal" step={step} defaultValue={cfg[k]}
                onChange={e => setCfgField(k, e.target.value)} />
            </label>
          ))}
          <label className={a.check}>
            <input type="checkbox" checked={cfg.voidQualifies}
              onChange={e => setCfg(c => ({ ...c, voidQualifies: e.target.checked }))} />
            Void leg still qualifies
          </label>
        </div>
        <p className={a.note}>Every leg must be {cfg.minOdds.toFixed(2)}+ · refund if exactly one leg loses.</p>
      </section>

      <section className={a.block}>
        <div className={a.modes} role="group" aria-label="True probability from">
          <button type="button" className={`${a.chip} ${mode === 'fair' ? a.on : ''}`} onClick={() => setMode('fair')}>Fair odds per leg</button>
          <button type="button" className={`${a.chip} ${mode === 'pct' ? a.on : ''}`} onClick={() => setMode('pct')}>One acca %</button>
          {mode === 'pct' && (
            <label className={a.pct}>Acca %
              <input type="number" inputMode="decimal" step="1" value={accaPct} onChange={e => setAccaPct(e.target.value)} />
            </label>
          )}
        </div>
        <div className={a.legHead}>
          <span>#</span><span>Odds</span>{mode === 'fair' && <span>Fair</span>}<span>Void</span>
        </div>
        {legs.map((l, i) => {
          const short = parseFloat(l.odds) < cfg.minOdds
          return (
            <div key={i} className={a.leg}>
              <span className={a.n} title={l.name}>{i + 1}</span>
              <input className={short ? a.bad : ''} type="number" inputMode="decimal" step="0.01" value={l.odds}
                onChange={e => setLeg(i, { odds: e.target.value })} aria-label={`Leg ${i + 1} odds`} />
              {mode === 'fair' && (
                <input type="number" inputMode="decimal" step="0.01" value={l.fair}
                  onChange={e => setLeg(i, { fair: e.target.value })} aria-label={`Leg ${i + 1} fair odds`} />
              )}
              <input type="checkbox" checked={l.void} onChange={e => setLeg(i, { void: e.target.checked })}
                aria-label={`Leg ${i + 1} void`} />
              {l.name && <span className={a.name}>{l.name}</span>}
            </div>
          )
        })}
      </section>

      <section className={a.block}>
        {result.error ? <p className={styles.error}>{result.error}</p> : (
          <>
            {result.problems.length > 0 && (
              <p className={a.flag}>Does not qualify — {result.problems.join('; ')}. EV below is without the refund.</p>
            )}
            <div className={a.out}>
              <div><span>Acca odds</span><b>{result.accaOdds.toFixed(2)}</b></div>
              <div><span>Raw acca %</span><b>{pct(result.rawAccaPct)}</b></div>
              <div><span>P(all win)</span><b>{pct(result.pWin)}</b></div>
              <div><span>P(refund)</span><b>{pct(result.pRefund)}</b></div>
              <div><span>P(full loss)</span><b>{pct(result.pLoss)}</b></div>
              {(k => (
                <div title="Growth per bet at the acca's optimal (Kelly) stake — the Paddy list ranks by this">
                  <span>Growth · Kelly</span><b>{pct(k.growth, 2)} · {pct(k.kelly, 0)}</b>
                </div>
              ))(kellyGrowth(result, cfg))}
              <div className={a.ev}><span>EV</span>
                <b className={result.evGbp >= 0 ? a.pos : a.neg}>{signedPct(result.evPct)} · {gbp(result.evGbp)}</b>
              </div>
            </div>
            {cfg.stake > cfg.refundCap && (
              <p className={a.note}>£{(cfg.stake - cfg.refundCap).toFixed(2)} of the stake is over the cap and uninsured — it is only worth the raw acca %.</p>
            )}
            {breakeven != null && (
              <p className={a.note}>
                Breakeven at these settings, equal legs at {avgOdds.toFixed(2)}: true win chance {breakeven.toFixed(4)} per leg (fair odds {(1 / breakeven).toFixed(3)}).
              </p>
            )}
          </>
        )}
      </section>

      <section className={a.block}>
        <div className={a.gridHead}>Grid · £50 stake · equal legs · EV %</div>
        <div className={a.gridWrap}>
          <table className={a.grid}>
            <thead>
              <tr><th>Avg odds</th>{GRID_PCTS.map(r => <th key={r}>{Math.round(r * 100)}%</th>)}</tr>
            </thead>
            <tbody>
              {table.map(row => (
                <tr key={row.odds}>
                  <th>{row.odds.toFixed(2)}</th>
                  {row.cells.map(c => (
                    <td key={c.r} className={c.evPct >= 0 ? a.pos : a.neg} title={gbp(c.evGbp)}>
                      {signedPct(c.evPct)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className={a.note}>
          Most of the edge is the refund. Shorter legs win at the same acca %, and EV is linear in stake: stake each acca up to the cap; above it a pound is only worth the raw acca %.
        </p>
      </section>
    </div>
  )
}
