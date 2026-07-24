/**
 * The P&L tracker wearing a Game Boy Advance skin, at /pnl/battle.
 *
 * Deliberately the SAME PAGE as /pnl — same sections in the same order, same
 * figures, same entry flow — only dressed differently. It reads GET /pnl/bets
 * and files through POST /pnl/bets on the same payload, so a bet entered either
 * side shows up on both.
 *
 * The wheels are the shared OddsWheel component, unmodified: it styles itself
 * from the global tokens, and this page's CSS module redefines those locally,
 * so it picks up the skin for free.
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { getBets, addBet, deleteBet } from '../api'
import OddsWheel, { DEFAULT_PRICE } from '../components/OddsWheel'
import styles from './PnlBattlePage.module.css'

const parseDate = s => new Date(/[Z+]/.test(s) ? s : s + 'Z')
const vsSpPct = (price, sp) => (price / sp - 1) * 100
const pct = v => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(1) + '%'

/** Same abbreviation rule as the plain tracker: k notation above £1,000. */
function magnitude(abs) {
  if (abs < 1000) return abs.toLocaleString('en-GB', { maximumFractionDigits: 2 })
  const k = abs / 1000
  const oneDp = Math.round(k * 10) / 10
  return oneDp >= 100 ? `${Math.round(k)}k` : `${oneDp.toFixed(1)}k`
}
const money = v => (v < 0 ? '-' : '') + '£' + magnitude(Math.abs(v))
const signed = v => (v > 0 ? '+' : v < 0 ? '−' : '') + '£' + magnitude(Math.abs(v))
const exact = v =>
  (v > 0 ? '+' : v < 0 ? '−' : '') + '£' +
  Math.abs(v).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const BIG_ENTRY = 1000

export default function PnlBattlePage() {
  const [bets, setBets] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const chartRef = useRef(null)

  useEffect(() => { getBets().then(setBets).catch(e => setError(e.message)) }, [])

  // ── derived figures, same as the plain tracker ────────────────────────────
  const { cum, total, mtd, mtdCount, winrate, turnover, roi } = useMemo(() => {
    const arr = [0]
    let t = 0
    for (const b of bets) { t += b.amount; arr.push(t) }
    const now = new Date()
    const inMonth = bets.filter(b => {
      const d = parseDate(b.created_at)
      return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear()
    })
    const settled = bets.filter(b => b.amount !== 0)
    const wins = settled.filter(b => b.amount > 0).length
    const staked = bets.filter(b => b.stake != null)
    const to = staked.reduce((s, b) => s + b.stake, 0)
    const sp = staked.reduce((s, b) => s + b.amount, 0)
    return {
      cum: arr,
      total: t,
      mtd: inMonth.reduce((s, b) => s + b.amount, 0),
      mtdCount: inMonth.length,
      winrate: settled.length ? Math.round(100 * wins / settled.length) + '%' : '0%',
      turnover: to,
      roi: to > 0 ? (sp / to * 100).toFixed(1) + '%' : null,
    }
  }, [bets])

  // ── the cumulative chart, drawn hard-edged to suit the skin ───────────────
  const drawChart = useCallback(() => {
    const c = chartRef.current
    if (!c) return
    const rect = c.getBoundingClientRect()
    if (!rect.width) return
    const dpr = window.devicePixelRatio || 1
    c.width = rect.width * dpr
    c.height = rect.height * dpr
    const g = c.getContext('2d')
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    const W = rect.width, H = rect.height
    const padL = 4, padR = 42, padT = 8, padB = 16

    g.clearRect(0, 0, W, H)
    if (cum.length < 2) return
    let lo = Math.min(0, ...cum), hi = Math.max(0, ...cum)
    const sp = (hi - lo) || 100
    lo -= sp * 0.12; hi += sp * 0.12
    const X = i => padL + (W - padL - padR) * (i / (cum.length - 1))
    const Y = v => padT + (H - padT - padB) * (1 - (v - lo) / (hi - lo))

    // gridlines + right-hand labels
    g.font = '9px ui-monospace, monospace'
    g.textBaseline = 'middle'
    const step = niceStep(hi - lo)
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      const y = Math.round(Y(v)) + 0.5
      const zero = Math.abs(v) < 1e-6
      g.strokeStyle = zero ? 'rgba(255,255,255,.45)' : 'rgba(255,255,255,.16)'
      g.lineWidth = zero ? 2 : 1
      g.setLineDash(zero ? [] : [3, 3])
      g.beginPath(); g.moveTo(padL, y); g.lineTo(W - padR, y); g.stroke()
      g.setLineDash([])
      g.fillStyle = 'rgba(226,238,252,.66)'
      g.fillText(money(v), W - padR + 4, y)
    }

    // fill + curve
    g.beginPath()
    cum.forEach((v, i) => (i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))))
    g.lineTo(X(cum.length - 1), Y(0)); g.lineTo(X(0), Y(0)); g.closePath()
    g.fillStyle = total < 0 ? 'rgba(232,80,58,.22)' : 'rgba(88,214,141,.24)'
    g.fill()

    g.beginPath()
    cum.forEach((v, i) => (i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))))
    g.strokeStyle = total < 0 ? '#ff9d8b' : '#8ef0b4'
    g.lineWidth = 3; g.lineJoin = 'miter'; g.stroke()

    // endpoint block — square, not a dot
    g.fillStyle = '#f2c14e'
    g.fillRect(X(cum.length - 1) - 3, Y(cum[cum.length - 1]) - 3, 6, 6)
  }, [cum, total])

  useEffect(() => { drawChart() }, [drawChart])
  useEffect(() => {
    // Coalesced + size-guarded: a phone's URL bar fires a resize storm on
    // scroll and this repaints a canvas.
    let raf = 0, lastW = 0, lastH = 0
    const onResize = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const c = chartRef.current
        if (!c) return
        const r = c.getBoundingClientRect()
        if (Math.abs(r.width - lastW) < 1 && Math.abs(r.height - lastH) < 1) return
        lastW = r.width; lastH = r.height
        drawChart()
      })
    }
    window.addEventListener('resize', onResize)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize) }
  }, [drawChart])

  async function handleAdd(payload) {
    setBusy(true); setError(null)
    try {
      const bet = await addBet(payload)
      setBets(bs => [...bs, bet])
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function undoLast() {
    if (!bets.length || busy) return
    setBusy(true); setError(null)
    try {
      await deleteBet(bets[bets.length - 1].id)
      setBets(bs => bs.slice(0, -1))
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className={styles.page}>
      {/* ── cumulative P&L ─────────────────────────────────────── */}
      <section className={styles.top}>
        <div className={styles.eyebrow}>
          <span>Total profit</span><span className={styles.me}>Private</span>
        </div>

        <div className={styles.panel}>
          <div className={styles.stats}>
            <div className={styles.stat}>
              <div className={`${styles.figure} ${styles.total} ${total < 0 ? styles.neg : styles.pos}`}>
                {signed(total)}
              </div>
              <div className={styles.statCount}>{bets.length} bet{bets.length === 1 ? '' : 's'}</div>
            </div>
            <div className={styles.stat}>
              <div className={styles.statLabel}>Month to date</div>
              <div className={`${styles.figure} ${styles.mtd} ${mtd < 0 ? styles.neg : styles.pos}`}>
                {signed(mtd)}
              </div>
              <div className={styles.statCount}>{mtdCount} bet{mtdCount === 1 ? '' : 's'}</div>
            </div>
          </div>

          <div className={styles.subline}>
            <span><b>{winrate}</b> win</span>
            {roi !== null && <span><b>{roi}</b> ROI on {money(turnover)}</span>}
            <span>last <b>{bets.length ? signed(bets[bets.length - 1].amount) : '—'}</b></span>
            <Link className={styles.chip} to="/pnl">plain</Link>
            {bets.length > 0 && (
              <>
                <a className={styles.chip} href="/api/pnl/bets.csv" download>csv</a>
                <button className={styles.chip} onClick={undoLast} disabled={busy}>undo</button>
              </>
            )}
          </div>
        </div>

        {error && <p className={styles.error}>{error}</p>}

        <div className={styles.chartFrame}>
          <canvas
            ref={chartRef}
            className={styles.chart}
            aria-label={`Cumulative profit over ${bets.length} bets, currently ${signed(total)}`}
          />
        </div>
      </section>

      <SpSeries bets={bets} />
      <BattleEntry onAdd={handleAdd} busy={busy} />
    </div>
  )
}

function niceStep(range) {
  const raw = range / 4
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)))
  const n = raw / mag
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag
}

/** Price taken vs Betfair SP — measured bets only, same rule as the plain page. */
function SpSeries({ bets }) {
  const measured = bets.filter(b => b.price_taken != null && b.bfsp != null)
  if (!measured.length) return null

  const pcts = measured.map(b => vsSpPct(b.price_taken, b.bfsp))
  // Index from map, NOT indexOf — two bets can share a percentage, and indexOf
  // would divide both by the first one's position.
  let sum = 0
  const running = pcts.map((p, i) => (sum += p) / (i + 1))
  const mean = running[running.length - 1]
  const beat = pcts.filter(p => p > 0).length
  const coverage = Math.round(100 * measured.length / bets.length)

  const W = 100, H = 24
  const lo = Math.min(0, ...running), hi = Math.max(0, ...running)
  const span = (hi - lo) || 1
  const X = i => (running.length === 1 ? W / 2 : (W * i) / (running.length - 1))
  const Y = v => H - ((v - lo) / span) * H
  const path = running.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ')

  return (
    <section className={styles.spBlock}>
      <div className={styles.eyebrow}>
        <span>Price vs BFSP</span>
        <span className={styles.me}>{measured.length}/{bets.length} · {coverage}%</span>
      </div>
      <div className={styles.panel}>
        <div className={styles.spRow}>
          <div className={styles.spFigures}>
            <div className={`${styles.spMean} ${mean < 0 ? styles.neg : styles.pos}`}>{pct(mean)}</div>
            <div className={styles.spSub}>beat SP on <b>{beat}</b>/{measured.length}</div>
          </div>
          <svg
            className={styles.spSpark}
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            aria-label={`Running mean price versus starting price, currently ${mean.toFixed(1)} percent`}
          >
            <line x1="0" x2={W} y1={Y(0)} y2={Y(0)} stroke="rgba(255,255,255,.35)" strokeWidth="0.6" strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
            <path d={path} fill="none" stroke={mean < 0 ? '#ff9d8b' : '#8ef0b4'} strokeWidth="1.8" strokeLinejoin="miter" vectorEffect="non-scaling-stroke" />
          </svg>
        </div>
      </div>
    </section>
  )
}

/** Same flow as the plain tracker: stake, odds, optional BFSP, lose/push/win. */
function BattleEntry({ onAdd, busy }) {
  const [stake, setStake] = useState('')
  const [odds, setOdds] = useState(DEFAULT_PRICE)
  const [bsp, setBsp] = useState(null)
  const [result, setResult] = useState(null)
  const [confirming, setConfirming] = useState(false)

  const onOddsChange = useCallback(v => { setConfirming(false); setOdds(v) }, [])
  const onBspChange = useCallback(v => { setConfirming(false); setBsp(v) }, [])

  const stakeV = parseFloat(stake)
  const hasStake = stake !== '' && stakeV > 0
  const returned =
    result === 'win' ? stakeV * odds
    : result === 'lose' ? 0
    : result === 'push' ? stakeV : null

  const canAdd = !busy && hasStake && result !== null
  const net = canAdd ? Math.round((returned - stakeV) * 100) / 100 : null
  const big = net !== null && Math.abs(net) >= BIG_ENTRY

  function submit() {
    if (!canAdd) return
    if (big && !confirming) { setConfirming(true); return }
    onAdd({
      stake: Math.round(stakeV * 100) / 100,
      returned: Math.round(returned * 100) / 100,
      price_taken: odds,
      ...(bsp != null ? { bfsp: bsp } : {}),
    })
    setStake(''); setOdds(DEFAULT_PRICE); setBsp(null); setResult(null); setConfirming(false)
  }

  return (
    <section className={styles.pad}>
      <div className={styles.frame}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>Stake</span>
          <span className={styles.slotBox}>
            £
            <input
              value={stake}
              onChange={e => {
                setConfirming(false)
                setStake(e.target.value.replace(/[^\d.]/g, '').replace(/^0+(?=\d)/, '').slice(0, 9))
              }}
              onKeyDown={e => { if (e.key === 'Enter') submit() }}
              inputMode="decimal"
              type="text"
              placeholder="0.00"
              aria-label="Stake"
              autoComplete="off"
            />
          </span>
        </label>

        <div className={styles.wheels}>
          <OddsWheel id="gbOdds" label="Odds" value={odds} onChange={onOddsChange} />
          <div className={styles.wheelCol}>
            <OddsWheel id="gbBsp" label="BFSP" value={bsp} onChange={onBspChange} />
            <button
              type="button"
              className={styles.wNote}
              onClick={() => { setConfirming(false); setBsp(bsp == null ? DEFAULT_PRICE : null) }}
            >{bsp == null ? 'optional · tap to set' : 'clear'}</button>
          </div>
        </div>

        {bsp != null && (
          <div className={styles.vsSpLine}>
            vs SP <b className={odds > bsp ? styles.pos : odds < bsp ? styles.neg : ''}>
              {pct(vsSpPct(odds, bsp))}
            </b>
          </div>
        )}

        <div className={styles.menu} role="group" aria-label="Result">
          {[['lose', 'Lose'], ['push', 'Push'], ['win', 'Win']].map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`${styles.opt} ${styles[key]}`}
              aria-pressed={result === key}
              onClick={() => { setConfirming(false); setResult(key) }}
            >{label}</button>
          ))}
        </div>

        <div className={styles.netLine}>
          <span>Net</span>
          <b className={net === null ? '' : net > 0 ? styles.pos : net < 0 ? styles.neg : ''}>
            {net === null ? '—' : exact(net)}
          </b>
        </div>

        {confirming ? (
          <div className={styles.confirmBox} role="alertdialog" aria-live="assertive">
            <p className={styles.confirmQ}>
              Record <b className={net > 0 ? styles.pos : styles.neg}>{exact(net)}</b>?
            </p>
            <div className={styles.confirmBtns}>
              <button type="button" className={styles.cancel} onClick={() => setConfirming(false)}>Cancel</button>
              <button type="button" className={styles.confirm} onClick={submit} disabled={!canAdd}>Yes</button>
            </div>
          </div>
        ) : (
          <button type="button" className={styles.confirm} onClick={submit} disabled={!canAdd}>
            {busy ? 'Saving…' : big ? `Enter ${exact(net)}…` : 'Enter'}
          </button>
        )}
      </div>
    </section>
  )
}
