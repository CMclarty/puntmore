// Fractional odds ladder and conversions.
//
// Coupon prices move during the day, so odds are picked rather than typed: a
// native <select> is a scroll wheel on mobile, needs no library, and cannot
// produce an unparseable value the way a free-text field can. The ladder is
// the conventional UK bookmaker set, so every price a coupon actually shows is
// on it.

export const FRACTIONAL_LADDER = [
  '1/100', '1/50', '1/33', '1/25', '1/20', '1/16', '1/14', '1/12', '1/10',
  '1/9', '1/8', '1/7', '2/13', '1/6', '2/11', '1/5', '2/9', '1/4', '2/7',
  '3/10', '1/3', '4/11', '2/5', '4/9', '1/2', '8/15', '4/7', '8/13', '3/5',
  '5/8', '4/6', '8/11', '3/4', '4/5', '5/6', '10/11', '1/1', '11/10', '6/5',
  '5/4', '11/8', '6/4', '8/5', '13/8', '7/4', '15/8', '2/1', '85/40', '9/4',
  '5/2', '11/4', '3/1', '10/3', '7/2', '4/1', '9/2', '5/1', '11/2', '6/1',
  '13/2', '7/1', '15/2', '8/1', '17/2', '9/1', '10/1', '11/1', '12/1', '14/1',
  '16/1', '20/1', '25/1', '33/1', '40/1', '50/1', '66/1', '100/1',
]

/** "7/4" -> 2.75 (decimal, stake included). */
export function fractionToDecimal(fraction) {
  const [n, d] = fraction.split('/').map(Number)
  if (!d) return null
  return n / d + 1
}

const LADDER_DECIMALS = FRACTIONAL_LADDER.map(f => ({
  fraction: f,
  decimal: fractionToDecimal(f),
}))

/**
 * Nearest ladder rung to a decimal price.
 *
 * Legs can hold a decimal that is not exactly on the ladder — OCR reads the
 * printed price, and older rows predate the picker. Snapping to the nearest
 * rung keeps the wheel showing something truthful rather than blank, and the
 * displayed decimal stays the leg's own value until the user actually picks.
 */
export function nearestFraction(decimal) {
  if (decimal == null) return null
  let best = null
  let bestDiff = Infinity
  for (const rung of LADDER_DECIMALS) {
    const diff = Math.abs(rung.decimal - decimal)
    if (diff < bestDiff) {
      bestDiff = diff
      best = rung.fraction
    }
  }
  return best
}

/** True when the leg's decimal sits on the ladder (within rounding). */
export function isOnLadder(decimal) {
  if (decimal == null) return false
  const f = nearestFraction(decimal)
  return Math.abs(fractionToDecimal(f) - decimal) < 0.005
}
