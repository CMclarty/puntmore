/**
 * The P&L ledger as a Game Boy Advance battle screen, at /pnl/battle.
 *
 * A skin, not a fork: it reads the same `GET /pnl/bets` and files through the
 * same `POST /pnl/bets` as the plain tracker, on the same stake/returned +
 * price_taken payload, and shares the fractional ladder with OddsWheel. Nothing
 * is stored differently, so a bet entered here shows up on /pnl and vice versa.
 *
 * The mapping: your bankroll is HP, the book holds the mirror image of it, a
 * settled bet is a turn, and the battle menu is lose / push / win.
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { getBets, addBet } from '../api'
import { LADDER, DEFAULT_PRICE } from '../components/OddsWheel'
import styles from './PnlBattlePage.module.css'

const parseDate = s => new Date(/[Z+]/.test(s) ? s : s + 'Z')

/** Same abbreviation rule as the plain tracker: k notation above £1,000. */
function magnitude(abs) {
  if (abs < 1000) return abs.toLocaleString('en-GB', { maximumFractionDigits: 2 })
  const k = abs / 1000
  const oneDp = Math.round(k * 10) / 10
  return oneDp >= 100 ? `${Math.round(k)}k` : `${oneDp.toFixed(1)}k`
}
const signed = v => (v > 0 ? '+' : v < 0 ? '−' : '') + '£' + magnitude(Math.abs(v))

const DEFAULT_I = LADDER.findIndex(t => t.dec === DEFAULT_PRICE)

export default function PnlBattlePage() {
  const [bets, setBets] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const [stake, setStake] = useState('')
  const [oddsI, setOddsI] = useState(DEFAULT_I === -1 ? 0 : DEFAULT_I)
  const [result, setResult] = useState(null)
  const [flash, setFlash] = useState(null)   // the line the box says after a bet

  const sparkRef = useRef(null)

  useEffect(() => { getBets().then(setBets).catch(e => setError(e.message)) }, [])

  const { cum, total, peak } = useMemo(() => {
    const arr = [0]
    let t = 0
    for (const b of bets) { t += b.amount; arr.push(t) }
    // The HP scale is anchored to your best cumulative profit so far — a real
    // high-water mark rather than an invented ceiling. Never zero, or the bar
    // maths divides by nothing on a fresh ledger.
    return { cum: arr, total: t, peak: Math.max(...arr.map(Math.abs), 1) }
  }, [bets])

  const odds = LADDER[oddsI]
  const stakeV = parseFloat(stake)
  const hasStake = stake !== '' && stakeV > 0
  const returned =
    result === 'win' ? stakeV * odds.dec
    : result === 'lose' ? 0
    : result === 'push' ? stakeV : null
  const net = hasStake && result ? Math.round((returned - stakeV) * 100) / 100 : 0
  const canAdd = !busy && hasStake && result !== null

  // What the bars read once this bet lands — a live preview of the turn.
  const after = total + net
  const hp = Math.max(3, Math.min(100, (after / peak) * 100))
  const hpClass = hp <= 22 ? styles.red : hp <= 50 ? styles.amber : ''

  // ── the ledger curve, drawn hard-edged on the field ──────────────────────
  const drawSpark = useCallback(() => {
    const c = sparkRef.current
    if (!c) return
    const rect = c.getBoundingClientRect()
    if (!rect.width) return
    const dpr = window.devicePixelRatio || 1
    c.width = rect.width * dpr
    c.height = rect.height * dpr
    const g = c.getContext('2d')
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    const W = rect.width, H = rect.height, pad = 6

    g.clearRect(0, 0, W, H)
    if (cum.length < 2) return
    const lo = Math.min(0, ...cum), hi = Math.max(0, ...cum)
    const span = (hi - lo) || 1
    const X = i => pad + (W - pad * 2) * (i / (cum.length - 1))
    const Y = v => H - pad - (H - pad * 2) * ((v - lo) / span)

    g.strokeStyle = 'rgba(255,255,255,.28)'
    g.setLineDash([4, 4]); g.lineWidth = 2
    g.beginPath(); g.moveTo(pad, Y(0)); g.lineTo(W - pad, Y(0)); g.stroke()
    g.setLineDash([])

    g.beginPath()
    cum.forEach((v, i) => (i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))))
    g.lineTo(X(cum.length - 1), Y(0)); g.lineTo(X(0), Y(0)); g.closePath()
    g.fillStyle = total < 0 ? 'rgba(232,80,58,.24)' : 'rgba(88,214,141,.26)'
    g.fill()

    g.beginPath()
    cum.forEach((v, i) => (i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))))
    g.strokeStyle = total < 0 ? '#ff9d8b' : '#8ef0b4'
    g.lineWidth = 3; g.lineJoin = 'miter'
    g.stroke()

    g.fillStyle = '#f2c14e'
    g.fillRect(X(cum.length - 1) - 3, Y(cum[cum.length - 1]) - 3, 6, 6)
  }, [cum, total])

  useEffect(() => { drawSpark() }, [drawSpark])
  useEffect(() => {
    // Coalesced + size-guarded, for the same reason as the plain tracker: a
    // phone's URL bar fires a resize storm on scroll and this repaints a canvas.
    let raf = 0, lastW = 0, lastH = 0
    const onResize = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const c = sparkRef.current
        if (!c) return
        const r = c.getBoundingClientRect()
        if (Math.abs(r.width - lastW) < 1 && Math.abs(r.height - lastH) < 1) return
        lastW = r.width; lastH = r.height
        drawSpark()
      })
    }
    window.addEventListener('resize', onResize)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize) }
  }, [drawSpark])

  async function submit() {
    if (!canAdd) return
    setBusy(true); setError(null)
    try {
      const bet = await addBet({
        stake: Math.round(stakeV * 100) / 100,
        returned: Math.round(returned * 100) / 100,
        price_taken: odds.dec,
      })
      setBets(bs => [...bs, bet])
      setFlash(
        result === 'win' ? `It's super effective! ${signed(net)} off the book.`
        : result === 'lose' ? `The bet fainted. ${signed(net)}.`
        : 'Stake returned. Nothing gained, nothing lost.',
      )
      setStake(''); setResult(null); setOddsI(DEFAULT_I === -1 ? 0 : DEFAULT_I)
    } catch (e) {
      setError(e.message)
    } finally { setBusy(false) }
  }

  function say() {
    if (error) return <span className={styles.bad}>{error}</span>
    if (flash) return flash
    if (!hasStake) return 'What will you do?'
    if (!result) return `Staked £${magnitude(stakeV)} at ${odds.label}. How did it land?`
    if (result === 'win') return `A win pays ${signed(net)}.`
    if (result === 'lose') return `A loss costs ${signed(net)}.`
    return 'A push returns the stake.'
  }

  const lvl = bets.length

  return (
    <div className={styles.page}>
      <div className={styles.screen}>
        {/* ── field ─────────────────────────────────────────────── */}
        <div className={styles.field}>
          <div className={styles.card}>
            <div className={styles.cardTop}>
              <span className={styles.name}>The Book</span>
              <span className={styles.lvl}>Lv {lvl}</span>
            </div>
            <div className={styles.barRow}>
              <span className={styles.barLabel}>HP</span>
              <div className={styles.barTrack}>
                <div
                  className={`${styles.barFill} ${styles.red}`}
                  style={{ width: `${Math.max(3, 100 - hp)}%` }}
                />
              </div>
            </div>
            <div className={styles.cardFig}>
              <span className={styles.cardSub}>Margin held</span>
              <span className={after < 0 ? styles.up : styles.down}>{signed(-after)}</span>
            </div>
          </div>

          <div className={`${styles.card} ${styles.you}`}>
            <div className={styles.cardTop}>
              <span className={styles.name}>You</span>
              <span className={styles.lvl}>Lv {lvl}</span>
            </div>
            <div className={styles.barRow}>
              <span className={styles.barLabel}>HP</span>
              <div className={styles.barTrack}>
                <div className={`${styles.barFill} ${hpClass}`} style={{ width: `${hp}%` }} />
              </div>
            </div>
            <div className={styles.cardFig}>
              <span className={styles.cardSub}>Total profit</span>
              <span className={after < 0 ? styles.down : styles.up}>{signed(after)}</span>
            </div>
          </div>

          <canvas
            ref={sparkRef}
            className={styles.spark}
            aria-label={`Cumulative profit over ${lvl} bets, currently ${signed(total)}`}
          />
        </div>

        {/* ── dialogue ──────────────────────────────────────────── */}
        <div className={styles.dialogue}>
          <div className={styles.frame}>
            <p className={styles.say} aria-live="polite">
              {say()}<span className={styles.cursor}>▼</span>
            </p>

            <div className={styles.rows}>
              <label className={styles.slot}>
                <span className={styles.slotLabel}>Stake</span>
                <span className={styles.slotBox}>
                  £
                  <input
                    value={stake}
                    onChange={e => {
                      setFlash(null)
                      setStake(
                        e.target.value.replace(/[^\d.]/g, '').replace(/^0+(?=\d)/, '').slice(0, 9),
                      )
                    }}
                    inputMode="decimal"
                    type="text"
                    placeholder="0"
                    aria-label="Stake"
                    autoComplete="off"
                  />
                </span>
              </label>

              <div className={styles.slot}>
                <span className={styles.slotLabel}>Odds</span>
                <span className={styles.slotBox}>
                  <button
                    type="button"
                    className={styles.stepper}
                    onClick={() => setOddsI(i => Math.max(0, i - 1))}
                    aria-label="Shorter price"
                  >◀</button>
                  <span className={styles.oddsVal}>{odds.label}</span>
                  <button
                    type="button"
                    className={styles.stepper}
                    onClick={() => setOddsI(i => Math.min(LADDER.length - 1, i + 1))}
                    aria-label="Longer price"
                  >▶</button>
                </span>
              </div>
            </div>

            <div className={styles.menu} role="group" aria-label="Result">
              {[['lose', 'Lose'], ['push', 'Push'], ['win', 'Win']].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={`${styles.opt} ${styles[key]}`}
                  aria-pressed={result === key}
                  onClick={() => { setFlash(null); setResult(key) }}
                >{label}</button>
              ))}
            </div>

            <button
              type="button"
              className={styles.confirm}
              onClick={submit}
              disabled={!canAdd}
            >{busy ? 'Saving…' : 'Enter'}</button>

            <Link to="/pnl" className={styles.exit}>▲ back to the plain ledger</Link>
          </div>
        </div>
      </div>
    </div>
  )
}
