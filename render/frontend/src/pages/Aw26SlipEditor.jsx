import { useState } from 'react'
import { editAwSlip, deleteAwSlip } from '../api'
import styles from './Aw26Page.module.css'

// One slip's editor, moved as it stood from the old AW 26 page; the Slips
// screen (Task 9) mounts and restyles it.

const OFFERS = [['refund', 'Refund'], ['boost', 'Boost'], ['other', 'Other']]
const RESULTS = [['pending', '–'], ['won', 'W'], ['lost', 'L'], ['void', 'V']]

export default function SlipEditor({ slip, onDone, onError }) {
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
