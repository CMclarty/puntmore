import { useState, useEffect, useRef, useCallback } from 'react'
import { getShopRoute, logShopVisit, deleteShopVisit, resetShopRoute } from '../api'
import styles from './ShopPage.module.css'

/**
 * Shop visit countdown — a field-route tally, one tap per shop visited.
 *
 * The target is the arithmetic the maths panel prints: £500/shop × 5/day ×
 * 4 days/wk × 40 wks = £400,000 of pipeline, at an assumed 20% ROI. Those five
 * numbers come from the API (`routers/shop.py`) rather than living here, so
 * the sum on screen and the total being counted down can never disagree.
 *
 * "Week" is a UNIT OF TWENTY VISITS, not a calendar week — week 3 means visits
 * 41–60, whenever they happened. A route runs at its own pace, and a calendar
 * week would reset the count on a quiet one, making it a scoreboard for the
 * calendar rather than for the route.
 *
 * Every visit is a row owned by the session user, so the tally follows you
 * from the phone in the field to the desk. Taps apply optimistically: a stop
 * is logged standing outside the shop, often on one bar of signal, and the
 * counter has to answer the thumb rather than the network. A failed write is
 * rolled back and said out loud — never left looking logged.
 */

const money = n => '£' + Math.round(n).toLocaleString('en-GB')

export default function ShopPage() {
  const [model, setModel] = useState(null)   // route constants, from the API
  const [visits, setVisits] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [confirmReset, setConfirmReset] = useState(false)
  const canvasRef = useRef(null)
  const tempId = useRef(0)

  useEffect(() => {
    let live = true
    getShopRoute()
      .then(r => { if (live) { setModel(r.model); setVisits(r.visits); setError(null) } })
      .catch(e => { if (live) setError(e?.message || 'Could not load the route') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [])

  const burst = useConfetti(canvasRef)

  const total = visits.length
  const done = model != null && total >= model.goal

  const logVisit = useCallback(async () => {
    if (!model || visits.length >= model.goal) return
    // Optimistic row. The id is a string until the API answers, so it can be
    // found and swapped (or dropped) without matching on position — two taps
    // in flight at once must not undo each other's row.
    const temp = { id: `tmp-${tempId.current++}`, created_at: new Date().toISOString() }
    const after = visits.length + 1
    setVisits(v => [...v, temp])
    setError(null)
    if (after === model.goal) burst(220)
    else if (after % 100 === 0) burst(140)
    else if (after % model.week_size === 0) burst(70)
    try {
      const saved = await logShopVisit()
      setVisits(v => v.map(x => (x.id === temp.id ? saved : x)))
    } catch (e) {
      setVisits(v => v.filter(x => x.id !== temp.id))
      setError(e?.message || 'That tap did not save')
    }
  }, [model, visits.length, burst])

  const undo = useCallback(async () => {
    const last = visits[visits.length - 1]
    // A row still in flight has no server id yet, so there is nothing to
    // delete — wait for it rather than sending "tmp-3" to the API.
    if (!last || typeof last.id !== 'number') return
    setVisits(v => v.filter(x => x.id !== last.id))
    setError(null)
    try {
      await deleteShopVisit(last.id)
    } catch (e) {
      setVisits(v => [...v, last].sort((a, b) => a.id - b.id))
      setError(e?.message || 'Could not undo that tap')
    }
  }, [visits])

  const reset = useCallback(async () => {
    const previous = visits
    setVisits([])
    setConfirmReset(false)
    setError(null)
    try {
      await resetShopRoute()
    } catch (e) {
      setVisits(previous)
      setError(e?.message || 'Could not reset the route')
    }
  }, [visits])

  if (loading) return <div className={styles.page}><p className={styles.note}>Loading…</p></div>
  if (!model) {
    return (
      <div className={styles.page}>
        <div className={styles.head}><span>Shop visits</span><span className={styles.tag}>Route</span></div>
        <p className={styles.error}>{error}</p>
      </div>
    )
  }

  const {
    week_size: weekSize, weeks, goal,
    shops_per_day: perDay, days_per_week: daysPerWeek,
    value_per_shop: valuePerShop, roi_rate: roiRate,
  } = model

  // Week index is derived from the count and clamped at the last week, so the
  // final visit reads "week 40 of 40" rather than rolling into a 41st.
  const weekIndex = Math.min(weeks - 1, Math.floor(total / weekSize))
  const inWeek = total - weekIndex * weekSize
  const weekLeft = weekSize - inWeek
  const weeksCleared = Math.floor(total / weekSize)
  const yearLeft = goal - total
  const yearPct = Math.min(100, (total / goal) * 100)
  const target = goal * valuePerShop

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        <span>Shop visits</span><span className={styles.tag}>Route</span>
      </div>

      <div className={styles.panels}>
        <button
          type="button"
          className={styles.week}
          onClick={logVisit}
          disabled={done}
          aria-label={done ? 'Route complete' : 'Log a shop visit, tap after each stop'}
        >
          <span className={styles.panelLabel}>This week</span>
          {/* key= restarts the count animation on every change, including a
              change back to a number already shown (undo after a log). */}
          <span key={weekLeft} className={styles.bigNum}>{weekLeft}</span>
          <span className={styles.panelSub}>{weekLeft === 0 ? 'week cleared' : `left of ${weekSize}`}</span>
          <span className={styles.track}>
            <span className={styles.fill} style={{ width: `${(inWeek / weekSize) * 100}%` }} />
          </span>
          <span className={styles.panelFoot}>
            {done ? `All ${weeks} weeks done` : `Week ${weekIndex + 1} of ${weeks}`}
          </span>
        </button>

        <div className={styles.year} aria-live="polite">
          <span className={styles.panelLabel}>Full year</span>
          <span key={yearLeft} className={styles.bigNum}>{yearLeft}</span>
          <span className={styles.panelSub}>{yearLeft === 0 ? 'route complete' : `left of ${goal}`}</span>
          <span className={styles.track}>
            <span className={styles.fill} style={{ width: `${yearPct}%` }} />
          </span>
          <span className={styles.panelFoot}>
            {Math.round(yearPct)}% · {weeksCleared}/{weeks} weeks
          </span>
        </div>
      </div>

      {error && <p className={styles.error}>{error}</p>}

      {/* The model, spelled out — the panels above are only ever this sum
          counted down, so it is worth showing rather than hiding in a doc. */}
      <div className={styles.maths}>
        <div className={styles.formula}>
          {money(valuePerShop)}<span className={styles.op}>/shop</span>
          <span className={styles.op}>×</span>{perDay}<span className={styles.op}>/day</span>
          <span className={styles.op}>×</span>{daysPerWeek}<span className={styles.op}>/wk</span>
          <span className={styles.op}>×</span>{weeks}<span className={styles.op}>wks</span>
          <span className={styles.eq}>=</span>{money(target)}
        </div>
        <div className={styles.stats}>
          <div className={styles.stat}>
            <div className={styles.statVal}>{money(total * valuePerShop)}</div>
            <div className={styles.statLabel}>value logged of {money(target)}</div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statVal}>{money(total * valuePerShop * roiRate)}</div>
            <div className={styles.statLabel}>
              ROI banked ({Math.round(roiRate * 100)}%) of {money(target * roiRate)}
            </div>
          </div>
        </div>
      </div>

      <div className={styles.utility}>
        <button type="button" className={styles.link} disabled={total === 0} onClick={undo}>
          Undo last tap
        </button>
        <button
          type="button" className={styles.link} disabled={total === 0}
          onClick={() => setConfirmReset(true)}
        >Reset route</button>
      </div>

      <canvas ref={canvasRef} className={styles.confetti} aria-hidden="true" />

      {confirmReset && (
        <div className={styles.overlay} onClick={e => { if (e.target === e.currentTarget) setConfirmReset(false) }}>
          <div className={styles.modal} role="dialog" aria-modal="true" aria-label="Reset the whole route?">
            <h3>Reset the whole route?</h3>
            <p>This clears every logged visit. There’s no undo for this one.</p>
            <div className={styles.modalActions}>
              <button type="button" onClick={() => setConfirmReset(false)}>Cancel</button>
              <button type="button" className={styles.danger} onClick={reset}>Reset route</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// Confetti on a canvas that is fixed, pointer-events:none and aria-hidden — it
// is decoration over the page and must never intercept the next tap. Returns a
// stable burst(count); it no-ops under prefers-reduced-motion rather than
// running a shortened version.
function useConfetti(canvasRef) {
  const particles = useRef([])
  const running = useRef(false)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const resize = () => { canvas.width = window.innerWidth; canvas.height = window.innerHeight }
    resize()
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [canvasRef])

  return useCallback(count => {
    const canvas = canvasRef.current
    if (!canvas) return
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    const ctx = canvas.getContext('2d')
    const originX = window.innerWidth / 2
    const colours = ['#2F7D55', '#4E9B72', '#A8792F', '#C79A4E', '#D8D5CD']
    for (let i = 0; i < count; i++) {
      particles.current.push({
        x: originX + (Math.random() - 0.5) * 120,
        y: window.innerHeight * 0.3,
        vx: (Math.random() - 0.5) * 6,
        vy: Math.random() * -6 - 3,
        size: Math.random() * 5 + 3,
        colour: colours[Math.floor(Math.random() * colours.length)],
        rot: Math.random() * Math.PI,
        vrot: (Math.random() - 0.5) * 0.3,
        life: 0,
        maxLife: 90 + Math.random() * 30,
      })
    }
    if (running.current) return
    running.current = true
    const tick = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      for (const p of particles.current) {
        p.vy += 0.18; p.x += p.vx; p.y += p.vy; p.rot += p.vrot; p.life++
        ctx.save()
        ctx.globalAlpha = Math.max(0, 1 - p.life / p.maxLife)
        ctx.translate(p.x, p.y)
        ctx.rotate(p.rot)
        ctx.fillStyle = p.colour
        ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6)
        ctx.restore()
      }
      particles.current = particles.current.filter(p => p.life < p.maxLife)
      if (particles.current.length) requestAnimationFrame(tick)
      else { running.current = false; ctx.clearRect(0, 0, canvas.width, canvas.height) }
    }
    requestAnimationFrame(tick)
  }, [canvasRef])
}
