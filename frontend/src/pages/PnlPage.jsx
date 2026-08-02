import { useState, useEffect, useRef, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { getBets, addBet, deleteBet } from '../api'
import OddsWheel, { DEFAULT_PRICE, TERMS_LADDER, DEFAULT_TERMS } from '../components/OddsWheel'
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
const fmtTime = d => d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })

/**
 * How to label a tick, given how much time the chart spans.
 *
 * Inside a day every tick falls on the same date, so a date label repeats
 * itself across the axis and says nothing — the 24-hour view read
 * "31 Jul · 31 Jul · 31 Jul · 31 Jul". Under a day and a half, label the clock
 * instead; above it, the date is what distinguishes one tick from the next.
 */
function tickFormatter(dates) {
  const real = dates.filter(Boolean)
  if (real.length < 2) return fmtDay
  const span = real[real.length - 1] - real[0]
  return span < 36 * 60 * 60 * 1000 ? fmtTime : fmtDay
}

// Price taken against Betfair SP, as a percentage of the SP. +5% means the
// price taken was 5% longer than the SP that bet eventually returned.
const vsSpPct = (price, sp) => (price / sp - 1) * 100
const pctSigned = p => (p > 0 ? '+' : p < 0 ? '−' : '') + Math.abs(p).toFixed(1) + '%'
const vsSp = (price, sp) => pctSigned(vsSpPct(price, sp))

/**
 * The expected value of the price taken, in POUNDS.
 *
 * Treating the Betfair SP as the fair price, the true win probability is 1/SP,
 * so a bet struck at P returns stake × (P/SP − 1) in expectation — positive when
 * you beat SP, negative when you didn't, and zero when you matched it. This is
 * the same quantity the old percentage showed, weighted by what was actually at
 * risk: +5% on a £2 bet and +5% on a £200 bet are not the same edge, and only
 * the pounds figure says so.
 *
 * Needs the stake, so flat entries (amount only) can't contribute.
 */
const evOf = b => b.stake * (b.price_taken / b.bfsp - 1)
const hasEv = b => b.price_taken != null && b.bfsp != null && b.stake != null

/**
 * Accumulated EV, anchored to the bet it was earned ON.
 *
 * Returns [cumIndex, totalSoFar] pairs, where cumIndex is the bet's position in
 * the FULL ledger (+1, since index 0 is the pre-first-bet start point) — not its
 * position among priced bets. That distinction is the whole point: indexing by
 * measured order, as the old separate sparkline did, drew the first priced bet
 * at the far left of the chart however late in the ledger it actually happened.
 */
function spSeries(bets) {
  const out = []
  let sum = 0
  bets.forEach((b, i) => {
    if (!hasEv(b)) return
    sum += evOf(b)
    out.push([i + 1, sum])
  })
  return out
}

function niceStep(range) {
  const raw = range / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / mag
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag
}

// The zoom chart's window, separate from the headline stats. A procession
// rather than a dropdown: one tap always means "zoom in one step", so there is
// nothing to open or choose from, just a value to cycle past. "All" is not a
// step in it — that chart is always on screen below.
//
// ROLLING windows, not calendar ones. A calendar month-to-date is empty on the
// 1st and thin on the 2nd, so the chart you look at while betting would go
// blank once a month through no fault of the ledger; "the last 30 days" always
// has the same amount of history in it. The MONTH TO DATE figure in the
// headline stays calendar — that one is an accounting period, not a view.
const ZOOM_RANGES = ['month', 'week', 'day']
const RANGE_WINDOW_MS = {
  month: 30 * 24 * 60 * 60 * 1000,
  week: 7 * 24 * 60 * 60 * 1000,
  day: 24 * 60 * 60 * 1000,
}
const RANGE_LABEL = { all: 'All time', month: '30 days', week: '7 days', day: '24 hours' }

// What separates a swipe from a scrub. Far enough to be deliberate, level
// enough not to be the page scrolling, quick enough to be a flick rather than
// someone reading values across the chart.
const SWIPE_MIN_PX = 45
const SWIPE_MAX_MS = 700

function inChartRange(b, range, now) {
  const window = RANGE_WINDOW_MS[range]
  if (!window) return true
  return parseDate(b.created_at).getTime() >= now.getTime() - window
}

/**
 * One cumulative P&L chart over a given window.
 *
 * Extracted so the panel can show TWO of them without a second copy of the
 * drawing code: a zoomable one on top and the all-time run below it. Each owns
 * its own svg, resize observer and hover readout — they share no state, so
 * scrubbing one leaves the other alone.
 *
 * `onCycle` makes the corner tag a button; without it the tag is a plain label
 * and the chart is fixed to its range.
 */
function PnlChart({ bets, range, label, onCycle, onSwipe }) {
  const chartRef = useRef(null)
  const tipRef = useRef(null)

  const renderChart = useCallback(() => {
    const svg = chartRef.current
    if (!svg) return
    const r = svg.getBoundingClientRect(), W = r.width, H = r.height
    if (!W || !H) return
    // Windowed to the chart's own range, not the ledger's: each step zooms the
    // SAME cumulative curve into a shorter tail, starting fresh from 0 at the
    // window's edge — so "Day" reads as today's shape, not a slice of the total.
    const rangedBets = range === 'all' ? bets : bets.filter(b => inChartRange(b, range, new Date()))
    const spPts = spSeries(rangedBets)
    const hasSp = spPts.length > 0
    const pad = { t: 16, r: 46, b: 30, l: 8 }
    const cumArr = [0]; let acc = 0
    for (const b of rangedBets) { acc += b.amount; cumArr.push(acc) }
    const n = cumArr.length
    const dates = [null, ...rangedBets.map(b => parseDate(b.created_at))]
    // EV shares the money curve's scale so the two can be read against each
    // other directly — the whole point of putting them together is perspective:
    // how much of the profit was earned in the price, and how much was variance.
    // Its range therefore has to sit inside the domain as well.
    const evVals = spPts.map(p => p[1])
    let lo = Math.min(0, ...cumArr, ...evVals), hi = Math.max(0, ...cumArr, ...evVals)
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

    const fmtTick = tickFormatter(dates)
    let xaxis = `<line x1="${pad.l}" x2="${W - pad.r}" y1="${axisY}" y2="${axisY}" stroke="var(--line-strong)" stroke-width="1"/>`
    const k = Math.min(4, n - 1), seen = new Set()
    for (let j = 0; j < k; j++) {
      const i = 1 + Math.round(j * (n - 2) / Math.max(1, k - 1))
      if (seen.has(i) || !dates[i]) continue; seen.add(i)
      const tx = X(i).toFixed(1)
      xaxis += `<line x1="${tx}" x2="${tx}" y1="${axisY}" y2="${(H - pad.b + 4).toFixed(1)}" stroke="var(--line-strong)" stroke-width="1"/>`
      const anchor = j === 0 ? 'start' : j === k - 1 ? 'end' : 'middle'
      xaxis += `<text x="${tx}" y="${(H - pad.b + 15).toFixed(1)}" text-anchor="${anchor}" font-size="9.5" font-family="var(--font-mono)" fill="var(--muted)">${fmtTick(dates[i])}</text>`
    }

    let months = ''
    for (let i = 2; i < n; i++) {
      if (dates[i].getMonth() !== dates[i - 1].getMonth()) {
        const bx = ((X(i - 1) + X(i)) / 2).toFixed(1)
        months += `<line x1="${bx}" x2="${bx}" y1="${pad.t}" y2="${axisY}" stroke="var(--line-strong)" stroke-width="1" stroke-dasharray="1 4"/>`
        months += `<text x="${(+bx + 4).toFixed(1)}" y="${pad.t + 9}" font-size="9" font-weight="700" letter-spacing="0.06em" fill="var(--faint)">${dates[i].toLocaleString('en-GB', { month: 'short' }).toUpperCase()}</text>`
      }
    }

    // ── EV, overlaid on its own £ scale ──────────────────────────────────────
    // Its OWN scale, not the money curve's, even though both are pounds: EV
    // accrues in pennies per bet while the P&L swings in tens, so sharing an
    // axis would flatten this line onto zero. Two scales, read together — the
    // money curve answers "am I up", this answers "am I taking the right
    // prices", and the difference between them is variance.
    let spLayer = ''
    const spAt = {}
    if (hasSp) {
      // Drawn on Y — the money scale — not a scale of its own. EV is usually a
      // small fraction of the swings beside it, and that flatness IS the
      // reading: it shows how little of the result the price accounts for.
      // One measured bet is a dot, not a line: a single reading is not a trend.
      const d = spPts.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' ')
      const last = spPts[spPts.length - 1]
      spLayer +=
        `<path d="${d}" fill="none" stroke="var(--amber)" stroke-width="1.6" stroke-dasharray="4 3" stroke-linejoin="round" stroke-linecap="round"/>` +
        `<circle cx="${X(last[0]).toFixed(1)}" cy="${Y(last[1]).toFixed(1)}" r="3" fill="var(--amber)" stroke="var(--paper)" stroke-width="1.5"/>`
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
    svg._fmt = fmtTick
  }, [bets, range])

  useEffect(() => { renderChart() }, [renderChart])

  // The SVG STRETCHES to fill its box via CSS, but its contents are drawn at
  // absolute coordinates taken from one getBoundingClientRect. So whenever the
  // box changes height without the window resizing, the drawing keeps the old
  // size and the chart stops reaching the entry pad — a gap that looks like a
  // layout bug but is a stale measurement. The entry pad changes height often:
  // the each-way toggle, the confirm box, an error line, a wrapped subline when
  // the SP figure appears. window.resize hears none of those.
  //
  // Observing the element itself catches every one, window resizes included.
  // Still coalesced to one frame and guarded on a real size change, because the
  // redraw re-parses the whole SVG and a phone's URL bar fires a storm of these.
  useEffect(() => {
    const svg = chartRef.current
    if (!svg) return
    let raf = 0, lastW = 0, lastH = 0
    const measure = () => {
      raf = 0
      const r = svg.getBoundingClientRect()
      if (Math.abs(r.width - lastW) < 1 && Math.abs(r.height - lastH) < 1) return
      lastW = r.width; lastH = r.height
      renderChart()
    }
    const ro = new ResizeObserver(() => { if (!raf) raf = requestAnimationFrame(measure) })
    ro.observe(svg)
    return () => { cancelAnimationFrame(raf); ro.disconnect() }
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
        (bi === 0 ? 'Start' : (svg._fmt || fmtDay)(dates[bi])) + '  ' + signed(cumA[bi]) +
        (spv === undefined ? '' : '  ·  ' + signed(spv) + ' EV')
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

  /**
   * Horizontal swipe changes the window: right widens it, left narrows it.
   *
   * It has to share the surface with the scrub above, which is why this reads
   * a whole gesture (down → up) rather than reacting to movement: a scrub is a
   * finger travelling across the chart to read values, and a swipe is the same
   * motion committed to. Only the release decides, so scrubbing still works
   * exactly as before — the tooltip follows the finger either way — and a
   * gesture only counts as a swipe if it went far enough, stayed level enough
   * to not be a page scroll, and finished quickly enough to be a flick.
   *
   * The tooltip is dismissed on a committed swipe: it is pointing at a bet in
   * the window being left behind.
   */
  useEffect(() => {
    const svg = chartRef.current
    if (!svg || !onSwipe) return
    let start = null

    const down = e => { start = { x: e.clientX, y: e.clientY, t: Date.now() } }
    const up = e => {
      if (!start) return
      const dx = e.clientX - start.x
      const dy = e.clientY - start.y
      const dt = Date.now() - start.t
      start = null
      if (Math.abs(dx) < SWIPE_MIN_PX) return          // a tap, or a scrub that went nowhere
      if (Math.abs(dy) > Math.abs(dx)) return          // vertical: the page is scrolling
      if (dt > SWIPE_MAX_MS) return                    // a slow read, not a flick
      if (tipRef.current) tipRef.current.style.opacity = 0
      onSwipe(dx > 0 ? 1 : -1)
    }

    svg.addEventListener('pointerdown', down)
    svg.addEventListener('pointerup', up)
    svg.addEventListener('pointercancel', () => { start = null })
    return () => {
      svg.removeEventListener('pointerdown', down)
      svg.removeEventListener('pointerup', up)
    }
  }, [onSwipe])

  return (
    <div className={styles.chartWrap}>
      <svg className={styles.chart} ref={chartRef} preserveAspectRatio="none"
           aria-label={`Cumulative profit and loss — ${label}`} />
      <div className={styles.tip} ref={tipRef} />
      {/* Sits in the chart's own empty top-left corner (inside the axis
          padding) rather than on the drag surface, so it can never eat the
          hover-scrub gesture the chart itself listens for. A separate
          element, not a second listener on the svg — no gesture to
          disambiguate from a drag. */}
      {onCycle ? (
        <button
          type="button"
          className={styles.rangeTag}
          onClick={onCycle}
          aria-label={`Chart range: ${label}. Tap to cycle.`}
        >
          {label}
        </button>
      ) : (
        <span className={`${styles.rangeTag} ${styles.rangeTagFixed}`}>{label}</span>
      )}
    </div>
  )
}

export default function PnlPage({ category = 'dogs' }) {
  const [bets, setBets] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  // The zoom chart's window — cycled by tapping the small tag in its corner,
  // never a dropdown. "All" is not in the cycle: the all-time run has its own
  // chart pinned below, so there is no reason to zoom out to what is already on
  // screen. Lives apart from the headline stats, which always read all-time /
  // month-to-date regardless of what the chart shows.
  const [zoom, setZoom] = useState('month')
  const cycleZoom = useCallback(() => {
    setZoom(r => ZOOM_RANGES[(ZOOM_RANGES.indexOf(r) + 1) % ZOOM_RANGES.length])
  }, [])

  /**
   * Swipe: right widens the window, left narrows it.
   *
   * The direction follows the time axis rather than a carousel — dragging the
   * chart to the right pulls older history into view, which is what a longer
   * window is. It CLAMPS at each end instead of wrapping: a swipe carries a
   * direction, and jumping from 24 hours to 30 days because you flicked once
   * more would read as the chart losing its place, where a tap that cycles has
   * no direction to contradict.
   */
  const stepZoom = useCallback(dir => {
    setZoom(r => {
      // ZOOM_RANGES runs longest → shortest, so a right swipe (dir > 0, "show
      // me further back") steps DOWN the array, not up.
      const i = ZOOM_RANGES.indexOf(r) - dir
      return ZOOM_RANGES[Math.min(Math.max(i, 0), ZOOM_RANGES.length - 1)]
    })
  }, [])

  // Which of the two ledgers this instance is, and the other one to hop to.
  const label = category === 'virtuals' ? 'Virtuals' : 'Dogs'
  const other = category === 'virtuals' ? 'dogs' : 'virtuals'
  const otherLabel = category === 'virtuals' ? 'Dogs' : 'Virtuals'

  // Re-fetch when the ledger changes. Each route mounts a fresh page with a
  // fixed category, but keeping it in the deps is correct and future-proof.
  useEffect(() => { getBets(category).then(setBets).catch(e => setError(e.message)) }, [category])

  // Derived figures — cumulative total, month to date, win rate.
  // Tips are real money out of the till, so they move every CASH figure (total,
  // month to date, the chart) exactly like a loss — but they are not bets, so
  // the bet count, win rate, turnover and EV all read `wagers`, never tips.
  const wagers = bets.filter(b => !b.is_tip)
  const cum = [0]
  let t = 0
  for (const b of bets) { t += b.amount; cum.push(t) }
  const total = t
  const now = new Date()
  const inMonth = b => {
    const d = parseDate(b.created_at)
    return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear()
  }
  const mtd = bets.filter(inMonth).reduce((s, b) => s + b.amount, 0)
  const mtdCount = wagers.filter(inMonth).length
  // Pushes (stake returned exactly) are neither win nor loss, so they leave the
  // win rate alone rather than counting against it. Tips never enter it at all.
  const settled = wagers.filter(b => b.amount !== 0)
  const wins = settled.filter(b => b.amount > 0).length
  const winrate = settled.length ? Math.round(100 * wins / settled.length) + '%' : '0%'

  // Turnover and ROI only exist for bets logged with a stake — a flat win/loss
  // entry has no stake, so it contributes to neither. Both are hidden until at
  // least one staked bet is on the ledger.
  const staked = bets.filter(b => b.stake != null)
  const turnover = staked.reduce((s, b) => s + b.stake, 0)
  const stakedProfit = staked.reduce((s, b) => s + b.amount, 0)
  const roi = turnover > 0 ? (stakedProfit / turnover * 100).toFixed(1) + '%' : null

  // EV — the figures behind the dashed line on the chart. It measures the PRICE,
  // not the result: a bet can lose and still have been +EV. The headline is the
  // accumulated figure, so it equals where the line ends rather than describing
  // it. Needs a stake, so flat entries sit this out.
  const measured = bets.filter(hasEv)
  const evTotal = measured.length ? measured.reduce((s, b) => s + evOf(b), 0) : null
  const spBeat = measured.filter(b => b.price_taken > b.bfsp).length


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
            <div className={styles.statCount}>{wagers.length} bet{wagers.length === 1 ? '' : 's'}</div>
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
          {evTotal !== null && (
            <span className={styles.spTag}>
              <b>{signed(evTotal)}</b> EV · {spBeat}/{measured.length}
            </span>
          )}
          <span>last <b>{wagers.length ? signed(wagers[wagers.length - 1].amount) : '—'}</b></span>
          {/* Actions ride in their own group so they anchor right as a unit and
              wrap together, rather than each fighting for the auto margin. */}
          <div className={styles.actions}>
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
        </div>
        {error && <p className={styles.error}>{error}</p>}
        {/* Two charts, near and far. The top one zooms — swipe right for a
            longer window, left for a shorter — and answers "how is it going
            right now"; the all-time run underneath is the context that question
            is asked against, and stays put so the shape of the whole ledger is
            always on screen. */}
        <div className={styles.charts}>
          <PnlChart
            bets={bets}
            range={zoom}
            label={RANGE_LABEL[zoom]}
            onCycle={cycleZoom}
            onSwipe={stepZoom}
          />
          <PnlChart bets={bets} range="all" label={RANGE_LABEL.all} />
        </div>
      </section>

      <PnlEntry onAdd={handleAdd} busy={busy} category={category} />
    </div>
  )
}

// A net swing at or beyond this asks for confirmation before it's written.
const BIG_ENTRY = 1000

// Staking is fixed on both ledgers, so the box comes pre-filled with the usual
// unit: virtuals £90 win-only and £80 a part each-way (which is the £160 total
// the pad shows), dogs a flat £100.
// The box always holds the UNIT — each-way doubles it into the outlay.
const DEFAULT_STAKE = '90'
const DEFAULT_STAKE_EW = '80'
const DEFAULT_STAKE_DOGS = '100'

/**
 * What a bet returns, for both ledgers' entry shapes.
 *
 * Straight (win-only): stake is the unit, and a win pays unit × odds.
 * Each-way: TWO bets on one line — the outlay is 2 × unit, the win part pays
 * unit × odds and the place part pays unit × (1 + (odds − 1) × terms). A "place"
 * result collects only the second; a "win" collects both. That is why the stake
 * doubles: forgetting it understates turnover and overstates ROI on every EW row.
 */
function settle({ unit, odds, result, ew, terms }) {
  const outlay = ew ? unit * 2 : unit
  const place = unit * (1 + (odds - 1) * terms)
  let returned
  if (!ew) {
    returned = result === 'win' ? unit * odds : result === 'push' ? unit : 0
  } else {
    returned = result === 'win' ? unit * odds + place : result === 'place' ? place : 0
  }
  return { outlay, returned }
}

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

/**
 * One outcome key: the word, and under it the exact figure that tapping it
 * will write to the ledger.
 *
 * The figure is the whole reason this is not just a labelled button. Removing
 * the Enter step removed the moment where you read the net before agreeing to
 * it, so the net moved onto the keys — you check the number on the thing you
 * are about to press, rather than on a line somewhere else in the pad.
 *
 * Exact to the penny, not abbreviated: this is a figure being checked before
 * it's committed, and abbreviating hides the digit a slipped decimal moves.
 */
function ResultKey({ kind, label, net, onClick, disabled }) {
  return (
    <button
      type="button"
      className={`${styles.res} ${styles[kind]}`}
      onClick={onClick}
      disabled={disabled}
    >
      <span className={styles.resLabel}>{label}</span>
      <span className={styles.resNet}>{net === null ? '—' : signedExact(net)}</span>
    </button>
  )
}

// Own component with local state: typing here re-renders only this small tree,
// never the chart above — which is what caused the input lag.
function PnlEntry({ onAdd, busy, category }) {
  // The two ledgers track different things about a bet. Dogs measures the price
  // against the Betfair SP; virtuals doesn't (there is no BFSP on a virtual
  // race) and instead records whether the bet was each-way, which changes both
  // the outlay and the set of results that can happen.
  const isVirtuals = category === 'virtuals'
  // Both ledgers staff a fixed stake, so the box starts pre-filled — virtuals
  // with its £90/£80 unit, dogs with £100. `stakeEdited` stops the each-way
  // toggle from overwriting a figure the user has typed — the pre-fill only
  // follows the toggle while it is still the default.
  const [stake, setStake] = useState(isVirtuals ? DEFAULT_STAKE : DEFAULT_STAKE_DOGS)
  const [stakeEdited, setStakeEdited] = useState(false)
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
  // Virtuals only: each-way and the place terms it pays at.
  const [ew, setEw] = useState(false)
  const [terms, setTerms] = useState(DEFAULT_TERMS)
  // THE RESULT BUTTON IS THE COMMIT. There is no Enter: a bet is stake (already
  // pre-filled) plus one tap on the outcome, which is the whole entry. Nothing
  // is "selected" here, so there is no result state to hold — the tapped result
  // is passed straight through to `record`.
  //
  // What that costs is the preview an Enter button bought: you could read the
  // net before agreeing to it. That is why each button now carries its own
  // figure — you read what the tap will file ON the thing you're about to tap,
  // which is a shorter path to the same check.
  //
  // `pending` is the four-figure guard's held result, not a selection: a swing
  // at or beyond BIG_ENTRY stops for a yes/no instead of filing on the tap.
  const [pending, setPending] = useState(null)   // 'win' | 'lose' | 'push' | 'place'
  // Staff tip (virtuals): its own amount, kept apart from the bet fields so a
  // tip can never pick up a stake, odds or result.
  const [tip, setTip] = useState('')

  // Every setter below clears `pending` for the same reason: an edit invalidates
  // a pending confirmation — you must re-read the figure you're agreeing to, or
  // a changed entry could ride through on a stale yes.

  // The pre-fill for the current mode, and the box's own edit handler — typing
  // marks the stake as touched so the toggle stops swapping it.
  const stakeDefault = isVirtuals ? (ew ? DEFAULT_STAKE_EW : DEFAULT_STAKE) : DEFAULT_STAKE_DOGS
  const onStakeChange = e => {
    setPending(null); setStakeEdited(true); setStake(sanitizeAmount(e.target.value))
  }

  // The wheels are memoised, so their onChange must keep a stable identity —
  // an inline `edit(setOdds)` is a new function every render and would re-render
  // both wheels' 130 rows on each keystroke, which is the lag this removes.
  const onOddsChange = useCallback(v => { setPending(null); setOdds(v) }, [])
  const onOddsTouch = useCallback(() => { setOddsTouched(true) }, [])
  // Spinning the wheel is itself an assertion that you know the price, so it
  // activates on its own rather than needing the tap first.
  const onBspChange = useCallback(v => { setPending(null); setBsp(v); setSpOn(true) }, [])
  const onBspActivate = useCallback(() => { setPending(null); setSpOn(true) }, [])
  const onTermsChange = useCallback(v => { setPending(null); setTerms(v) }, [])

  // What the BFSP wheel shows — the odds taken until spun — is the same whether
  // it is tracking or recording. Only `spOn` decides whether it is FILED, and
  // the ghost styling is what tells the two apart on screen.
  const bspShown = bsp ?? odds

  // The label always names what a tap will do next.
  const spNote = !spOn ? 'tracking odds · tap to record'
    : bsp == null ? 'recording · tap to stop'
    : 'set · tap to match odds'
  function cycleSp() {
    setPending(null)
    if (!spOn) setSpOn(true)          // tracking  → record the tracked price
    else if (bsp == null) setSpOn(false)  // matched   → back to tracking
    else setBsp(null)                     // set       → back to matching
  }

  const stakeV = parseFloat(stake)
  const hasStake = stake !== '' && stakeV > 0

  // Odds always carries a value and the stake is pre-filled, so the only thing
  // standing between a fresh pad and a filed bet is the outcome tap.
  const canAdd = !busy && hasStake
  // What actually leaves the till — the each-way line shows it, since a doubled
  // outlay is the easiest figure on this page to get wrong.
  const outlay = hasStake ? (isVirtuals && ew ? stakeV * 2 : stakeV) : null

  /**
   * What tapping `r` would file. Derived from stake, odds and the result — no
   * second figure to type. Each-way doubles the outlay and adds the place part
   * (see settle). Returns null when there's no stake to settle against.
   *
   * A function rather than a value now: with no Enter button there is no single
   * "current" result to compute, and every button needs its own figure.
   */
  function netFor(r) {
    if (!hasStake) return null
    const { outlay, returned } = settle({ unit: stakeV, odds, result: r, ew: isVirtuals && ew, terms })
    return Math.round((returned - outlay) * 100) / 100
  }

  const pendingNet = pending ? netFor(pending) : null

  /** The tap itself: file it, or stop for a yes on a four-figure swing. */
  function record(r) {
    if (!canAdd) return
    // A four-figure swing is almost always a slipped decimal on a phone keypad,
    // and the ledger has no edit — only undo-last. Make it deliberate. The
    // guard matters MORE now that the outcome tap files straight away: it is
    // the only thing between a mis-tap and a £2,000 line in the ledger.
    const net = netFor(r)
    if (Math.abs(net) >= BIG_ENTRY && pending !== r) { setPending(r); return }
    file(r)
  }

  function file(r) {
    const { outlay, returned } = settle({ unit: stakeV, odds, result: r, ew: isVirtuals && ew, terms })
    // An untouched odds wheel files NEITHER price: its 2/1 seat is a resting
    // position, not a price taken, and a BFSP "matching" it would inherit the
    // same fiction. A spun BFSP still files on its own — that is a real reading
    // the user entered, and the API takes either price independently.
    const price = oddsTouched ? odds : null
    const sp = isVirtuals || !spOn ? null : (bsp != null ? bsp : price)
    const isEw = isVirtuals && ew
    onAdd({
      stake: Math.round(outlay * 100) / 100,
      returned: Math.round(returned * 100) / 100,
      ...(price != null ? { price_taken: price } : {}),
      ...(sp != null ? { bfsp: sp } : {}),
      ...(isEw ? { each_way: true, place_fraction: terms } : {}),
    })
    setStake(stakeDefault); setStakeEdited(false)
    setOdds(DEFAULT_PRICE); setOddsTouched(false)
    setBsp(null); setSpOn(false); setPending(null)
    // Each-way and its terms persist across entries — a session of virtuals is
    // usually all EW or all not, so re-toggling per bet would be pure friction.
  }

  // A staff tip: real money out of the till, filed as a negative cash amount
  // flagged is_tip — it moves the total but is never counted as a bet.
  const tipV = parseFloat(tip)
  const canTip = !busy && tip !== '' && tipV > 0
  function submitTip() {
    if (!canTip) return
    onAdd({ amount: -Math.round(tipV * 100) / 100, is_tip: true })
    setTip('')
  }

  // Keyboard Enter files a LOSS — which is what it always did, since 'lose' was
  // the result the pad started on. Most bets lose, so on a desktop keyboard the
  // whole entry stays "type the stake, press Enter"; the four-figure guard
  // still applies, so a slipped decimal stops here too.
  const onEnterKey = e => { if (e.key === 'Enter') record('lose') }

  return (
    <section className={styles.pad}>
      <label className={styles.field}>
        <span className={styles.fieldLabel}>
          {isVirtuals && ew ? 'Stake (each way)' : 'Stake'}
        </span>
        <div className={styles.display}>
          <span className={styles.currency}>£</span>
          <input
            className={styles.amountInput}
            value={stake}
            onChange={onStakeChange}
            onKeyDown={onEnterKey}
            inputMode="decimal"
            type="text"
            placeholder="0.00"
            aria-label="Stake"
            autoComplete="off"
          />
        </div>
      </label>

      {/* Each-way is two bets, so the money actually leaving is double the unit
          typed above. Say so on the line itself — an EW stake that reads as the
          unit is the easiest figure on this page to get wrong. */}
      {isVirtuals && ew && hasStake && (
        <div className={styles.vsSpLine}>
          outlay <b>{exact(outlay)}</b> · 2 × {exact(stakeV)}
        </div>
      )}

      {/* Win-only vs each-way decides both the outlay and which results can
          happen, so it sits above the wheels rather than beside them. */}
      {isVirtuals && (
        <div className={styles.ewToggle} role="group" aria-label="Each way">
          <button
            type="button"
            className={`${styles.res} ${!ew ? styles.on : ''}`}
            aria-pressed={!ew}
            onClick={() => { setPending(null); setEw(false); if (!stakeEdited) setStake(DEFAULT_STAKE) }}
          >Win only</button>
          <button
            type="button"
            className={`${styles.res} ${ew ? styles.on : ''}`}
            aria-pressed={ew}
            onClick={() => { setPending(null); setEw(true); if (!stakeEdited) setStake(DEFAULT_STAKE_EW) }}
          >E/W</button>
        </div>
      )}

      {/* THE ENTRY. Not a selection to be confirmed later — a tap here files the
          bet, which is why each button carries the figure it will file.

          ABOVE the wheels, not below them: the wheels are the tallest thing in
          the pad, and with this row under them Lose/Push/Win sat in the bottom
          inch of the screen — where a phone's browser toolbar is, so reaching
          for Win hit the toolbar instead. Stake and result are also the only
          two controls every entry touches; the wheels are often left alone. */}
      <div className={styles.results} role="group" aria-label="Record result">
        <ResultKey
          kind="resLose" label="Lose" net={netFor('lose')}
          onClick={() => record('lose')} disabled={!canAdd}
        />
        {/* An each-way bet can place without winning; a win-only bet can't, so
            that slot stays the push it was. */}
        {isVirtuals && ew ? (
          <ResultKey
            kind="resPush" label="Place" net={netFor('place')}
            onClick={() => record('place')} disabled={!canAdd}
          />
        ) : (
          <ResultKey
            kind="resPush" label="Push" net={netFor('push')}
            onClick={() => record('push')} disabled={!canAdd}
          />
        )}
        <ResultKey
          kind="resWin" label="Win" net={netFor('win')}
          onClick={() => record('win')} disabled={!canAdd}
        />
      </div>

      <div className={isVirtuals && !ew ? styles.wheelsOne : styles.wheels}>
        <div className={styles.wheelCol}>
          <OddsWheel
            id="pnlOdds" label="Odds" value={odds}
            onChange={onOddsChange} onTouch={onOddsTouch}
          />
        </div>
        {isVirtuals ? (ew && (
          <div className={styles.wheelCol}>
            {/* Terms are a property of the race, printed on the slip — almost
                always 1/4, which is where the wheel rests. */}
            <OddsWheel
              id="pnlTerms" label="Place terms" value={terms}
              onChange={onTermsChange} ladder={TERMS_LADDER}
            />
          </div>
        )) : (
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
        )}
      </div>

      {!isVirtuals && spOn && (
        <div className={styles.vsSpLine}>
          vs SP <b className={odds > bspShown ? styles.pos : odds < bspShown ? styles.neg : ''}>{vsSp(odds, bspShown)}</b>
        </div>
      )}

      {/* The four-figure guard, and the only thing that ever delays a tap. It
          appears in place BELOW the buttons rather than as a modal, so the
          figure you're agreeing to and the row you tapped are both on screen. */}
      {pending && (
        <div className={styles.confirm} role="alertdialog" aria-live="assertive">
          <p className={styles.confirmQ}>
            Record <b className={pendingNet > 0 ? styles.pos : styles.neg}>{signedExact(pendingNet)}</b>?
            <span className={styles.confirmSub}>
              {exact(outlay)} at {odds.toFixed(2)} · {pending}
              {isVirtuals && ew ? ` · e/w ${TERMS_LADDER.find(t => t.dec === terms)?.label ?? ''}` : ''}
            </span>
          </p>
          <div className={styles.confirmBtns}>
            <button type="button" className={styles.cancel} onClick={() => setPending(null)}>Cancel</button>
            <button
              type="button" className={styles.enter}
              onClick={() => file(pending)} disabled={!canAdd}
            >Yes, record it</button>
          </div>
        </div>
      )}

      {/* Staff tip — a cash loss, not a bet. Its own amount and button, kept
          clear of the bet entry so it can't inherit a stake, odds or result. */}
      {isVirtuals && (
        <div className={styles.tipRow}>
          <label className={styles.tipField}>
            <span className={styles.fieldLabel}>Staff tip</span>
            <div className={styles.display}>
              <span className={styles.currency}>£</span>
              <input
                className={styles.amountInput}
                value={tip}
                onChange={e => setTip(sanitizeAmount(e.target.value))}
                onKeyDown={e => { if (e.key === 'Enter') submitTip() }}
                inputMode="decimal"
                type="text"
                placeholder="0.00"
                aria-label="Staff tip amount"
                autoComplete="off"
              />
            </div>
          </label>
          <button type="button" className={styles.tipBtn} onClick={submitTip} disabled={!canTip}>
            Tip
          </button>
        </div>
      )}
    </section>
  )
}
