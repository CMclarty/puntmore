import { useState, useEffect, useRef, useCallback } from 'react'
import { getBets, addBet, deleteBet } from '../api'
import OddsWheel, { DEFAULT_PRICE } from '../components/OddsWheel'
import styles from './PnlPage.module.css'

const money = v => (v < 0 ? '-' : '') + '£' + Math.abs(v).toLocaleString('en-GB', { maximumFractionDigits: 2 })
const signed = v => (v > 0 ? '+' : v < 0 ? '−' : '') + '£' + Math.abs(v).toLocaleString('en-GB', { maximumFractionDigits: 2 })
const parseDate = s => new Date(/[Z+]/.test(s) ? s : s + 'Z')
const fmtDay = d => d.getDate() + ' ' + d.toLocaleString('en-GB', { month: 'short' })

// Price taken against Betfair SP, as a percentage of the SP. +5% means the
// price taken was 5% longer than the SP that bet eventually returned.
const vsSpPct = (price, sp) => (price / sp - 1) * 100
const vsSp = (price, sp) => {
  const p = vsSpPct(price, sp)
  return (p > 0 ? '+' : p < 0 ? '−' : '') + Math.abs(p).toFixed(1) + '%'
}

function niceStep(range) {
  const raw = range / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / mag
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag
}

export default function PnlPage() {
  const [bets, setBets] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const chartRef = useRef(null)
  const tipRef = useRef(null)

  useEffect(() => { getBets().then(setBets).catch(e => setError(e.message)) }, [])

  // Derived figures — cumulative total, month to date, win rate.
  const cum = [0]
  let t = 0
  for (const b of bets) { t += b.amount; cum.push(t) }
  const total = t
  const now = new Date()
  const mtdBets = bets.filter(b => {
    const d = parseDate(b.created_at)
    return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear()
  })
  const mtd = mtdBets.reduce((s, b) => s + b.amount, 0)
  const mtdCount = mtdBets.length
  // Pushes (stake returned exactly) are neither win nor loss, so they leave the
  // win rate alone rather than counting against it.
  const settled = bets.filter(b => b.amount !== 0)
  const wins = settled.filter(b => b.amount > 0).length
  const winrate = settled.length ? Math.round(100 * wins / settled.length) + '%' : '0%'

  // Turnover and ROI only exist for bets logged with a stake — a flat win/loss
  // entry has no stake, so it contributes to neither. Both are hidden until at
  // least one staked bet is on the ledger.
  const staked = bets.filter(b => b.stake != null)
  const turnover = staked.reduce((s, b) => s + b.stake, 0)
  const stakedProfit = staked.reduce((s, b) => s + b.amount, 0)
  const roi = turnover > 0 ? (stakedProfit / turnover * 100).toFixed(1) + '%' : null

  const renderChart = useCallback(() => {
    const svg = chartRef.current
    if (!svg) return
    const r = svg.getBoundingClientRect(), W = r.width, H = r.height
    if (!W || !H) return
    const pad = { t: 16, r: 46, b: 30, l: 8 }
    const cumArr = [0]; let acc = 0
    for (const b of bets) { acc += b.amount; cumArr.push(acc) }
    const n = cumArr.length
    const dates = [null, ...bets.map(b => parseDate(b.created_at))]
    let lo = Math.min(0, ...cumArr), hi = Math.max(0, ...cumArr)
    const sp = (hi - lo) || 100; lo -= sp * 0.14; hi += sp * 0.14
    const X = i => pad.l + (W - pad.l - pad.r) * (n <= 1 ? 0.5 : i / (n - 1))
    const Y = v => pad.t + (H - pad.t - pad.b) * (1 - (v - lo) / (hi - lo))
    const zeroY = Y(0)
    const pts = cumArr.map((v, i) => [X(i), Y(v)])
    const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ')
    const area = line + ` L${X(n - 1).toFixed(1)} ${zeroY.toFixed(1)} L${X(0).toFixed(1)} ${zeroY.toFixed(1)} Z`
    const axisY = (H - pad.b).toFixed(1)

    const step = niceStep(hi - lo); let grid = ''
    for (let g = Math.ceil(lo / step) * step; g <= hi; g += step) {
      const gy = Y(g).toFixed(1), z = Math.abs(g) < 1e-6
      grid += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${gy}" y2="${gy}" stroke="${z ? 'var(--line-strong)' : 'var(--line)'}" stroke-width="${z ? 1.5 : 1}" ${z ? '' : 'stroke-dasharray="2 3"'}/>`
      grid += `<text x="${W - pad.r + 5}" y="${gy}" dy="3.5" font-size="9.5" font-family="var(--font-mono)" fill="var(--faint)">${money(g)}</text>`
    }

    let xaxis = `<line x1="${pad.l}" x2="${W - pad.r}" y1="${axisY}" y2="${axisY}" stroke="var(--line-strong)" stroke-width="1"/>`
    const k = Math.min(4, n - 1), seen = new Set()
    for (let j = 0; j < k; j++) {
      const i = 1 + Math.round(j * (n - 2) / Math.max(1, k - 1))
      if (seen.has(i) || !dates[i]) continue; seen.add(i)
      const tx = X(i).toFixed(1)
      xaxis += `<line x1="${tx}" x2="${tx}" y1="${axisY}" y2="${(H - pad.b + 4).toFixed(1)}" stroke="var(--line-strong)" stroke-width="1"/>`
      const anchor = j === 0 ? 'start' : j === k - 1 ? 'end' : 'middle'
      xaxis += `<text x="${tx}" y="${(H - pad.b + 15).toFixed(1)}" text-anchor="${anchor}" font-size="9.5" font-family="var(--font-mono)" fill="var(--muted)">${fmtDay(dates[i])}</text>`
    }

    let months = ''
    for (let i = 2; i < n; i++) {
      if (dates[i].getMonth() !== dates[i - 1].getMonth()) {
        const bx = ((X(i - 1) + X(i)) / 2).toFixed(1)
        months += `<line x1="${bx}" x2="${bx}" y1="${pad.t}" y2="${axisY}" stroke="var(--line-strong)" stroke-width="1" stroke-dasharray="1 4"/>`
        months += `<text x="${(+bx + 4).toFixed(1)}" y="${pad.t + 9}" font-size="9" font-weight="700" letter-spacing="0.06em" fill="var(--faint)">${dates[i].toLocaleString('en-GB', { month: 'short' }).toUpperCase()}</text>`
      }
    }

    const cur = cumArr[n - 1], endCol = cur >= 0 ? 'var(--accent)' : 'var(--red)'
    svg.innerHTML = `
      <defs>
        <clipPath id="pnlAbove"><rect x="0" y="0" width="${W}" height="${zeroY}"/></clipPath>
        <clipPath id="pnlBelow"><rect x="0" y="${zeroY}" width="${W}" height="${Math.max(0, H - zeroY)}"/></clipPath>
      </defs>
      ${grid}${months}
      <path d="${area}" fill="var(--accent)" fill-opacity="0.14" clip-path="url(#pnlAbove)"/>
      <path d="${area}" fill="var(--red)" fill-opacity="0.13" clip-path="url(#pnlBelow)"/>
      <path d="${line}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      ${xaxis}
      <circle cx="${X(n - 1).toFixed(1)}" cy="${Y(cur).toFixed(1)}" r="4.5" fill="${endCol}" stroke="var(--paper)" stroke-width="2"/>
      <g class="pnlHover" style="opacity:0"><circle class="pnlHdot" r="4" fill="var(--ink)" stroke="var(--paper)" stroke-width="1.5"/></g>`
    svg._pts = pts; svg._cum = cumArr; svg._dates = dates
  }, [bets])

  useEffect(() => { renderChart() }, [renderChart])
  useEffect(() => {
    window.addEventListener('resize', renderChart)
    return () => window.removeEventListener('resize', renderChart)
  }, [renderChart])

  // hover / tap readout — attached once, reads live data off the svg element
  useEffect(() => {
    const svg = chartRef.current, tip = tipRef.current
    if (!svg) return
    const move = e => {
      const r = svg.getBoundingClientRect()
      const px = (e.touches ? e.touches[0].clientX : e.clientX) - r.left
      const pts = svg._pts, cumA = svg._cum, dates = svg._dates
      if (!pts) return
      let bi = 0, bd = 1e9
      pts.forEach((p, i) => { const d = Math.abs(p[0] - px); if (d < bd) { bd = d; bi = i } })
      const hv = svg.querySelector('.pnlHover'), hd = svg.querySelector('.pnlHdot')
      if (hv && hd) { hv.style.opacity = 1; hd.setAttribute('cx', pts[bi][0]); hd.setAttribute('cy', pts[bi][1]) }
      tip.style.opacity = 1; tip.style.left = pts[bi][0] + 'px'; tip.style.top = pts[bi][1] + 'px'
      tip.textContent = (bi === 0 ? 'Start' : fmtDay(dates[bi])) + '  ' + signed(cumA[bi])
    }
    const leave = () => { tip.style.opacity = 0; const hv = svg.querySelector('.pnlHover'); if (hv) hv.style.opacity = 0 }
    svg.addEventListener('pointermove', move)
    svg.addEventListener('pointerleave', leave)
    return () => { svg.removeEventListener('pointermove', move); svg.removeEventListener('pointerleave', leave) }
  }, [])

  // Only called when a bet is actually submitted — so the chart re-renders once
  // per bet, never per keystroke (the entry lives in its own component below).
  async function handleAdd(payload) {
    if (!payload || busy) return
    setBusy(true); setError(null)
    try {
      const bet = await addBet(payload)
      setBets(bs => [...bs, bet])
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function undoLast() {
    if (!bets.length || busy) return
    const last = bets[bets.length - 1]
    setBusy(true); setError(null)
    try {
      await deleteBet(last.id)
      setBets(bs => bs.slice(0, -1))
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className={styles.page}>
      <section className={styles.top}>
        <div className={styles.eyebrow}>
          <span>Cumulative P&amp;L</span><span className={styles.me}>Private</span>
        </div>
        <div className={styles.stats}>
          <div className={styles.stat}>
            <div className={styles.statLabel}>Total profit</div>
            <div className={`${styles.figure} ${styles.total} ${total < 0 ? styles.neg : styles.pos}`}>{signed(total)}</div>
            <div className={styles.statCount}>{bets.length} bet{bets.length === 1 ? '' : 's'}</div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statLabel}>Month to date</div>
            <div className={`${styles.figure} ${styles.mtd} ${mtd < 0 ? styles.neg : styles.pos}`}>{signed(mtd)}</div>
            <div className={styles.statCount}>{mtdCount} bet{mtdCount === 1 ? '' : 's'}</div>
          </div>
        </div>
        <div className={styles.subline}>
          <span><b>{winrate}</b> win</span>
          {roi !== null && <span><b>{roi}</b> ROI on {money(turnover)}</span>}
          <span>last <b>{bets.length ? signed(bets[bets.length - 1].amount) : '—'}</b></span>
          {bets.length > 0 && (
            <button className={styles.undo} onClick={undoLast} disabled={busy}>undo last</button>
          )}
        </div>
        {error && <p className={styles.error}>{error}</p>}
        <div className={styles.chartWrap}>
          <svg className={styles.chart} ref={chartRef} preserveAspectRatio="none" aria-label="Cumulative profit and loss over time" />
          <div className={styles.tip} ref={tipRef} />
        </div>
      </section>

      <SpSeries bets={bets} />
      <PnlEntry onAdd={handleAdd} busy={busy} />
    </div>
  )
}

/**
 * Price taken vs Betfair SP — its own series, deliberately kept out of the P&L
 * figures above. It measures the price, not the money: a bet can lose and still
 * have beaten SP. Only bets carrying BOTH prices appear here.
 *
 * The line is the running mean, so it reads as "where my average price stands
 * after n bets" rather than as per-bet noise.
 */
const SP_WINDOW_DAYS = 30   // "prior month" for the rolling estimate
const DAY_MS = 86400000

/**
 * Estimate the unpriced bets from the trailing SP_WINDOW_DAYS of PRICED ones.
 *
 * The window is drawn exclusively from measured bets. Feeding imputed values
 * back in would let the estimate drift on its own output until it converged on
 * whatever it started at — a fixed assumption wearing a moving average's
 * clothes, and the exact failure this is meant to avoid.
 *
 * A bet with no measured bets behind it in the window is left OUT, not filled
 * with zero: substituting a constant where there is no evidence is the thing
 * being replaced. Those are counted and reported as un-estimable.
 */
function estimateUnpriced(bets, measuredSet) {
  const withPct = bets.map(b => ({
    b,
    at: parseDate(b.created_at).getTime(),
    pct: measuredSet.has(b) ? vsSpPct(b.price_taken, b.bfsp) : null,
  }))

  let filled = 0, unestimable = 0
  const out = withPct.map(row => {
    if (row.pct !== null) return { ...row, estimated: false }
    const win = withPct.filter(o =>
      o.pct !== null && o.at < row.at && o.at >= row.at - SP_WINDOW_DAYS * DAY_MS
    )
    if (!win.length) { unestimable++; return null }
    filled++
    return {
      ...row,
      pct: win.reduce((s, o) => s + o.pct, 0) / win.length,
      estimated: true,
    }
  }).filter(Boolean)

  return { rows: out, filled, unestimable }
}

function SpSeries({ bets }) {
  const [estimating, setEstimating] = useState(false)

  const measured = bets.filter(b => b.price_taken != null && b.bfsp != null)
  if (!measured.length) return null

  const measuredSet = new Set(measured)
  const est = estimating ? estimateUnpriced(bets, measuredSet) : null

  // The figure on screen: measured-only by default, measured+estimated when
  // asked for. The measured count stays visible either way, so the estimated
  // number can never be mistaken for a bigger sample than it has.
  const pcts = est ? est.rows.map(r => r.pct) : measured.map(b => vsSpPct(b.price_taken, b.bfsp))
  const running = []
  let sum = 0
  pcts.forEach((p, i) => { sum += p; running.push(sum / (i + 1)) })
  const mean = running[running.length - 1]
  const beat = measured.filter(b => vsSpPct(b.price_taken, b.bfsp) > 0).length
  const coverage = Math.round(100 * measured.length / bets.length)

  const W = 100, H = 28
  const lo = Math.min(0, ...running), hi = Math.max(0, ...running)
  const span = (hi - lo) || 1
  const X = i => running.length === 1 ? W / 2 : (W * i) / (running.length - 1)
  const Y = v => H - ((v - lo) / span) * H
  const path = running.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(v).toFixed(1)}`).join(' ')
  const col = mean > 0 ? 'var(--accent)' : mean < 0 ? 'var(--red)' : 'var(--muted)'

  return (
    <section className={styles.spBlock}>
      <div className={styles.eyebrow}>
        <span>Price vs BFSP{estimating && <span className={styles.estTag}>est</span>}</span>
        <span className={styles.me}>{measured.length} of {bets.length} priced · {coverage}%</span>
      </div>
      <div className={styles.spRow}>
        <div className={styles.spFigures}>
          <div className={`${styles.spMean} ${mean < 0 ? styles.neg : styles.pos} ${estimating ? styles.estimated : ''}`}>
            {(mean > 0 ? '+' : mean < 0 ? '−' : '') + Math.abs(mean).toFixed(1) + '%'}
          </div>
          <div className={styles.spSub}>
            {estimating
              ? <>estimated · <b>{est.filled}</b> filled from prior {SP_WINDOW_DAYS}d
                  {est.unestimable > 0 && <> · {est.unestimable} unestimable</>}</>
              : <>mean vs SP · beat SP on <b>{beat}</b>/{measured.length}</>}
          </div>
        </div>
        <svg
          className={styles.spSpark}
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          aria-label={`Running mean price versus starting price, currently ${mean.toFixed(1)} percent${estimating ? ', including estimated bets' : ', measured bets only'}`}
        >
          <line x1="0" x2={W} y1={Y(0)} y2={Y(0)} stroke="var(--line-strong)" strokeWidth="0.6" strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
          {/* Dashed while estimating: the line is part inference, and should not
              draw like a measurement. */}
          <path
            d={path} fill="none" stroke={col} strokeWidth="1.6"
            strokeDasharray={estimating ? '3 2' : undefined}
            strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke"
          />
          <circle cx={X(running.length - 1)} cy={Y(mean)} r="2.4" fill={col} vectorEffect="non-scaling-stroke" />
        </svg>
      </div>
      <button
        type="button"
        className={styles.estToggle}
        onClick={() => setEstimating(e => !e)}
        aria-pressed={estimating}
      >
        {estimating ? 'show measured only' : `estimate unpriced from prior ${SP_WINDOW_DAYS}d`}
      </button>
    </section>
  )
}

// A net swing at or beyond this asks for confirmation before it's written.
const BIG_ENTRY = 1000

// Digits plus a single dot, max 2 decimals, capped length.
function sanitizeAmount(v) {
  v = v.replace(/[^\d.]/g, '')
  const parts = v.split('.')
  if (parts.length > 2) v = parts[0] + '.' + parts.slice(1).join('')
  const dot = v.indexOf('.')
  if (dot !== -1) v = v.slice(0, dot + 1) + v.slice(dot + 1).replace(/\./g, '').slice(0, 2)
  return v.slice(0, 9)
}

// Own component with local state: typing here re-renders only this small tree,
// never the chart above — which is what caused the input lag.
function PnlEntry({ onAdd, busy }) {
  const [mode, setMode] = useState('flat')
  const [amount, setAmount] = useState('')
  const [sign, setSign] = useState(1)
  const [stake, setStake] = useState('')
  const [ret, setRet] = useState('')
  const [confirming, setConfirming] = useState(false)
  // Odds are opt-in per bet: the wheels stay collapsed until asked for, so the
  // fast win/loss path is still two taps.
  const [showOdds, setShowOdds] = useState(false)
  const [price, setPrice] = useState(null)
  const [sp, setSp] = useState(null)

  // Any edit invalidates a pending confirmation — you must re-read the figure
  // you're agreeing to, or a changed amount could ride through on a stale yes.
  const edit = setter => v => { setConfirming(false); setter(v) }

  const value = parseFloat(amount)
  const stakeV = parseFloat(stake)
  const retV = parseFloat(ret)

  // Flat: any positive figure, signed by the win/loss toggle. Detailed: a
  // positive stake and a return (0 = lost the lot). A push nets zero and is
  // allowed here — it's a real settlement, unlike a zero flat entry.
  const canAdd = !busy && (mode === 'flat'
    ? amount !== '' && value > 0
    : stake !== '' && ret !== '' && stakeV > 0 && retV >= 0)

  const net = mode === 'flat'
    ? (canAdd ? sign * Math.round(value * 100) / 100 : null)
    : (canAdd ? Math.round((retV - stakeV) * 100) / 100 : null)

  const big = net !== null && Math.abs(net) >= BIG_ENTRY

  function submit() {
    if (!canAdd) return
    // A four-figure swing is almost always a slipped decimal point on a phone
    // keypad, and the ledger has no edit — only undo-last. Make it deliberate.
    if (big && !confirming) { setConfirming(true); return }
    // Odds ride along with whichever money mode is active; each is sent only if
    // set, so a price with no BFSP yet is a valid thing to file.
    const odds = {}
    if (price != null) odds.price_taken = price
    if (sp != null) odds.bfsp = sp
    if (mode === 'flat') {
      onAdd({ amount: sign * Math.round(value * 100) / 100, ...odds })
    } else {
      onAdd({
        stake: Math.round(stakeV * 100) / 100,
        returned: Math.round(retV * 100) / 100,
        ...odds,
      })
    }
    setAmount(''); setSign(1); setStake(''); setRet(''); setConfirming(false)
    setPrice(null); setSp(null); setShowOdds(false)
  }

  const onEnterKey = e => { if (e.key === 'Enter') submit() }

  return (
    <section className={styles.pad}>
      <div className={styles.modes} role="tablist" aria-label="Entry mode">
        <button
          type="button" role="tab" aria-selected={mode === 'flat'}
          className={mode === 'flat' ? styles.modeOn : ''}
          onClick={() => setMode('flat')}
        >Win / loss</button>
        <button
          type="button" role="tab" aria-selected={mode === 'detail'}
          className={mode === 'detail' ? styles.modeOn : ''}
          onClick={() => setMode('detail')}
        >Stake / return</button>
      </div>

      {mode === 'flat' ? (
        <>
          <div className={`${styles.display} ${sign > 0 ? styles.plus : styles.minus}`}>
            <span className={styles.dSign}>{sign > 0 ? '+' : '−'}</span>
            <span className={styles.currency}>£</span>
            <input
              className={styles.amountInput}
              value={amount}
              onChange={e => edit(setAmount)(sanitizeAmount(e.target.value))}
              onKeyDown={onEnterKey}
              inputMode="decimal"
              type="text"
              placeholder="0.00"
              aria-label="Bet result"
              autoComplete="off"
            />
          </div>
          <div className={styles.toggle}>
            <button
              type="button"
              className={`${styles.minusBtn} ${sign < 0 ? styles.on : ''}`}
              onClick={() => edit(setSign)(-1)}
            ><span>−</span><span className={styles.lbl}>Loss</span></button>
            <button
              type="button"
              className={`${styles.plusBtn} ${sign > 0 ? styles.on : ''}`}
              onClick={() => edit(setSign)(1)}
            ><span>+</span><span className={styles.lbl}>Win</span></button>
          </div>
        </>
      ) : (
        <>
          <div className={styles.pair}>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>Stake</span>
              <div className={styles.display}>
                <span className={styles.currency}>£</span>
                <input
                  className={styles.amountInput}
                  value={stake}
                  onChange={e => edit(setStake)(sanitizeAmount(e.target.value))}
                  onKeyDown={onEnterKey}
                  inputMode="decimal"
                  type="text"
                  placeholder="0.00"
                  autoComplete="off"
                />
              </div>
            </label>
            <label className={styles.field}>
              <span className={styles.fieldLabel}>Return</span>
              <div className={styles.display}>
                <span className={styles.currency}>£</span>
                <input
                  className={styles.amountInput}
                  value={ret}
                  onChange={e => edit(setRet)(sanitizeAmount(e.target.value))}
                  onKeyDown={onEnterKey}
                  inputMode="decimal"
                  type="text"
                  placeholder="0.00"
                  autoComplete="off"
                />
              </div>
            </label>
          </div>
          <div className={styles.netLine}>
            <span>Net</span>
            <b className={net === null ? '' : net > 0 ? styles.pos : net < 0 ? styles.neg : ''}>
              {net === null ? '—' : signed(net)}
            </b>
          </div>
        </>
      )}

      {showOdds ? (
        <div className={styles.odds}>
          <div className={styles.wheels}>
            <OddsWheel id="pnlPrice" label="Price taken" value={price} onChange={edit(setPrice)} />
            <OddsWheel id="pnlBfsp" label="BFSP" value={sp} onChange={edit(setSp)} />
          </div>
          <div className={styles.oddsFoot}>
            <span className={styles.vsSp}>
              {price != null && sp != null
                ? <>vs SP <b className={price > sp ? styles.pos : price < sp ? styles.neg : ''}>{vsSp(price, sp)}</b></>
                : 'Set both to score this bet against SP'}
            </span>
            <span className={styles.oddsHint}>fractional · stored as decimal</span>
            <button
              type="button"
              className={styles.oddsClear}
              onClick={() => { setPrice(null); setSp(null); setShowOdds(false) }}
            >clear</button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          className={styles.oddsAdd}
          // Opening the panel seats both wheels at the default, and that is the
          // value that gets filed — what you see is what is recorded, with no
          // hidden "untouched means nothing" state.
          onClick={() => { setPrice(DEFAULT_PRICE); setSp(DEFAULT_PRICE); setShowOdds(true) }}
        >
          + Price / BFSP
        </button>
      )}

      {confirming ? (
        <div className={styles.confirm} role="alertdialog" aria-live="assertive">
          <p className={styles.confirmQ}>
            Record <b className={net > 0 ? styles.pos : styles.neg}>{signed(net)}</b>?
            {mode === 'detail' && <span className={styles.confirmSub}>
              {money(stakeV)} staked, {money(retV)} returned
            </span>}
          </p>
          <div className={styles.confirmBtns}>
            <button type="button" className={styles.cancel} onClick={() => setConfirming(false)}>Cancel</button>
            <button type="button" className={styles.enter} onClick={submit} disabled={!canAdd}>Yes, record it</button>
          </div>
        </div>
      ) : (
        <button type="button" className={styles.enter} onClick={submit} disabled={!canAdd}>
          {big ? `Enter ${signed(net)}…` : 'Enter'}
        </button>
      )}
    </section>
  )
}
