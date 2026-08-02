/**
 * The P&L tracker wearing a Game Boy Advance skin, at /pnl/battle.
 *
 * Deliberately the SAME PAGE as /pnl — same sections in the same order, same
 * figures, same entry flow — only dressed differently. It reads GET /pnl/bets
 * and files through POST /pnl/bets on the same payload, so a bet entered either
 * side shows up on both.
 *
 * The wheels are the shared OddsWheel component, unmodified: it styles itself
 * from the global tokens, and this page's CSS module redefines those locally,
 * so it picks up the skin for free.
 */
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { getBets, addBet, deleteBet } from '../api'
import OddsWheel, { DEFAULT_PRICE, TERMS_LADDER, DEFAULT_TERMS } from '../components/OddsWheel'
import styles from './PnlBattlePage.module.css'

const parseDate = s => new Date(/[Z+]/.test(s) ? s : s + 'Z')
const vsSpPct = (price, sp) => (price / sp - 1) * 100
const pct = v => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(1) + '%'

/** EV of the price taken, in pounds: SP is the fair price, so a bet struck at
 *  P returns stake × (P/SP − 1) in expectation. Needs the stake. */
const evOf = b => b.stake * (b.price_taken / b.bfsp - 1)
const hasEv = b => b.price_taken != null && b.bfsp != null && b.stake != null

/** Same abbreviation rule as the plain tracker: k notation above £1,000. */
function magnitude(abs) {
  if (abs < 1000) return abs.toLocaleString('en-GB', { maximumFractionDigits: 2 })
  const k = abs / 1000
  const oneDp = Math.round(k * 10) / 10
  return oneDp >= 100 ? `${Math.round(k)}k` : `${oneDp.toFixed(1)}k`
}
const money = v => (v < 0 ? '-' : '') + '£' + magnitude(Math.abs(v))
const signed = v => (v > 0 ? '+' : v < 0 ? '−' : '') + '£' + magnitude(Math.abs(v))
const exact = v =>
  (v > 0 ? '+' : v < 0 ? '−' : '') + '£' +
  Math.abs(v).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const BIG_ENTRY = 1000

/** Shared with the plain tracker: each-way is two bets, so the outlay doubles
 *  and the place part pays unit × (1 + (odds − 1) × terms). */
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

export default function PnlBattlePage({ category = 'dogs' }) {
  const [bets, setBets] = useState([])
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const chartRef = useRef(null)

  // Which of the two ledgers this skin is showing, and the other to hop to.
  const other = category === 'virtuals' ? 'dogs' : 'virtuals'
  const otherLabel = category === 'virtuals' ? 'Dogs' : 'Virtuals'

  useEffect(() => { getBets(category).then(setBets).catch(e => setError(e.message)) }, [category])

  // ── derived figures, same as the plain tracker ────────────────────────────
  const {
    cum, total, wagerCount, mtd, mtdCount, winrate, turnover, roi,
    spPts, evTotal, spBeat, measuredCount,
  } = useMemo(() => {
    const arr = [0]
    let t = 0
    for (const b of bets) { t += b.amount; arr.push(t) }
    // Tips are cash out of the till, not bets: they move the total but never the
    // bet count or win rate (see the plain tracker). Cash figures use `bets`,
    // counted figures use `wagers`.
    const wagers = bets.filter(b => !b.is_tip)
    const now = new Date()
    const inMonth = bets.filter(b => {
      const d = parseDate(b.created_at)
      return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear()
    })
    const settled = wagers.filter(b => b.amount !== 0)
    const wins = settled.filter(b => b.amount > 0).length
    const staked = bets.filter(b => b.stake != null)
    const to = staked.reduce((s, b) => s + b.stake, 0)
    const sp = staked.reduce((s, b) => s + b.amount, 0)

    // Accumulated EV in pounds, anchored to the bet it was earned ON — the
    // bet's index in the FULL ledger, not its index among priced bets, so the
    // line starts where that bet actually sits on the money curve's axis. Each
    // bet's EV adds to the last, so the slope is the edge per bet (a running
    // mean converges and then stops moving, hiding the recent hundred).
    const pts = []
    let ssum = 0, sn = 0
    bets.forEach((b, i) => {
      if (!hasEv(b)) return
      ssum += evOf(b); sn += 1
      pts.push([i + 1, ssum])
    })

    return {
      cum: arr,
      total: t,
      wagerCount: wagers.length,
      mtd: inMonth.reduce((s, b) => s + b.amount, 0),
      mtdCount: inMonth.filter(b => !b.is_tip).length,
      winrate: settled.length ? Math.round(100 * wins / settled.length) + '%' : '0%',
      turnover: to,
      roi: to > 0 ? (sp / to * 100).toFixed(1) + '%' : null,
      spPts: pts,
      evTotal: sn ? ssum : null,
      spBeat: bets.filter(b => hasEv(b) && b.price_taken > b.bfsp).length,
      measuredCount: sn,
    }
  }, [bets])

  // ── the cumulative chart, drawn hard-edged to suit the skin ───────────────
  const drawChart = useCallback(() => {
    const c = chartRef.current
    if (!c) return
    const rect = c.getBoundingClientRect()
    if (!rect.width) return
    const dpr = window.devicePixelRatio || 1
    c.width = rect.width * dpr
    c.height = rect.height * dpr
    const g = c.getContext('2d')
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    const W = rect.width, H = rect.height
    const padL = 4, padR = 42, padT = 8, padB = 16

    g.clearRect(0, 0, W, H)
    if (cum.length < 2) return
    // EV shares the money scale so the two read against each other.
    const evVals = spPts.map(p => p[1])
    let lo = Math.min(0, ...cum, ...evVals), hi = Math.max(0, ...cum, ...evVals)
    const sp = (hi - lo) || 100
    lo -= sp * 0.12; hi += sp * 0.12
    const X = i => padL + (W - padL - padR) * (i / (cum.length - 1))
    const Y = v => padT + (H - padT - padB) * (1 - (v - lo) / (hi - lo))

    // Gen 1 drew its screens as black line art on white — so the chart is black
    // ink on the panel, with Pikachu yellow the only fill.
    g.font = '9px ui-monospace, monospace'
    g.textBaseline = 'middle'
    const step = niceStep(hi - lo)
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      const y = Math.round(Y(v)) + 0.5
      const zero = Math.abs(v) < 1e-6
      g.strokeStyle = zero ? '#1b1b1b' : 'rgba(27,27,27,.18)'
      g.lineWidth = zero ? 2 : 1
      g.setLineDash(zero ? [] : [3, 3])
      g.beginPath(); g.moveTo(padL, y); g.lineTo(W - padR, y); g.stroke()
      g.setLineDash([])
      g.fillStyle = 'rgba(27,27,27,.55)'
      g.fillText(money(v), W - padR + 4, y)
    }

    // fill + curve
    g.beginPath()
    cum.forEach((v, i) => (i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))))
    g.lineTo(X(cum.length - 1), Y(0)); g.lineTo(X(0), Y(0)); g.closePath()
    g.fillStyle = total < 0 ? 'rgba(227,53,13,.16)' : 'rgba(255,203,5,.34)'
    g.fill()

    g.beginPath()
    cum.forEach((v, i) => (i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))))
    g.strokeStyle = '#1b1b1b'
    g.lineWidth = 3; g.lineJoin = 'miter'; g.stroke()

    // endpoint block — square, not a dot. Pikachu's cheek red.
    g.fillStyle = '#e3350d'
    g.fillRect(X(cum.length - 1) - 3, Y(cum[cum.length - 1]) - 3, 6, 6)

    // ── EV, overlaid on its own £ scale ─────────────────────────────────────
    // Its own scale even though both are pounds: EV accrues in pennies per bet
    // while the P&L swings in tens, so a shared axis would flatten it to zero.
    // Logo blue, dashed — distinct from the black money curve at a glance.
    if (spPts.length) {
      // Drawn on Y, the money scale — no scale of its own, so the two can be
      // read in proportion to each other.
      const SY = Y
      g.strokeStyle = '#2a75bb'
      g.lineWidth = 2
      g.setLineDash([4, 3])
      g.beginPath()
      spPts.forEach((p, i) => (i ? g.lineTo(X(p[0]), SY(p[1])) : g.moveTo(X(p[0]), SY(p[1]))))
      g.stroke()
      g.setLineDash([])

      const last = spPts[spPts.length - 1]
      g.fillStyle = '#2a75bb'
      g.fillRect(X(last[0]) - 2.5, SY(last[1]) - 2.5, 5, 5)
    }
  }, [cum, total, spPts])

  useEffect(() => { drawChart() }, [drawChart])
  // Observing the canvas rather than the window catches every box change — the
  // each-way toggle, the confirm box, a wrapped subline — not just window
  // resizes. Without it the canvas stretches via CSS while its drawing keeps an
  // older size, leaving a gap between the chart and the pad below.
  // Still coalesced + size-guarded: a phone's URL bar fires a storm of these.
  useEffect(() => {
    const c = chartRef.current
    if (!c) return
    let raf = 0, lastW = 0, lastH = 0
    const measure = () => {
      raf = 0
      const r = c.getBoundingClientRect()
      if (Math.abs(r.width - lastW) < 1 && Math.abs(r.height - lastH) < 1) return
      lastW = r.width; lastH = r.height
      drawChart()
    }
    const ro = new ResizeObserver(() => { if (!raf) raf = requestAnimationFrame(measure) })
    ro.observe(c)
    return () => { cancelAnimationFrame(raf); ro.disconnect() }
  }, [drawChart])

  async function handleAdd(payload) {
    setBusy(true); setError(null)
    try {
      const bet = await addBet(category, payload)
      setBets(bs => [...bs, bet])
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  async function undoLast() {
    if (!bets.length || busy) return
    setBusy(true); setError(null)
    try {
      await deleteBet(category, bets[bets.length - 1].id)
      setBets(bs => bs.slice(0, -1))
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  return (
    <div className={styles.page}>
      {/* ── cumulative P&L ─────────────────────────────────────── */}
      <section className={styles.top}>
        <div className={styles.eyebrow}>
          <span>Total profit</span><span className={styles.me}>Private</span>
        </div>

        <div className={styles.panel}>
          <div className={styles.stats}>
            <div className={styles.stat}>
              <div className={`${styles.figure} ${styles.total} ${total < 0 ? styles.neg : styles.pos}`}>
                {signed(total)}
              </div>
              <div className={styles.statCount}>{wagerCount} bet{wagerCount === 1 ? '' : 's'}</div>
            </div>
            <div className={styles.stat}>
              <div className={styles.statLabel}>Month to date</div>
              <div className={`${styles.figure} ${styles.mtd} ${mtd < 0 ? styles.neg : styles.pos}`}>
                {signed(mtd)}
              </div>
              <div className={styles.statCount}>{mtdCount} bet{mtdCount === 1 ? '' : 's'}</div>
            </div>
          </div>

          <div className={styles.subline}>
            <span><b>{winrate}</b> win</span>
            {roi !== null && <span><b>{roi}</b> ROI on {money(turnover)}</span>}
            {/* Doubles as the chart's legend — the dash matches the overlaid line. */}
            {evTotal !== null && (
              <span className={styles.spTag}>
                <b>{signed(evTotal)}</b> EV · {spBeat}/{measuredCount}
              </span>
            )}
            <span>last <b>{bets.length ? signed(bets[bets.length - 1].amount) : '—'}</b></span>
            <Link className={styles.chip} to={`/pnl/${other}/battle`}>{otherLabel}</Link>
            <Link className={styles.chip} to={`/pnl/${category}`}>plain</Link>
            {bets.length > 0 && (
              <>
                <a className={styles.chip} href={`/api/pnl/${category}/bets.csv`} download>csv</a>
                <button className={styles.chip} onClick={undoLast} disabled={busy}>undo</button>
              </>
            )}
          </div>
        </div>

        {error && <p className={styles.error}>{error}</p>}

        <div className={styles.chartFrame}>
          <canvas
            ref={chartRef}
            className={styles.chart}
            aria-label={`Cumulative profit over ${bets.length} bets, currently ${signed(total)}`}
          />
        </div>
      </section>

      <BattleEntry onAdd={handleAdd} busy={busy} category={category} />
    </div>
  )
}

function niceStep(range) {
  const raw = range / 4
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)))
  const n = raw / mag
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag
}

/** Same flow as the plain tracker: stake, odds, optional BFSP, lose/push/win. */
function BattleEntry({ onAdd, busy, category }) {
  // Virtuals has no BFSP (no Betfair market on a virtual race); it records
  // each-way instead, which doubles the outlay and adds a "place" result.
  const isVirtuals = category === 'virtuals'
  const [stake, setStake] = useState('')
  // The 2/1 seat is where the wheel rests, not a price the user chose —
  // `oddsTouched` keeps the two apart so an unspun wheel files nothing.
  const [odds, setOdds] = useState(DEFAULT_PRICE)
  const [oddsTouched, setOddsTouched] = useState(false)
  // Three states (see PnlPage): tracking (ghosted, follows the odds, files
  // nothing), matched (tapped — records the tracked price), set (spun to its
  // own). Tracking is the default: BFSP is only knowable after the off.
  const [bsp, setBsp] = useState(null)
  const [spOn, setSpOn] = useState(false)
  const [ew, setEw] = useState(false)
  const [terms, setTerms] = useState(DEFAULT_TERMS)
  // Losses are the common outcome, so start on 'lose': a losing bet is stake +
  // Add, no result tap. Win/push are one tap away.
  const [result, setResult] = useState('lose')
  const [confirming, setConfirming] = useState(false)

  const onOddsChange = useCallback(v => { setConfirming(false); setOdds(v) }, [])
  const onOddsTouch = useCallback(() => { setOddsTouched(true) }, [])
  // A spin is itself the assertion that the price is known.
  const onBspChange = useCallback(v => { setConfirming(false); setBsp(v); setSpOn(true) }, [])
  const onBspActivate = useCallback(() => { setConfirming(false); setSpOn(true) }, [])
  const onTermsChange = useCallback(v => { setConfirming(false); setTerms(v) }, [])

  const bspShown = bsp ?? odds
  const spNote = !spOn ? 'tracking odds · tap to record'
    : bsp == null ? 'recording · tap to stop'
    : 'set · tap to match odds'
  function cycleSp() {
    setConfirming(false)
    if (!spOn) setSpOn(true)
    else if (bsp == null) setSpOn(false)
    else setBsp(null)
  }

  const stakeV = parseFloat(stake)
  const hasStake = stake !== '' && stakeV > 0
  const { outlay, returned } = hasStake
    ? settle({ unit: stakeV, odds, result, ew: isVirtuals && ew, terms })
    : { outlay: null, returned: null }

  const canAdd = !busy && hasStake && result !== null
  const net = canAdd ? Math.round((returned - outlay) * 100) / 100 : null
  const big = net !== null && Math.abs(net) >= BIG_ENTRY

  function submit() {
    if (!canAdd) return
    if (big && !confirming) { setConfirming(true); return }
    // An untouched odds wheel files NEITHER price — its 2/1 seat is not a price
    // taken, and a BFSP "matching" it would inherit the same fiction.
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
    setStake(''); setOdds(DEFAULT_PRICE); setOddsTouched(false)
    setBsp(null); setSpOn(false); setResult('lose'); setConfirming(false)
  }

  return (
    <section className={styles.pad}>
      <div className={styles.frame}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>{isVirtuals && ew ? 'Stake (each way)' : 'Stake'}</span>
          <span className={styles.slotBox}>
            £
            <input
              value={stake}
              onChange={e => {
                setConfirming(false)
                setStake(e.target.value.replace(/[^\d.]/g, '').replace(/^0+(?=\d)/, '').slice(0, 9))
              }}
              onKeyDown={e => { if (e.key === 'Enter') submit() }}
              inputMode="decimal"
              type="text"
              placeholder="0.00"
              aria-label="Stake"
              autoComplete="off"
            />
          </span>
        </label>

        {isVirtuals && (
          <div className={styles.ewToggle} role="group" aria-label="Each way">
            <button
              type="button" className={styles.opt} aria-pressed={!ew}
              onClick={() => { setConfirming(false); setEw(false); if (result === 'place') setResult('lose') }}
            >Win only</button>
            <button
              type="button" className={styles.opt} aria-pressed={ew}
              onClick={() => { setConfirming(false); setEw(true) }}
            >E/W</button>
          </div>
        )}

        {/* Above the wheels for the same reason as the plain tracker: the
            wheels are the tallest thing here, and underneath them the result
            row sat in the bottom inch of the screen, where the phone's browser
            toolbar is. */}
        <div className={styles.menu} role="group" aria-label="Result">
          {(isVirtuals && ew
            ? [['lose', 'Lose'], ['place', 'Place'], ['win', 'Win']]
            : [['lose', 'Lose'], ['push', 'Push'], ['win', 'Win']]
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`${styles.opt} ${styles[key]}`}
              aria-pressed={result === key}
              onClick={() => { setConfirming(false); setResult(key) }}
            >{label}</button>
          ))}
        </div>

        <div className={isVirtuals && !ew ? styles.wheelsOne : styles.wheels}>
          <OddsWheel
            id="gbOdds" label="Odds" value={odds}
            onChange={onOddsChange} onTouch={onOddsTouch}
          />
          {isVirtuals ? (ew && (
            <div className={styles.wheelCol}>
              <OddsWheel
                id="gbTerms" label="Place terms" value={terms}
                onChange={onTermsChange} ladder={TERMS_LADDER}
              />
            </div>
          )) : (
          <div className={styles.wheelCol}>
            {/* Tracks the odds taken; one tap records that price. */}
            <OddsWheel
              id="gbBsp" label="BFSP"
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
            vs SP <b className={odds > bspShown ? styles.pos : odds < bspShown ? styles.neg : ''}>
              {pct(vsSpPct(odds, bspShown))}
            </b>
          </div>
        )}

        <div className={styles.netLine}>
          <span>Net</span>
          <b className={net === null ? '' : net > 0 ? styles.pos : net < 0 ? styles.neg : ''}>
            {net === null ? '—' : exact(net)}
          </b>
        </div>

        {confirming ? (
          <div className={styles.confirmBox} role="alertdialog" aria-live="assertive">
            <p className={styles.confirmQ}>
              Record <b className={net > 0 ? styles.pos : styles.neg}>{exact(net)}</b>?
            </p>
            <div className={styles.confirmBtns}>
              <button type="button" className={styles.cancel} onClick={() => setConfirming(false)}>Cancel</button>
              <button type="button" className={styles.confirm} onClick={submit} disabled={!canAdd}>Yes</button>
            </div>
          </div>
        ) : (
          <button type="button" className={styles.confirm} onClick={submit} disabled={!canAdd}>
            {busy ? 'Saving…' : big ? `Enter ${exact(net)}…` : 'Enter'}
          </button>
        )}
      </div>
    </section>
  )
}
