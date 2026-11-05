// Virtual racing boost maths (Sprint Valley / Portman Park / Steepledowns).
//
// Measured from 1,449 settled races, 23–29 Sep 2026 (A McLean cards, the same
// prices Betfred shows):
//  - Win chance is exactly the implied probability with the overround removed
//    proportionally. The fitted power exponent was 1.000, so there is no
//    favourite–longshot bias to correct for.
//  - The finishing order follows Harville: place counts matched it within
//    ±1.6σ in every price band and field size.
// So a boost prices from the card alone. The runner's own price gives its win
// chance. The rest of the field is filled with that field size's median price
// profile, which reproduced real-card place chances to ±0.4pp (sd). Per-race
// overround varies by about ±1%, so EV figures carry roughly ±1% either way.

// overround: mean book %; places/terms: A McLean each-way terms (places paid,
// 1/terms of the odds); field: median win-probability profile, shortest first.
export const FIELDS = {
  8: { overround: 1.1598, places: 3, terms: 5, field: [0.2659, 0.2009, 0.143, 0.1165, 0.0971, 0.0795, 0.0672, 0.0298] },
  9: { overround: 1.1598, places: 3, terms: 5, field: [0.2496, 0.1753, 0.1361, 0.1157, 0.0966, 0.0785, 0.0663, 0.0516, 0.0303] },
  10: { overround: 1.1601, places: 3, terms: 5, field: [0.2452, 0.1744, 0.1341, 0.1094, 0.0881, 0.0729, 0.0583, 0.0509, 0.0413, 0.0254] },
  11: { overround: 1.159, places: 3, terms: 5, field: [0.2167, 0.1719, 0.1358, 0.1113, 0.0888, 0.0728, 0.0582, 0.0469, 0.0386, 0.0333, 0.0257] },
  12: { overround: 1.2093, places: 3, terms: 4, field: [0.1865, 0.1494, 0.1264, 0.1038, 0.0917, 0.0754, 0.064, 0.055, 0.0483, 0.0395, 0.0357, 0.0242] },
  13: { overround: 1.2104, places: 3, terms: 4, field: [0.191, 0.1522, 0.1106, 0.0955, 0.0832, 0.0701, 0.0641, 0.0554, 0.0488, 0.04, 0.0364, 0.0321, 0.0205] },
  14: { overround: 1.2107, places: 3, terms: 4, field: [0.1661, 0.137, 0.1105, 0.0973, 0.083, 0.0747, 0.0639, 0.0553, 0.0487, 0.0429, 0.0361, 0.0319, 0.0286, 0.0241] },
  15: { overround: 1.21, places: 3, terms: 4, field: [0.1755, 0.1287, 0.0976, 0.0885, 0.0766, 0.0691, 0.0638, 0.0554, 0.0489, 0.0437, 0.0394, 0.036, 0.0318, 0.0286, 0.0165] },
  16: { overround: 1.276, places: 4, terms: 4, field: [0.14, 0.1185, 0.0909, 0.0854, 0.077, 0.07, 0.0637, 0.0593, 0.052, 0.0461, 0.0418, 0.0402, 0.0364, 0.0335, 0.0298, 0.0153] },
  17: { overround: 1.2806, places: 4, terms: 4, field: [0.1509, 0.1212, 0.0882, 0.0791, 0.0722, 0.066, 0.0608, 0.0529, 0.0523, 0.0462, 0.0415, 0.0376, 0.0343, 0.0305, 0.0274, 0.0271, 0.0118] },
  18: { overround: 1.2808, places: 4, terms: 4, field: [0.1419, 0.1119, 0.0873, 0.0787, 0.0716, 0.0656, 0.0605, 0.0525, 0.0519, 0.0461, 0.0413, 0.0374, 0.0342, 0.0303, 0.0271, 0.027, 0.0231, 0.0117] },
  19: { overround: 1.278, places: 4, terms: 4, field: [0.1282, 0.1095, 0.0864, 0.078, 0.071, 0.065, 0.06, 0.0523, 0.0512, 0.0456, 0.0408, 0.037, 0.0339, 0.0301, 0.0271, 0.0267, 0.0229, 0.0226, 0.0116] },
  20: { overround: 1.2986, places: 4, terms: 4, field: [0.1294, 0.1044, 0.0774, 0.0709, 0.0692, 0.0635, 0.0591, 0.0515, 0.0459, 0.0453, 0.0406, 0.0368, 0.0337, 0.0331, 0.0296, 0.0266, 0.0263, 0.0227, 0.0225, 0.0115] },
  21: { overround: 1.2996, places: 4, terms: 4, field: [0.1247, 0.0968, 0.0779, 0.0713, 0.0654, 0.0604, 0.0594, 0.0517, 0.0462, 0.0454, 0.0408, 0.0371, 0.034, 0.0333, 0.0297, 0.0267, 0.0232, 0.0228, 0.0226, 0.0189, 0.0117] },
  22: { overround: 1.3008, places: 4, terms: 4, field: [0.1292, 0.097, 0.0774, 0.0704, 0.0647, 0.0598, 0.052, 0.0515, 0.0456, 0.0412, 0.0406, 0.0368, 0.0336, 0.0299, 0.0295, 0.0267, 0.0228, 0.0228, 0.019, 0.0189, 0.0188, 0.0117] },
}

export const RUNNER_COUNTS = Object.keys(FIELDS).map(Number)

// Harville chance that runner 0 of `probs` finishes in the first k places.
function harvilleTopK(probs, k) {
  const n = probs.length
  const used = new Array(n).fill(false)
  let total = 0
  // Walk every ordering of the other runners ahead of the target, up to k-1
  // deep. At most 21·20·19 paths, so it's instant.
  function walk(prob, remaining, depth) {
    total += prob * probs[0] / remaining          // target finishes next
    if (depth + 1 >= k) return
    for (let i = 1; i < n; i++) {
      if (used[i]) continue
      used[i] = true
      walk(prob * probs[i] / remaining, remaining - probs[i], depth + 1)
      used[i] = false
    }
  }
  walk(1, 1, 0)
  return total
}

/**
 * Price one boosted runner.
 * @param runners field size (8–22)
 * @param price   the runner's decimal price before the boost
 * @param boosted the boosted decimal price (whole bet, place part included)
 * @param places  places paid; defaults to the field's usual terms
 * @param terms   place odds as 1/terms; defaults to the field's usual terms
 * Returns EV per £1 of total outlay: each-way (win + place, £2) and win only.
 */
export function priceBoost({ runners, price, boosted, places, terms }) {
  const f = FIELDS[runners]
  if (!f || !(price > 1) || !(boosted > 1)) return null
  const k = places ?? f.places
  const t = terms ?? f.terms
  const pWin = (1 / price) / f.overround
  if (pWin >= 1) return null

  // Seat the runner in the median field: drop the profile slot nearest its
  // chance, and scale the rest to share what's left.
  let j = 0
  f.field.forEach((v, i) => { if (Math.abs(v - pWin) < Math.abs(f.field[j] - pWin)) j = i })
  const rest = f.field.filter((_, i) => i !== j)
  const restSum = rest.reduce((a, b) => a + b, 0)
  const probs = [pWin, ...rest.map(v => v * (1 - pWin) / restSum)]
  const pPlace = harvilleTopK(probs, Math.min(k, runners))

  const placeReturn = 1 + (boosted - 1) / t
  const evWin = pWin * boosted - 1
  const evPlace = pPlace * placeReturn - 1
  const evEw = (evWin + evPlace) / 2
  return {
    pWin, pPlace, places: k, terms: t, overround: f.overround,
    evWin, evPlace, evEw,
    fairWin: 1 / pWin,
    // Boosted price at which each-way breaks even: pWin·B + pPlace·(1+(B−1)/t) = 2.
    breakEvenEw: (2 - pPlace + pPlace / t) / (pWin + pPlace / t),
  }
}

// EV below this is inside the model's ±1% noise — called, but marked thin.
export const THIN = 0.02

/**
 * The call on a priced boost (a priceBoost result): back it each way, win
 * only, or skip it. `ew` is the mode to back it in, null on a skip; `ev` the
 * better of the two per £1 of outlay; `tone` 'pos' | 'thin' | 'neg'.
 */
export function verdictFor(r) {
  if (!r) return null
  const ev = Math.max(r.evEw, r.evWin)
  if (ev <= 0) return { word: 'Skip', ew: null, ev, tone: 'neg' }
  const ew = r.evEw >= r.evWin
  return { word: ew ? 'Each way' : 'Win only', ew, ev, tone: ev < THIN ? 'thin' : 'pos' }
}
