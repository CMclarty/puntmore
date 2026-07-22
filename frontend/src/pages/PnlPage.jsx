import { useState, useEffect, useRef, useCallback } from 'react'
import { getBets, addBet, deleteBet } from '../api'
import styles from './PnlPage.module.css'

const money = v => (v < 0 ? '-' : '') + '£' + Math.abs(v).toLocaleString('en-GB', { maximumFractionDigits: 2 })
const signed = v => (v > 0 ? '+' : v < 0 ? '−' : '') + '£' + Math.abs(v).toLocaleString('en-GB', { maximumFractionDigits: 2 })
const parseDate = s => new Date(/[Z+]/.test(s) ? s : s + 'Z')
const fmtDay = d => d.getDate() + ' ' + d.toLocaleString('en-GB', { month: 'short' })

function niceStep(range) {
  const raw = range / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / mag
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag
}

const KEYS = ['7', '8', '9', '4', '5', '6', '1', '2', '3', '.', '0', '⌫']

export default function PnlPage() {
  const [bets, setBets] = useState([])
  const [entry, setEntry] = useState('')
  const [sign, setSign] = useState(1)
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
  const mtd = bets
    .filter(b => { const d = parseDate(b.created_at); return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear() })
    .reduce((s, b) => s + b.amount, 0)
  const wins = bets.filter(b => b.amount > 0).length
  const winrate = bets.length ? Math.round(100 * wins / bets.length) + '%' : '0%'

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

  function press(k) {
    if (k === '⌫') return setEntry(e => e.slice(0, -1))
    if (k === '.') return setEntry(e => e.includes('.') ? e : (e || '0') + '.')
    setEntry(e => {
      if (e.includes('.') && e.split('.')[1].length >= 2) return e
      if (e.replace('.', '').length >= 6) return e
      return e + k
    })
  }

  async function commit() {
    const v = parseFloat(entry)
    if (!v || busy) return
    setBusy(true); setError(null)
    try {
      const bet = await addBet(sign * Math.round(v * 100) / 100)
      setBets(bs => [...bs, bet])
      setEntry(''); setSign(1)
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

  const canEnter = entry !== '' && parseFloat(entry) !== 0 && !busy

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
          </div>
          <div className={styles.stat}>
            <div className={styles.statLabel}>Month to date</div>
            <div className={`${styles.figure} ${styles.mtd} ${mtd < 0 ? styles.neg : styles.pos}`}>{signed(mtd)}</div>
          </div>
        </div>
        <div className={styles.subline}>
          <span><b>{bets.length}</b> bets</span>
          <span><b>{winrate}</b> win</span>
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

      <section className={styles.pad}>
        <div className={`${styles.display} ${sign > 0 ? styles.plus : styles.minus}`}>
          <span className={styles.dSign}>{sign > 0 ? '+' : '−'}</span>
          <span className={`${styles.amt} ${entry === '' ? styles.empty : ''}`}>
            {entry === '' ? '£0.00' : '£' + entry}
          </span>
        </div>
        <div className={styles.toggle}>
          <button
            className={`${styles.minusBtn} ${sign < 0 ? styles.on : ''}`}
            onClick={() => setSign(-1)}
          ><span>−</span><span className={styles.lbl}>Loss</span></button>
          <button
            className={`${styles.plusBtn} ${sign > 0 ? styles.on : ''}`}
            onClick={() => setSign(1)}
          ><span>+</span><span className={styles.lbl}>Win</span></button>
        </div>
        <div className={styles.keys}>
          {KEYS.map(k => (
            <button key={k} className={k === '.' || k === '⌫' ? styles.util : ''} onClick={() => press(k)}>{k}</button>
          ))}
        </div>
        <button className={styles.enter} onClick={commit} disabled={!canEnter}>Enter</button>
      </section>
    </div>
  )
}
