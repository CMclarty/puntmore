import { useRef, useEffect, useMemo, useCallback } from 'react'
import styles from './OddsWheel.module.css'

// The Betfair price ladder: increment widens as the price lengthens. Building
// the wheel from this rather than free text means every value entered is a real
// tradeable price — you cannot land on 3.03, which is not a price that exists.
const BANDS = [
  [1.01, 2, 0.01],
  [2, 3, 0.02],
  [3, 4, 0.05],
  [4, 6, 0.1],
  [6, 10, 0.2],
  [10, 20, 0.5],
  [20, 30, 1],
  [30, 50, 2],
  [50, 100, 5],
  [100, 1000, 10],
]

export const LADDER = (() => {
  const out = []
  for (const [from, to, step] of BANDS) {
    // Rounded per tick: repeated float addition drifts (1.01+0.01 → 1.0199…).
    const n = Math.round((to - from) / step)
    for (let i = 0; i < n; i++) out.push(Math.round((from + i * step) * 100) / 100)
  }
  out.push(1000)
  return out
})()

const ROW = 34   // px per row; must match .row height in the CSS

// Nearest real ladder price to an arbitrary number — used to seat an incoming
// value that came from somewhere other than this wheel.
export function nearestTick(v) {
  if (!Number.isFinite(v)) return null
  let best = LADDER[0]
  for (const p of LADDER) if (Math.abs(p - v) < Math.abs(best - v)) best = p
  return best
}

/**
 * A scroll-snapping odds picker. Native scrolling does the work — no drag
 * maths, no library — so it has momentum and rubber-banding on touch for free,
 * and stays usable with a trackpad or a mouse wheel on the desktop.
 *
 * value: decimal odds or null. onChange: (price) => void.
 */
export default function OddsWheel({ value, onChange, label, id }) {
  const ref = useRef(null)
  const settle = useRef(null)
  // Held so programmatic scrolling (seating the initial value) doesn't feed
  // itself back through onChange as if the user had spun it.
  const quiet = useRef(false)

  const index = useMemo(() => {
    if (value == null) return -1
    const i = LADDER.indexOf(value)
    return i === -1 ? LADDER.indexOf(nearestTick(value)) : i
  }, [value])

  // Seat the wheel on the selected row whenever the value changes from outside
  // (including the reset to null after a bet is filed).
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const target = index === -1 ? 0 : index * ROW
    if (Math.abs(el.scrollTop - target) < 1) return
    quiet.current = true
    el.scrollTop = target
    // One frame is enough for the scroll event this triggers to have fired.
    const t = setTimeout(() => { quiet.current = false }, 60)
    return () => clearTimeout(t)
  }, [index])

  const onScroll = useCallback(() => {
    const el = ref.current
    if (!el || quiet.current) return
    // Report only once the wheel has come to rest — otherwise every frame of a
    // flick fires a change, and the parent re-renders through the whole spin.
    clearTimeout(settle.current)
    settle.current = setTimeout(() => {
      const i = Math.max(0, Math.min(LADDER.length - 1, Math.round(el.scrollTop / ROW)))
      if (LADDER[i] !== value) onChange(LADDER[i])
    }, 90)
  }, [onChange, value])

  useEffect(() => () => clearTimeout(settle.current), [])

  return (
    <div className={styles.wheel}>
      <label className={styles.label} htmlFor={id}>{label}</label>
      <div className={styles.viewport}>
        <div className={styles.selection} aria-hidden="true" />
        <div
          className={styles.scroller}
          ref={ref}
          onScroll={onScroll}
          id={id}
          role="listbox"
          aria-label={label}
          tabIndex={0}
        >
          {/* Spacers let the first and last prices reach the centre line. */}
          <div className={styles.spacer} />
          {LADDER.map((p, i) => (
            <div
              key={p}
              className={`${styles.row} ${i === index ? styles.on : ''}`}
              role="option"
              aria-selected={i === index}
            >
              {p.toFixed(2)}
            </div>
          ))}
          <div className={styles.spacer} />
        </div>
      </div>
    </div>
  )
}
