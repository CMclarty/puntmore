import { Fragment, useState, useEffect, useRef, useCallback } from 'react'
import { getAwSlips, getAwPhotos, uploadAwPhotos, resendAwPhoto, deleteAwPhoto } from '../api'
import useAutoRefresh, { REFRESH_MS } from '../hooks/useAutoRefresh'
import { money, pounds, pickText, openTo, flagged, koTime, koDay, mark } from '../aw26'
import f from './CardsPage.module.css'
import s from './Aw26Page.module.css'
import SlipEditor from './Aw26SlipEditor'

// AW 26's Slips screen: the upload, the photos waiting on Qwen, then the
// chosen day's slips, newest first. A tap on a slip opens its editor.

const WORD = { W: 'Won', L: 'Lost', V: 'Void', '–': '–', '?': '?' }
const TONE = { W: s.won, L: s.lost, '–': s.none }
const QUICK_MS = 20_000   // while a photo waits on Qwen
const reading = p => p.state === 'waiting' || p.state === 'reading'
const odds = n => (n == null ? '—' : n.toFixed(2))
const stake = n => (n == null || n % 1 === 0 ? pounds(n) : `£${n.toFixed(2)}`)
const signed = n => (n > 0 ? s.good : n < 0 ? s.bad : '')

function Photo({ photo, act }) {
  if (reading(photo)) {
    return <div className={`${f.row} ${s.photo}`}><span className={s.photoText}>Waiting for Qwen</span></div>
  }
  const failed = photo.state === 'failed'
  return (
    <div className={`${f.row} ${s.photo}`}>
      <span className={f.what}>
        {failed && <span className={s.photoText}>Qwen could not read this</span>}
        <span className={failed ? f.match : s.photoText}>{photo.reason}</span>
      </span>
      <span className={s.photoActs}>
        {failed && <button type="button" className={f.chip} onClick={() => act(resendAwPhoto, photo.id)}>Send again</button>}
        <button type="button" className={f.chip} onClick={() => act(deleteAwPhoto, photo.id)}>{failed ? 'Delete' : 'Dismiss'}</button>
      </span>
    </div>
  )
}

function Slip({ slip, day, now, first, onOpen }) {
  const to = openTo(slip)
  const profit = slip.returns != null && slip.stake != null ? slip.returns - slip.stake : null
  return (
    <li>
      <button type="button" className={s.slipBtn} onClick={onOpen}>
        <span className={s.slipHead}>
          <span>
            <b>{slip.offer.charAt(0).toUpperCase() + slip.offer.slice(1)}</b> {koTime(slip.placed_at)} · {stake(slip.stake)}
          </span>
          <span>
            {flagged(slip) ? <span className={s.note}>{slip.ev_note}</span> : `EV ${money(slip.ev, true)}`}
            {slip.status === 'open'
              ? ` · open${to ? ` to ${to}` : ''}`
              : <> · <b className={signed(profit)}>{money(profit, true)}</b></>}
          </span>
        </span>
        {first && (
          <span className={`${f.row} ${f.colHead} ${s.leg}`}>
            <span>Leg</span><span>Odds</span><span>Fair</span><span>Result</span>
          </span>
        )}
        {slip.legs.map(l => {
          const away = koDay(l.kickoff, day)
          const m = mark(l, now)
          return (
            <span key={l.id} className={`${f.row} ${s.leg}`}>
              <span className={f.what}>
                <span className={f.pick}>{pickText(l)}</span>
                <span className={f.match}>{l.event}{away && ` · ${away}`}</span>
              </span>
              <span className={f.odds}>{odds(l.odds)}</span>
              <span className={f.fair}>{odds(l.fair_odds)}{l.fair_assumed && '~'}</span>
              <span className={`${s.legResult} ${TONE[m] ?? ''}`}>{WORD[m]}</span>
            </span>
          )
        })}
      </button>
    </li>
  )
}

export default function Aw26Slips({ day, today, onChange, onCount }) {
  const [data, setData] = useState(null)       // { day, slips, photos }: the last good load
  const [error, setError] = useState(null)     // the last load failed
  const [failed, setFailed] = useState(null)   // the last action failed: { at: 'queue' | slip id, message }
  const [open, setOpen] = useState(null)       // the slip whose editor is open
  const [now, setNow] = useState(() => Date.now())
  const [sending, setSending] = useState(false)
  const picker = useRef(null)

  // Load the day's slips and the queue together; only the latest load lands.
  // A reload that finds fewer photos queued tells the page, whose figures
  // follow the slips Qwen has recorded.
  const live = useRef(true)
  const seq = useRef(0)
  const queued = useRef(null)
  const load = useCallback(() => {
    const id = ++seq.current
    return Promise.all([getAwSlips(day), getAwPhotos()])
      .then(([{ slips }, queue]) => {
        if (!live.current || id !== seq.current) return
        const photos = queue.filter(p => p.state !== 'done')
        if (queued.current != null && photos.length < queued.current) onChange()
        queued.current = photos.length
        setData({ day, slips, photos })
        setError(null)
        setNow(Date.now())
        onCount(slips.length)
      })
      .catch(err => {
        if (!live.current || id !== seq.current) return
        setError(err.message)
        onCount(null)
      })
  }, [day, onChange, onCount])
  useEffect(() => {
    live.current = true
    load()
    return () => { live.current = false }
  }, [load])

  // A failed load hides the list; a load for another day shows nothing until it lands.
  const shown = !error && data?.day === day ? data : null
  const editing = shown?.slips.some(sl => sl.id === open)
  const waiting = shown?.photos.some(reading)
  // One timer: every 20 s while a photo waits on Qwen, else every 2 min. An
  // edit in progress is never reloaded under.
  useAutoRefresh(() => { if (!editing) load() }, { everyMs: waiting ? QUICK_MS : REFRESH_MS })

  // An action that fails leaves the screen as it is and says why where it was taken.
  const act = (call, id) => {
    setFailed(null)
    return call(id).then(load, err => setFailed({ at: 'queue', message: err.message }))
  }
  const upload = e => {
    const files = [...e.target.files]
    e.target.value = ''   // the same photos can be chosen again
    if (!files.length) return
    setFailed(null)
    setSending(true)
    uploadAwPhotos(files)
      .then(load, err => setFailed({ at: 'queue', message: err.message }))
      .finally(() => setSending(false))
  }
  const done = updated => {
    setOpen(null)
    setFailed(null)
    if (updated) { load(); onChange() }
  }

  const chosen = day ?? today
  return (
    <div className={`${f.list} ${s.slipsView}`}>
      <div className={s.upload}>
        <button type="button" className={f.chip} disabled={sending} onClick={() => picker.current.click()}>Upload slips</button>
        <input ref={picker} type="file" accept="image/*" multiple hidden onChange={upload} />
      </div>
      {failed?.at === 'queue' && <p className={f.error}>{failed.message}</p>}
      {error && <p className={f.error}>{error}</p>}
      {!error && !shown && <p className={f.empty}>Loading…</p>}
      {shown && (
        <>
          {shown.photos.map(p => <Photo key={p.id} photo={p} act={act} />)}
          {shown.slips.length ? (
            <ul className={s.slipList}>
              {shown.slips.map((slip, i) => (
                open === slip.id ? (
                  <Fragment key={slip.id}>
                    {failed?.at === slip.id && <li className={f.error}>{failed.message}</li>}
                    <SlipEditor slip={slip} onDone={done}
                      onError={message => setFailed({ at: slip.id, message })} />
                  </Fragment>
                ) : (
                  <Slip key={slip.id} slip={slip} day={chosen} now={now} first={i === 0}
                    onOpen={() => { setOpen(slip.id); setFailed(null) }} />
                )
              ))}
            </ul>
          ) : <p className={f.empty}>No slips.</p>}
        </>
      )}
    </div>
  )
}
