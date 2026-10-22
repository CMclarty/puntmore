import { useState, useEffect, useCallback, useRef } from 'react'
import { getAwSlips, uploadAwSlip, editAwSlip, deleteAwSlip } from '../api'
import useAutoRefresh from '../hooks/useAutoRefresh'
import styles from './Aw26Page.module.css'

/**
 * AW 26 — every slip the acca project places at the Paddy terminal, against
 * £1,000 EV a day and £100,000 profit. A photo of the terminal is read and
 * saved on upload; its EV is fixed once the day's fairs arrive (Bookie
 * Bashing, via the local script) and it settles from its legs' results.
 * One pot for the team: everyone with the grant sees every slip.
 */

const OFFERS = [['refund', 'Refund'], ['boost', 'Boost'], ['other', 'Other']]
const RESULTS = [['pending', '–'], ['won', 'W'], ['lost', 'L'], ['void', 'V']]
const OFFER_KEY = 'aw26.offer'

const gbp = v => (v < 0 ? '−' : '') + '£' + Math.abs(v).toLocaleString('en-GB', { maximumFractionDigits: 0 })
const gbp2 = v => (v < 0 ? '−' : v > 0 ? '+' : '') + '£' + Math.abs(v).toFixed(2)
const tone = v => (v > 0 ? styles.pos : v < 0 ? styles.neg : '')
const when = iso => {
  const d = new Date(/[Z+]/.test(iso) ? iso : iso + 'Z')
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) + ' '
    + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}
// A totals leg in a few characters ("Corners O9.5"), so the line is never what
// gets cut off; anything else as its selection, then its market.
const KIND = { corners: 'Corners', cards: 'Cards', goals: 'Goals', saves: 'Saves' }
const legText = l => (KIND[l.kind] && l.line != null
  ? `${KIND[l.kind]} ${l.side === 'under' ? 'U' : 'O'}${l.line}`
  : [l.selection, l.market].filter(Boolean).join(' · '))

function readOffer() {
  try { const v = localStorage.getItem(OFFER_KEY); return OFFERS.some(([k]) => k === v) ? v : 'refund' } catch { return 'refund' }
}

export default function Aw26Page() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [offer, setOfferState] = useState(readOffer)
  const [open, setOpen] = useState(null)       // slip id being edited
  const file = useRef(null)

  const setOffer = v => {
    setOfferState(v)
    try { localStorage.setItem(OFFER_KEY, v) } catch { /* private mode */ }
  }

  const load = useCallback(() => {
    // An edit in progress is never overwritten by a refresh under it.
    if (open != null) return
    getAwSlips().then(d => { setData(d); setError(null) }).catch(e => setError(e.message))
  }, [open])
  useEffect(() => { load() }, [load])
  useAutoRefresh(load)

  async function onFile(e) {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    setBusy(true); setError(null)
    try {
      await uploadAwSlip(offer, f)
      setData(await getAwSlips())
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  const s = data?.summary
  return (
    <div className={styles.page}>
      {s && (
        <section className={styles.head}>
          <div className={styles.figures}>
            <div className={styles.stat}>
              <div className={styles.label}>EV today</div>
              <div className={styles.big}>
                <span className={tone(s.today.ev)}>{gbp(s.today.ev)}</span>
                <span className={styles.of}> / {gbp(s.targets.ev_per_day)}</span>
              </div>
            </div>
            <div className={styles.stat}>
              <div className={styles.label}>Profit</div>
              <div className={styles.big}>
                <span className={tone(s.total.profit)}>{gbp(s.total.profit)}</span>
                <span className={styles.of}> / {gbp(s.targets.profit)}</span>
              </div>
            </div>
          </div>
          <div className={styles.line}>
            EV to date <b>{gbp(s.total.ev)}</b> / {gbp(s.total.ev_target)} · {s.total.days} day{s.total.days === 1 ? '' : 's'}
          </div>
          <div className={styles.line}>
            Today <b>{s.today.slips}</b> slips · <b>{s.today.legs}</b> legs · <b>{gbp(s.today.staked)}</b>
            {' '}· To date <b>{s.total.slips}</b> · <b>{gbp(s.total.staked)}</b>
            {s.total.open > 0 && <> · <b>{s.total.open}</b> open</>}
            {s.total.unpriced > 0 && <> · <b>{s.total.unpriced}</b> unpriced</>}
          </div>
          {s.today.settled_slips > 0 && (
            <div className={styles.line}>
              Settled today <span className={`${styles.sval} ${tone(s.today.settled)}`}>{gbp2(s.today.settled)}</span>
              <span className={styles.muted}> · {s.today.settled_slips} slip{s.today.settled_slips === 1 ? '' : 's'}</span>
            </div>
          )}
        </section>
      )}

      <section className={styles.add}>
        <div className={styles.chips} role="group" aria-label="Offer">
          {OFFERS.map(([k, label]) => (
            <button key={k} type="button" aria-pressed={offer === k}
              className={`${styles.chip} ${offer === k ? styles.on : ''}`}
              onClick={() => setOffer(k)}>{label}</button>
          ))}
        </div>
        <button type="button" className={styles.addBtn} disabled={busy} onClick={() => file.current?.click()}>
          {busy ? 'Reading…' : 'Add slip'}
        </button>
        <input ref={file} type="file" accept="image/*" capture="environment" hidden onChange={onFile} />
      </section>
      {error && <p className={styles.error}>{error}</p>}

      <ul className={styles.list}>
        {data?.slips.map(slip => (
          open === slip.id
            ? <SlipEditor key={slip.id} slip={slip}
                onDone={async updated => {
                  setOpen(null)
                  if (updated) setData(await getAwSlips())
                }}
                onError={setError} />
            : <SlipRow key={slip.id} slip={slip} onOpen={() => setOpen(slip.id)} />
        ))}
      </ul>
    </div>
  )
}

function outcome(slip) {
  if (slip.status === 'open') return <span className={styles.muted}>open</span>
  const p = slip.returns - slip.stake
  return <span className={tone(p)}>{gbp2(p)}</span>
}

function SlipRow({ slip, onOpen }) {
  return (
    <li className={styles.slip}>
      <button type="button" className={styles.slipBtn} onClick={onOpen}>
        <div className={styles.slipTop}>
          <span className={styles.when}>{when(slip.placed_at)}</span>
          <span className={styles.offer}>{slip.offer}</span>
          <span className={styles.num}>{slip.stake != null ? '£' + slip.stake.toFixed(2) : '£—'}</span>
          <span className={`${styles.num} ${slip.ev != null ? tone(slip.ev) : styles.muted}`}>
            {slip.ev != null ? 'EV ' + gbp2(slip.ev) : 'EV —'}
          </span>
          <span className={styles.num}>{outcome(slip)}</span>
        </div>
        {slip.legs.map(l => (
          <div key={l.id} className={styles.leg}>
            <span className={`${styles.res} ${l.result === 'won' ? styles.resWon : l.result === 'lost' ? styles.resLost : ''}`}>
              {RESULTS.find(([k]) => k === l.result)?.[1]}
            </span>
            <span className={styles.ev}>{l.event}</span>
            <span className={styles.mk}>{legText(l)}</span>
            <span className={styles.num}>{l.odds != null ? l.odds.toFixed(2) : '—'}</span>
            <span className={`${styles.num} ${styles.muted}`}>{l.fair_odds != null ? 'fair ' + l.fair_odds.toFixed(2) : 'fair —'}</span>
          </div>
        ))}
        {slip.ev_note && <div className={styles.note}>{slip.ev_note}</div>}
      </button>
    </li>
  )
}

function SlipEditor({ slip, onDone, onError }) {
  const [offer, setOffer] = useState(slip.offer)
  const [stake, setStake] = useState(slip.stake ?? '')
  const [legs, setLegs] = useState(slip.legs.map(l => ({ ...l })))
  const [saving, setSaving] = useState(false)
  const [sure, setSure] = useState(false)

  const setLeg = (i, k, v) => setLegs(ls => ls.map((l, j) => (j === i ? { ...l, [k]: v } : l)))
  const num = v => (v === '' || v == null ? null : Number(v))

  async function save() {
    setSaving(true)
    try {
      await editAwSlip(slip.id, {
        offer, stake: num(stake),
        legs: legs.map(l => ({
          id: l.id, event: l.event, market: l.market, selection: l.selection,
          odds: num(l.odds), fair_odds: num(l.fair_odds), result: l.result,
        })),
      })
      onDone(true)
    } catch (e) { onError(e.message); setSaving(false) }
  }

  async function remove() {
    if (!sure) { setSure(true); return }
    try { await deleteAwSlip(slip.id); onDone(true) } catch (e) { onError(e.message) }
  }

  return (
    <li className={`${styles.slip} ${styles.editing}`}>
      <div className={styles.editTop}>
        <div className={styles.chips} role="group" aria-label="Offer">
          {OFFERS.map(([k, label]) => (
            <button key={k} type="button" aria-pressed={offer === k}
              className={`${styles.chip} ${offer === k ? styles.on : ''}`}
              onClick={() => setOffer(k)}>{label}</button>
          ))}
        </div>
        <label className={styles.field}>
          <span className={styles.label}>Stake</span>
          <input inputMode="decimal" value={stake} onChange={e => setStake(e.target.value)} aria-label="Stake" />
        </label>
      </div>
      {legs.map((l, i) => (
        <div key={l.id ?? i} className={styles.editLeg}>
          <input className={styles.wide} value={l.event} onChange={e => setLeg(i, 'event', e.target.value)} aria-label="Event" />
          <input className={styles.wide} value={l.market ?? ''} onChange={e => setLeg(i, 'market', e.target.value)} aria-label="Market" />
          <input className={styles.wide} value={l.selection ?? ''} onChange={e => setLeg(i, 'selection', e.target.value)} aria-label="Selection" />
          <div className={styles.legNums}>
            <label className={styles.field}>
              <span className={styles.label}>Odds</span>
              <input inputMode="decimal" value={l.odds ?? ''} onChange={e => setLeg(i, 'odds', e.target.value)} />
            </label>
            <label className={styles.field}>
              <span className={styles.label}>Fair</span>
              <input inputMode="decimal" value={l.fair_odds ?? ''} onChange={e => setLeg(i, 'fair_odds', e.target.value)} />
            </label>
            <div className={styles.chips} role="group" aria-label="Result">
              {RESULTS.map(([k, label]) => (
                <button key={k} type="button" aria-pressed={l.result === k}
                  className={`${styles.chip} ${l.result === k ? styles.on : ''}`}
                  onClick={() => setLeg(i, 'result', k)}>{label}</button>
              ))}
            </div>
          </div>
        </div>
      ))}
      <div className={styles.actions}>
        {slip.has_image && <a className={styles.link} href={`/api/aw26/slips/${slip.id}/image`} target="_blank" rel="noreferrer">Photo</a>}
        <button type="button" className={styles.danger} onClick={remove}>{sure ? 'Delete it' : 'Delete'}</button>
        <button type="button" className={styles.ghost} onClick={() => onDone(false)}>Cancel</button>
        <button type="button" className={styles.save} onClick={save} disabled={saving}>Save</button>
      </div>
    </li>
  )
}
