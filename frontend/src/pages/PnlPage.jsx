import { useState, useEffect, useRef, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { getBets, addBet, deleteBet } from '../api'
import OddsWheel, { DEFAULT_PRICE } from '../components/OddsWheel'
import styles from './PnlPage.module.css'

/**
 * Magnitude for the headline figures: exact pounds and pence below £1,000,
 * abbreviated above it (£1,250 → "£1.3k"). Four- and five-figure sums are what
 * the total, the chart axis and the tooltip have to fit, and their pennies
 * carry no information at that size. Six figures drop the decimal too — "£123k"
 * rather than "£123.4k".
 */
function magnitude(abs) {
  if (abs < 1000) return abs.toLocaleString('en-GB', { maximumFractionDigits: 2 })
  const k = abs / 1000
  // Test the ROUNDED value, not the raw one: £99,999 rounds to 100.0k, which
  // belongs in the no-decimal bracket alongside £100,000 rather than printing
  // as "£100.0k" one pound below it.
  const oneDp = Math.round(k * 10) / 10
  return oneDp >= 100 ? `${Math.round(k)}k` : `${oneDp.toFixed(1)}k`
}

const money = v => (v < 0 ? '-' : '') + '£' + magnitude(Math.abs(v))
const signed = v => (v > 0 ? '+' : v < 0 ? '−' : '') + '£' + magnitude(Math.abs(v))

// Exact to the penny, for the places where a figure is being CONFIRMED rather
// than read. The four-figure guard exists to catch a slipped decimal point, so
// abbreviating there would hide the very digits it asks you to check.
const exact = v =>
  (v < 0 ? '-' : '') + '£' + Math.abs(v).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const signedExact = v =>
  (v > 0 ? '+' : v < 0 ? '−' : '') + '£' + Math.abs(v).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const parseDate = s => new Date(/[Z+]/.test(s) ? s : s + 'Z')
const fmtDay = d => d.getDate() + ' ' + d.toLocaleString('en-GB', { month: 'short' })

// Price taken against Betfair SP, as a percentage of the SP. +5% means the
// price taken was 5% longer than the SP that bet eventually returned.
const vsSpPct = (price, sp) => (price / sp - 1) * 100
const pctSigned = p => (p > 0 ? '+' : p < 0 ? '−' : '') + Math.abs(p).toFixed(1) + '%'
const vsSp = (price, sp) => pctSigned(vsSpPct(price, sp))

/**
 * The price-vs-BFSP running mean, anchored to the bet it was measured ON.
 *
 * Returns [cumIndex, meanSoFar] pairs, where cumIndex is the bet's position in
 * the FULL ledger (+1, since index 0 is the pre-first-bet start point) — not its
 * position among priced bets. That distinction is the whole point: indexing by
 * measured order, as the old separate sparkline did, drew the first priced bet
 * at the far left of the chart however late in the ledger it actually happened.
 */
function spSeries(bets) {
  const out = []
  let sum = 0, n = 0
  bets.forEach((b, i) => {
    if (b.price_taken == null || b.bfsp == null) return
    sum += vsSpPct(b.price_taken, b.bfsp); n += 1
    out.push([i + 1, sum / n])
  })
  return out
}

function niceStep(range) {
  const raw = range / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / mag
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag
}

export default function PnlPage({ category = 'dogs' }) {
  const [bets, setBets] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const chartRef = useRef(null)
  const tipRef = useRef(null)

  // Which of the two ledgers this instance is, and the other one to hop to.
  const label = category === 'virtuals' ? 'Virtuals' : 'Dogs'
  const other = category === 'virtuals' ? 'dogs' : 'virtuals'
  const otherLabel = category === 'virtuals' ? 'Dogs' : 'Virtuals'

  // Re-fetch when the ledger changes. Each route mounts a fresh page with a
  // fixed category, but keeping it in the deps is correct and future-proof.
  useEffect(() => { getBets(category).then(setBets).catch(e => setError(e.message)) }, [category])

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

  // Price vs BFSP — the figures behind the dashed line on the chart. Only bets
  // carrying BOTH prices count; it measures the price, not the money, so a bet
  // can lose and still have beaten SP.
  const measured = bets.filter(b => b.price_taken != null && b.bfsp != null)
  const spMean = measured.length
    ? measured.reduce((s, b) => s + vsSpPct(b.price_taken, b.bfsp), 0) / measured.length
    : null
  const spBeat = measured.filter(b => vsSpPct(b.price_taken, b.bfsp) > 0).length

  const renderChart = useCallback(() => {
    const svg = chartRef.current
    if (!svg) return
    const r = svg.getBoundingClientRect(), W = r.width, H = r.height
    if (!W || !H) return
    const spPts = spSeries(bets)
    const hasSp = spPts.length > 0
    // The % axis needs a left gutter; without a BFSP series there's nothing to
    // label, so the money curve keeps the full width it had.
    const pad = { t: 16, r: 46, b: 30, l: hasSp ? 36 : 8 }
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

    // ── price vs BFSP, overlaid on its own % scale ───────────────────────────
    // A second scale on one chart, so the two are read together: the money
    // curve answers "am I up", this answers "am I taking the right prices".
    let spLayer = ''
    const spAt = {}
    if (hasSp) {
      const vals = spPts.map(p => p[1])
      let slo = Math.min(0, ...vals), shi = Math.max(0, ...vals)
      // The SAME 14% breathing room the money scale gets. The old sparkline had
      // none, so whichever point was the extreme sat flush on the frame — which
      // is what made the line look like it began at the floor.
      const ssp = (shi - slo) || 10
      slo -= ssp * 0.14; shi += ssp * 0.14
      const SY = v => pad.t + (H - pad.t - pad.b) * (1 - (v - slo) / (shi - slo))
      const sstep = niceStep(shi - slo)
      const dp = sstep < 1 ? 1 : 0
      for (let g = Math.ceil(slo / sstep) * sstep; g <= shi; g += sstep) {
        spLayer += `<text x="${(pad.l - 6).toFixed(1)}" y="${SY(g).toFixed(1)}" dy="3.5" text-anchor="end" font-size="9" font-family="var(--font-mono)" fill="var(--amber)" opacity="0.8">${(g > 0 ? '+' : '') + g.toFixed(dp)}%</text>`
      }
      // One measured bet is a dot, not a line — a single reading is not a trend,
      // and drawing it as one is exactly the false start this replaces.
      const d = spPts.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + SY(p[1]).toFixed(1)).join(' ')
      const last = spPts[spPts.length - 1]
      spLayer +=
        `<path d="${d}" fill="none" stroke="var(--amber)" stroke-width="1.6" stroke-dasharray="4 3" stroke-linejoin="round" stroke-linecap="round"/>` +
        `<circle cx="${X(last[0]).toFixed(1)}" cy="${SY(last[1]).toFixed(1)}" r="3" fill="var(--amber)" stroke="var(--paper)" stroke-width="1.5"/>`
      for (const [i, v] of spPts) spAt[i] = v
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
      ${spLayer}
      <g class="pnlHover" style="opacity:0"><circle class="pnlHdot" r="4" fill="var(--ink)" stroke="var(--paper)" stroke-width="1.5"/></g>`
    svg._pts = pts; svg._cum = cumArr; svg._dates = dates; svg._spAt = spAt
  }, [bets])

  useEffect(() => { renderChart() }, [renderChart])

  // Redrawing the chart re-parses its whole SVG (innerHTML), so it must not run
  // on every resize event: on a phone, scrolling shows/hides the URL bar and
  // fires a storm of them. Coalesce to one animation frame and redraw only when
  // the box genuinely changed size — a URL-bar resize that leaves the chart the
  // same width now costs nothing.
  useEffect(() => {
    let raf = 0
    let lastW = 0, lastH = 0
    const onResize = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const svg = chartRef.current
        if (!svg) return
        const r = svg.getBoundingClientRect()
        if (Math.abs(r.width - lastW) < 1 && Math.abs(r.height - lastH) < 1) return
        lastW = r.width; lastH = r.height
        renderChart()
      })
    }
    window.addEventListener('resize', onResize)
    return () => { cancelAnimationFrame(raf); window.removeEventListener('resize', onResize) }
  }, [renderChart])

  // hover / tap readout — attached once, reads live data off the svg element.
  // The per-move work is deliberately minimal: the bounding rect and the two
  // hover nodes are looked up once and cached (a getBoundingClientRect on every
  // pointermove forces a layout, which is what made dragging the chart stutter),
  // and the DOM writes are coalesced into one animation frame.
  useEffect(() => {
    const svg = chartRef.current, tip = tipRef.current
    if (!svg) return
    let rect = null, hv = null, hd = null, raf = 0, pending = 0

    const invalidate = () => { rect = null }

    const paint = () => {
      raf = 0
      const pts = svg._pts, cumA = svg._cum, dates = svg._dates
      if (!pts) return
      const px = pending
      let bi = 0, bd = 1e9
      for (let i = 0; i < pts.length; i++) {
        const d = Math.abs(pts[i][0] - px)
        if (d < bd) { bd = d; bi = i }
      }
      // A redraw replaces the SVG's innerHTML, so a cached node can be detached
      // — re-query when that happens rather than writing into a dead element.
      if (!hv || !hv.isConnected) {
        hv = svg.querySelector('.pnlHover'); hd = svg.querySelector('.pnlHdot')
      }
      if (hv && hd) { hv.style.opacity = 1; hd.setAttribute('cx', pts[bi][0]); hd.setAttribute('cy', pts[bi][1]) }
      tip.style.opacity = 1; tip.style.left = pts[bi][0] + 'px'; tip.style.top = pts[bi][1] + 'px'
      // The SP mean only exists on bets that carried both prices, so the tooltip
      // gains a second figure exactly where the dashed line has a point.
      const spv = svg._spAt ? svg._spAt[bi] : undefined
      tip.textContent =
        (bi === 0 ? 'Start' : fmtDay(dates[bi])) + '  ' + signed(cumA[bi]) +
        (spv === undefined ? '' : '  ·  ' + pctSigned(spv) + ' SP')
    }

    const move = e => {
      if (!rect) rect = svg.getBoundingClientRect()
      pending = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left
      if (!raf) raf = requestAnimationFrame(paint)
    }
    const leave = () => {
      cancelAnimationFrame(raf); raf = 0
      tip.style.opacity = 0
      if (!hv || !hv.isConnected) hv = svg.querySelector('.pnlHover')
      if (hv) hv.style.opacity = 0
    }

    svg.addEventListener('pointermove', move)
    svg.addEventListener('pointerleave', leave)
    window.addEventListener('resize', invalidate)
    window.addEventListener('scroll', invalidate, true)
    return () => {
      cancelAnimationFrame(raf)
      svg.removeEventListener('pointermove', move)
      svg.removeEventListener('pointerleave', leave)
      window.removeEventListener('resize', invalidate)
      window.removeEventListener('scroll', invalidate, true)
    }
  }, [])

  // Only called when a bet is actually submitted — so the chart re-renders once
  // per bet, never per keystroke (the entry lives in its own component below).
  async function handleAdd(payload) {
    if (!payload || busy) return
    setBusy(true); setError(null)
    try {
      const bet = await addBet(category, payload)
      setBets(bs => [...bs, bet])
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function undoLast() {
    if (!bets.length || busy) return
    const last = bets[bets.length - 1]
    setBusy(true); setError(null)
    try {
      await deleteBet(category, last.id)
      setBets(bs => bs.slice(0, -1))
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className={styles.page}>
      <section className={styles.top}>
        <div className={styles.eyebrow}>
          <span>Total profit · {label}</span><span className={styles.me}>Private</span>
        </div>
        <div className={styles.stats}>
          <div className={styles.stat}>
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
          {/* Doubles as the chart's legend — the dash matches the overlaid line. */}
          {spMean !== null && (
            <span className={styles.spTag}>
              <b>{pctSigned(spMean)}</b> SP · {spBeat}/{measured.length}
            </span>
          )}
          <span>last <b>{bets.length ? signed(bets[bets.length - 1].amount) : '—'}</b></span>
          {/* Hop to the other ledger, and to the alternate skin of this one. */}
          <Link className={styles.battle} to={`/pnl/${other}`}>{otherLabel}</Link>
          <Link className={styles.battle} to={`/pnl/${category}/battle`}>battle</Link>
          {bets.length > 0 && (
            <>
              {/* A plain link, not a fetch: the session cookie rides along and
                  the browser handles the download, so there is no blob to build
                  or object URL to revoke. */}
              <a className={styles.export} href={`/api/pnl/${category}/bets.csv`} download>csv</a>
              <button className={styles.undo} onClick={undoLast} disabled={busy}>undo last</button>
            </>
          )}
        </div>
        {error && <p className={styles.error}>{error}</p>}
        <div className={styles.chartWrap}>
          <svg className={styles.chart} ref={chartRef} preserveAspectRatio="none" aria-label="Cumulative profit and loss over time" />
          <div className={styles.tip} ref={tipRef} />
        </div>
      </section>

      <PnlEntry onAdd={handleAdd} busy={busy} />
    </div>
  )
}

// A net swing at or beyond this asks for confirmation before it's written.
const BIG_ENTRY = 1000

// Digits plus a single dot, max 2 decimals, capped length.
function sanitizeAmount(v) {
  v = v.replace(/[^\d.]/g, '')
  // Drop leading zeros so typing over a default "0" reads "50", not "050".
  // Keeps a lone "0" and the "0" in "0.50" (the lookahead needs a digit next).
  v = v.replace(/^0+(?=\d)/, '')
  const parts = v.split('.')
  if (parts.length > 2) v = parts[0] + '.' + parts.slice(1).join('')
  const dot = v.indexOf('.')
  if (dot !== -1) v = v.slice(0, dot + 1) + v.slice(dot + 1).replace(/\./g, '').slice(0, 2)
  return v.slice(0, 9)
}

// Own component with local state: typing here re-renders only this small tree,
// never the chart above — which is what caused the input lag.
function PnlEntry({ onAdd, busy }) {
  const [stake, setStake] = useState('')
  // The odds wheel rests on 2/1, but that seat is NOT a price the user chose.
  // `oddsTouched` separates the two: until the wheel is actually spun nothing is
  // filed, because recording the untouched default as a real price produced
  // vs-SP readings measured against odds that were never taken.
  const [odds, setOdds] = useState(DEFAULT_PRICE)
  const [oddsTouched, setOddsTouched] = useState(false)
  // The BFSP wheel TRACKS the odds wheel from the start — it always shows the
  // price it would record — but does not record anything until it is activated:
  //   !spOn                 TRACKING  ghosted, follows the odds, files nothing
  //   spOn && bsp == null   MATCHED   tap to record, keeps following the odds
  //   spOn && bsp != null   SET       spun to its own price, files that
  // Tracking is the default because the BFSP is only knowable after the off, so
  // a bet filed before checking must not assert a measurement never made. One
  // tap covers the common "SP came back at my price" case with no spinning.
  const [bsp, setBsp] = useState(null)
  const [spOn, setSpOn] = useState(false)
  // Losses are the common outcome, so the wheel starts on 'lose' — a losing bet
  // is then just stake + Add, no result tap. Any edit still re-reads for a big
  // swing, and win/push are one tap away.
  const [result, setResult] = useState('lose')   // 'win' | 'lose' | 'push'
  const [confirming, setConfirming] = useState(false)

  // Any edit invalidates a pending confirmation — you must re-read the figure
  // you're agreeing to, or a changed entry could ride through on a stale yes.
  const edit = setter => v => { setConfirming(false); setter(v) }

  // The wheels are memoised, so their onChange must keep a stable identity —
  // an inline `edit(setOdds)` is a new function every render and would re-render
  // both wheels' 130 rows on each keystroke, which is the lag this removes.
  const onOddsChange = useCallback(v => { setConfirming(false); setOdds(v) }, [])
  const onOddsTouch = useCallback(() => { setOddsTouched(true) }, [])
  // Spinning the wheel is itself an assertion that you know the price, so it
  // activates on its own rather than needing the tap first.
  const onBspChange = useCallback(v => { setConfirming(false); setBsp(v); setSpOn(true) }, [])
  const onBspActivate = useCallback(() => { setConfirming(false); setSpOn(true) }, [])

  // What the BFSP wheel shows — the odds taken until spun — is the same whether
  // it is tracking or recording. Only `spOn` decides whether it is FILED, and
  // the ghost styling is what tells the two apart on screen.
  const bspShown = bsp ?? odds

  // The label always names what a tap will do next.
  const spNote = !spOn ? 'tracking odds · tap to record'
    : bsp == null ? 'recording · tap to stop'
    : 'set · tap to match odds'
  function cycleSp() {
    setConfirming(false)
    if (!spOn) setSpOn(true)          // tracking  → record the tracked price
    else if (bsp == null) setSpOn(false)  // matched   → back to tracking
    else setBsp(null)                     // set       → back to matching
  }

  const stakeV = parseFloat(stake)
  const hasStake = stake !== '' && stakeV > 0

  // The return is DERIVED from stake, odds and result — no second figure to
  // type: win → stake × odds, lose → 0, push → stake back.
  const returned =
    result === 'win' ? stakeV * odds
    : result === 'lose' ? 0
    : result === 'push' ? stakeV
    : null

  // Odds always carries a value, so a bet needs only a stake and a result.
  const canAdd = !busy && hasStake && result !== null
  const net = canAdd ? Math.round((returned - stakeV) * 100) / 100 : null
  const big = net !== null && Math.abs(net) >= BIG_ENTRY

  function submit() {
    if (!canAdd) return
    // A four-figure swing is almost always a slipped decimal on a phone keypad,
    // and the ledger has no edit — only undo-last. Make it deliberate.
    if (big && !confirming) { setConfirming(true); return }
    // An untouched odds wheel files NEITHER price: its 2/1 seat is a resting
    // position, not a price taken, and a BFSP "matching" it would inherit the
    // same fiction. A spun BFSP still files on its own — that is a real reading
    // the user entered, and the API takes either price independently.
    const price = oddsTouched ? odds : null
    const sp = !spOn ? null : (bsp != null ? bsp : price)
    onAdd({
      stake: Math.round(stakeV * 100) / 100,
      returned: Math.round(returned * 100) / 100,
      ...(price != null ? { price_taken: price } : {}),
      ...(sp != null ? { bfsp: sp } : {}),
    })
    setStake(''); setOdds(DEFAULT_PRICE); setOddsTouched(false)
    setBsp(null); setSpOn(false); setResult('lose'); setConfirming(false)
  }

  const onEnterKey = e => { if (e.key === 'Enter') submit() }

  return (
    <section className={styles.pad}>
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
            aria-label="Stake"
            autoComplete="off"
          />
        </div>
      </label>

      <div className={styles.wheels}>
        <div className={styles.wheelCol}>
          <OddsWheel
            id="pnlOdds" label="Odds" value={odds}
            onChange={onOddsChange} onTouch={onOddsTouch}
          />
        </div>
        <div className={styles.wheelCol}>
          {/* Tracks the odds taken from the start so the price is always in
              place; one tap on the wheel records it, which covers the common
              "SP came back at my price" case without a spin. Ghosted until
              then, because a tracked price is not a measured one. */}
          <OddsWheel
            id="pnlBsp" label="BFSP"
            value={spOn ? bspShown : null} seat={odds}
            ghost={!spOn} onActivate={onBspActivate}
            onChange={onBspChange}
          />
          <button type="button" className={styles.wNote} onClick={cycleSp}>{spNote}</button>
        </div>
      </div>

      {spOn && (
        <div className={styles.vsSpLine}>
          vs SP <b className={odds > bspShown ? styles.pos : odds < bspShown ? styles.neg : ''}>{vsSp(odds, bspShown)}</b>
        </div>
      )}

      <div className={styles.results} role="group" aria-label="Result">
        <button
          type="button"
          className={`${styles.res} ${styles.resLose} ${result === 'lose' ? styles.on : ''}`}
          onClick={() => edit(setResult)('lose')}
        >Lose</button>
        <button
          type="button"
          className={`${styles.res} ${styles.resPush} ${result === 'push' ? styles.on : ''}`}
          onClick={() => edit(setResult)('push')}
        >Push</button>
        <button
          type="button"
          className={`${styles.res} ${styles.resWin} ${result === 'win' ? styles.on : ''}`}
          onClick={() => edit(setResult)('win')}
        >Win</button>
      </div>

      <div className={styles.netLine}>
        <span>Net</span>
        <b className={net === null ? '' : net > 0 ? styles.pos : net < 0 ? styles.neg : ''}>
          {net === null ? '—' : signedExact(net)}
        </b>
      </div>

      {confirming ? (
        <div className={styles.confirm} role="alertdialog" aria-live="assertive">
          <p className={styles.confirmQ}>
            Record <b className={net > 0 ? styles.pos : styles.neg}>{signedExact(net)}</b>?
            <span className={styles.confirmSub}>
              {exact(stakeV)} at {odds.toFixed(2)} · {result}
            </span>
          </p>
          <div className={styles.confirmBtns}>
            <button type="button" className={styles.cancel} onClick={() => setConfirming(false)}>Cancel</button>
            <button type="button" className={styles.enter} onClick={submit} disabled={!canAdd}>Yes, record it</button>
          </div>
        </div>
      ) : (
        <button type="button" className={styles.enter} onClick={submit} disabled={!canAdd}>
          {big ? `Enter ${signedExact(net)}…` : 'Enter'}
        </button>
      )}
    </section>
  )
}
