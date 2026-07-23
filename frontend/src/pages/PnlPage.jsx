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

      <PnlEntry onAdd={handleAdd} busy={busy} />
    </div>
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
    if (mode === 'flat') {
      onAdd({ amount: sign * Math.round(value * 100) / 100 })
    } else {
      onAdd({
        stake: Math.round(stakeV * 100) / 100,
        returned: Math.round(retV * 100) / 100,
      })
    }
    setAmount(''); setSign(1); setStake(''); setRet(''); setConfirming(false)
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
