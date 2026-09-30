import { useRef, useEffect, useMemo, useCallback, memo } from 'react'
import styles from './OddsWheel.module.css'

// The bookmakers' fractional ladder — the prices actually returned as SPs —
// from 1/20 to 25/1. Not an even scale: the rungs are the traditional board
// prices, so 5/2 is followed by 11/4 and then 3/1, with nothing between.
//
// Stored as [numerator, denominator]; the decimal is derived, never typed, so
// the two can't disagree. The database column stays decimal, which keeps the
// vs-SP arithmetic and every bet recorded before this change working unaltered.
const FRACTIONS = [
  // odds-on
  [1, 20], [1, 16], [1, 14], [1, 12], [1, 10], [1, 9], [1, 8], [1, 7], [1, 6],
  [1, 5], [2, 9], [1, 4], [2, 7], [3, 10], [1, 3], [4, 11], [2, 5], [4, 9],
  [1, 2], [8, 15], [4, 7], [8, 13], [4, 6], [8, 11], [4, 5], [5, 6], [10, 11],
  // evens
  [1, 1],
  // odds-against
  [11, 10], [6, 5], [5, 4], [13, 10], [11, 8], [7, 5], [6, 4], [13, 8], [7, 4],
  [15, 8], [2, 1], [9, 4], [5, 2], [11, 4], [3, 1], [10, 3], [7, 2], [4, 1],
  [9, 2], [5, 1], [11, 2], [6, 1], [13, 2], [7, 1], [15, 2], [8, 1], [17, 2],
  [9, 1], [10, 1], [11, 1], [12, 1], [14, 1], [16, 1], [18, 1], [20, 1],
  [22, 1], [25, 1],
]

export const LADDER = FRACTIONS.map(([n, d]) => ({
  n,
  d,
  label: n === 1 && d === 1 ? 'EVS' : `${n}/${d}`,
  // 2dp to match the column's rounding, so what the wheel shows is exactly what
  // gets stored (10/11 → 1.91, not 1.9090909…).
  dec: Math.round((n / d + 1) * 100) / 100,
}))

// Where a wheel sits before it's touched.
export const DEFAULT_PRICE = LADDER.find(t => t.n === 2 && t.d === 1).dec   // 2/1

/**
 * Each-way place terms, as a fraction of the odds taken. A second ladder for
 * the same wheel — the rungs are the terms boards actually run, so there is
 * nothing between 1/4 and 1/5.
 *
 * `dec` is the fraction ITSELF (0.25 = quarter odds), NOT a price, and is left
 * unrounded: 1/3 must stay 0.333… or a placed third-odds bet settles a penny
 * light. The display rounds; the stored value doesn't.
 */
export const TERMS_LADDER = [[1, 6], [1, 5], [1, 4], [1, 3], [1, 2]].map(([n, d]) => ({
  n, d, label: `${n}/${d}`, dec: n / d,
}))

// Almost every board is quarter odds, so that is where the wheel rests.
export const DEFAULT_TERMS = TERMS_LADDER.find(t => t.d === 4).dec

const ROW = 34   // px per row; must match .row height in the CSS

// Nearest rung to an arbitrary value — seats one that came from somewhere other
// than this wheel, including bets recorded against the old Betfair ladder.
export function nearestTick(v, rungs = LADDER) {
  if (!Number.isFinite(v)) return null
  let best = rungs[0]
  for (const t of rungs) if (Math.abs(t.dec - v) < Math.abs(best.dec - v)) best = t
  return best.dec
}

/**
 * A scroll-snapping odds picker. Native scrolling does the work — no drag
 * maths, no library — so it has momentum and rubber-banding on touch for free,
 * and stays usable with a trackpad or a mouse wheel on the desktop.
 *
 * value: decimal odds or null. onChange: (price) => void.
 * seat: the row a BLANK wheel rests on, so a spin starts near the likely price.
 *   Defaults to 2/1; the BFSP wheel passes the current odds so it tracks the
 *   price taken and a set is a short nudge, not a spin from scratch.
 * onTouch: fired once per settled USER spin, before onChange and regardless of
 *   whether the value changed. Lets a caller tell "the user chose this price"
 *   from "nobody moved it off the default" — including the case where a spin
 *   lands back on the value it started from, which onChange stays silent about.
 *   Must be stable (useCallback), like onChange.
 * ladder: which rungs the wheel offers — the odds board by default, or
 *   TERMS_LADDER for each-way place terms. Every rung is {label, dec}; `dec` is
 *   the value stored and reported.
 * ghost: show the seated price and track it live, but styled as NOT being
 *   recorded — for a wheel that follows another until the user opts in. The
 *   value is still null (nothing is filed); tapping fires onActivate.
 * onActivate: fired when a ghost wheel is tapped. Spinning one activates it too,
 *   via onChange. Stable (useCallback), like onChange.
 * decimalOnly: the ladder's labels are already decimals, so drop the second,
 *   quieter decimal beside each rung (the Boost page works in decimal only).
 */
function OddsWheel({
  value, onChange, label, id, seat = DEFAULT_PRICE, onTouch,
  ghost = false, onActivate, ladder = LADDER, decimalOnly = false,
}) {
  const ref = useRef(null)
  const settle = useRef(null)
  // Held so programmatic scrolling (seating the initial value) doesn't feed
  // itself back through onChange as if the user had spun it.
  const quiet = useRef(false)
  // A null value is BLANK, not a price. The wheel still SEATS on the `seat` row
  // so a spin starts somewhere sensible, but nothing is shown as selected and
  // the centre band shows a placeholder — an unset wheel must not read as a
  // chosen price. (Used by the optional BFSP wheel; odds carries a default.)
  //
  // A GHOST wheel is also unset, but deliberately shows the price it is
  // tracking instead of a placeholder, so `blank` (and its cover) is off.
  const blank = value == null && !ghost

  const index = useMemo(() => {
    const dec = value == null ? (seat ?? DEFAULT_PRICE) : value
    const i = ladder.findIndex(t => t.dec === dec)
    return i === -1 ? ladder.findIndex(t => t.dec === nearestTick(dec, ladder)) : i
  }, [value, seat, ladder])

  // Seat the wheel on the selected row whenever the value changes from outside
  // (including the reset after a bet is filed, which returns it to blank). Mark
  // it quiet: the scroll this triggers is the seat's own, not a user spin, and
  // must not commit a value. `quiet` is cleared in onScroll once that scroll
  // settles — NOT on a fixed timer, which fired too early on mobile and let the
  // seat auto-commit 2/1, silently setting an "unset" wheel.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const target = index * ROW
    if (Math.abs(el.scrollTop - target) < 1) return
    const before = el.scrollTop
    quiet.current = true
    el.scrollTop = target
    // If the assignment didn't move anything — the list isn't laid out yet, so
    // the browser clamps it — no scroll event follows to clear `quiet`, and a
    // stuck flag silently swallows the user's NEXT genuine spin. Clear it here
    // instead of waiting for an event that will never arrive.
    if (el.scrollTop === before) quiet.current = false
  }, [index])

  const onScroll = useCallback(() => {
    const el = ref.current
    if (!el) return
    // Report only once the wheel has come to rest — otherwise every frame of a
    // flick fires a change, and the parent re-renders through the whole spin.
    clearTimeout(settle.current)
    settle.current = setTimeout(() => {
      const i = Math.max(0, Math.min(ladder.length - 1, Math.round(el.scrollTop / ROW)))
      const seating = quiet.current
      quiet.current = false
      // A programmatic seat leaves the wheel ON `index` — that is what seating
      // means — so a settle that ends anywhere ELSE is the user having moved it,
      // whatever the flag says.
      //
      // Trusting the flag alone lost picks. This timer is DEBOUNCED, so when a
      // spin starts within 90ms of a seat (exactly what happens right after
      // filing a bet, which reseats the wheel) the seat's own settle never runs
      // separately — one settle fires at the end of the user's spin, saw the
      // flag, and swallowed the pick. The wheel then sat visibly on 11/4 while
      // the bet still priced at the 2/1 default, and it took spinning away and
      // back to stick.
      if (seating && i === index) return
      onTouch?.()
      if (ladder[i].dec !== value) onChange(ladder[i].dec)
    }, 90)
  }, [onChange, onTouch, value, ladder, index])

  useEffect(() => () => clearTimeout(settle.current), [])

  return (
    <div className={styles.wheel}>
      <label className={styles.label} htmlFor={id}>{label}</label>
      <div className={`${styles.viewport} ${blank ? styles.blank : ''} ${ghost ? styles.ghost : ''}`}>
        <div className={styles.selection} aria-hidden="true" />
        <div
          className={styles.scroller}
          ref={ref}
          onScroll={onScroll}
          /* A tap on a ghost wheel opts it in at the price it is already
             tracking — the whole point of the ghost state. A spin activates it
             too, through onChange. */
          onClick={ghost ? onActivate : undefined}
          id={id}
          role="listbox"
          aria-label={label}
          tabIndex={0}
        >
          {/* Spacers let the first and last prices reach the centre line. */}
          <div className={styles.spacer} />
          {ladder.map((t, i) => (
            <div
              key={t.label}
              className={`${styles.row} ${i === index && !blank ? styles.on : ''}`}
              role="option"
              /* Nothing is SELECTED while ghosting — the row is merely the one
                 being tracked — even though it carries the (dimmed) .on look. */
              aria-selected={i === index && !blank && !ghost}
              aria-label={`${t.label}, decimal ${t.dec.toFixed(2)}`}
            >
              <span className={styles.frac}>{t.label}</span>
              {!decimalOnly && <span className={styles.dec}>{t.dec.toFixed(2)}</span>}
            </div>
          ))}
          <div className={styles.spacer} />
        </div>
        {/* Covers the centre band while blank, so no price reads as selected. */}
        <div className={styles.placeholder} aria-hidden="true">—</div>
      </div>
    </div>
  )
}

// Each wheel renders 65 price rows, so two of them rebuilt 130 elements on
// every parent render — including each keystroke in the stake field, which is
// what made the P&L entry feel laggy. Memoised, a wheel re-renders only when
// its own value changes. Callers MUST pass a stable onChange (useCallback) or
// this does nothing.
export default memo(OddsWheel)
