import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getCoupons, uploadCoupon, getUploadOptions, getCouponPeriods, getUploadStatus } from '../api'
import styles from './UploadPage.module.css'


function fmtDate(d) {
  if (!d) return ''
  const dt = new Date(d)
  return dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

// A step running this long is past anything a normal read takes: say so.
const SLOW_SECONDS = { read: 120, verify: 150, price: 60 }
const MARK = { done: '✓', failed: '✕', running: '', todo: '', skipped: '–' }

/** The read's steps, live: what is done, what is running and for how long. */
function ReadSteps({ progress }) {
  const steps = progress?.steps ?? [{ key: 'saved', label: 'Uploading photo', state: 'running' }]
  return (
    <ol className={styles.steps}>
      {steps.map(s => {
        const slow = s.state === 'running' && s.seconds > (SLOW_SECONDS[s.key] ?? 60)
        return (
          <li key={s.key} className={`${styles.step} ${styles[s.state] ?? ''}`}>
            <span className={styles.stepMark} aria-hidden="true">
              {s.state === 'running' ? <span className={styles.spinner} /> : MARK[s.state]}
            </span>
            <span className={styles.stepLabel}>
              {s.label}
              {s.note && <span className={styles.stepNote}> · {s.note}</span>}
              {slow && <span className={styles.stepSlow}> · slower than usual</span>}
            </span>
            {s.seconds != null && s.state !== 'skipped' &&
              <span className={styles.stepTime}>{Math.round(s.seconds)}s</span>}
          </li>
        )
      })}
    </ol>
  )
}

export default function UploadPage() {
  const navigate = useNavigate()
  // OCR auto-confirms — rows save and price on upload, so there is no review
  // phase. Corrections happen inline on the tracker afterwards.
  const [phase, setPhase] = useState('form') // form | loading | failed | done
  const [error, setError] = useState(null)
  // The read's live steps (GET /upload/{id}/status) and whether the last poll
  // reached the server — shown, so a dead read can never just spin.
  const [progress, setProgress] = useState(null)
  const [offline, setOffline] = useState(false)

  // Form state
  // Types come from the API so this form and the tracker cannot drift.
  const [types, setTypes] = useState([])
  const [typeCode, setTypeCode] = useState('')
  // Coupons run on fixed windows, so the period is chosen and its dates are
  // derived — no free date entry.
  const [periods, setPeriods] = useState([])
  const [period, setPeriod] = useState('')
  const [file, setFile] = useState(null)
  const [dragOver, setDragOver] = useState(false)

  // Outcome of the last upload, shown on the done screen
  const [savedCount, setSavedCount] = useState(0)

  // Previous uploads list
  const [prevCoupons, setPrevCoupons] = useState([])

  useEffect(() => {
    getCoupons().then(setPrevCoupons).catch(() => {})
    getUploadOptions().then(ts => {
      setTypes(ts)
      if (ts.length > 0) setTypeCode(prev => prev || ts[0].code)
    }).catch(() => {})
  }, [])

  // Periods are per type — GG coupons run midweek/weekend on moving dates,
  // the priced coupons run the fixed weekly schedule.
  useEffect(() => {
    if (!typeCode) return
    getCouponPeriods(typeCode).then(ps => {
      setPeriods(ps)
      setPeriod((ps.find(p => p.suggested) ?? ps[0])?.code ?? '')
    }).catch(() => {})
  }, [typeCode])

  async function handleProcess() {
    if (!file) return
    setError(null)
    setProgress(null)
    setOffline(false)
    setPhase('loading')
    let result
    try {
      // Returns as soon as the coupon is SAVED; reading it takes about a minute
      // and happens server-side, so nothing here holds the connection open.
      result = await uploadCoupon(typeCode, period, file)
    } catch (e) {
      setError(e.message)
      setPhase('form')
      // It may have saved even so; refresh the list rather than invite a duplicate.
      getCoupons().then(setPrevCoupons).catch(() => {})
      return
    }
    const end = await waitForCoupon(result.coupon_id)
    getCoupons().then(setPrevCoupons).catch(() => {})
    if (end.status === 'confirmed') {
      setSavedCount(end.legs)
      setPhase('done')
      setTimeout(() => navigate('/'), 1200)
    } else {
      setPhase('failed')
    }
  }

  /**
   * Poll a freshly-uploaded coupon's read until it ends, drawing each step.
   *
   * The upload response carries no legs — the read runs after it. Each poll is
   * short, so a dropped connection costs one retry, and is SAID (offline) rather
   * than swallowed. It ends on confirmed, failed, or stalled (nothing is
   * reading it any more), never on a timer: a slow read is shown as slow.
   */
  async function waitForCoupon(couponId, gapMs = 1500) {
    for (;;) {
      await new Promise(r => setTimeout(r, gapMs))
      let s
      try {
        s = await getUploadStatus(couponId)
      } catch {
        setOffline(true)
        continue
      }
      setOffline(false)
      setProgress(s)
      if (s.status !== 'processing' || s.stalled) return s
    }
  }

  function handleDrop(e) {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer?.files?.[0]
    if (f) setFile(f)
  }

  if (phase === 'loading' || phase === 'failed') {
    const failed = phase === 'failed'
    return (
      <div className={styles.page}>
        <div className={styles.statusScreen}>
          <p className={styles.loadingMsg}>{failed ? 'Coupon not saved' : 'Reading coupon…'}</p>
          <ReadSteps progress={progress} />
          {failed
            ? <>
                <p className={styles.readError}>{progress?.error ?? 'The read failed.'}</p>
                <button className={styles.btnPrimary} style={{ maxWidth: 260 }}
                  onClick={() => { setPhase('form'); setProgress(null) }}>
                  TRY AGAIN
                </button>
              </>
            : <p className={styles.loadingSub}>
                {offline
                  ? 'Lost touch with the server, retrying… The read carries on regardless.'
                  : 'Takes about a minute. It keeps going if you leave this page.'}
              </p>}
        </div>
      </div>
    )
  }

  if (phase === 'done') {
    return (
      <div className={styles.page}>
        <div className={styles.statusScreen}>
          <p className={styles.loadingMsg}>
            ✓ {savedCount} leg{savedCount !== 1 ? 's' : ''} saved &amp; priced
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className={styles.page}>
      <div className={styles.sectionHeader}>
        <span className={styles.sectionTitle}>UPLOAD COUPON</span>
      </div>

      {error && <p className={styles.error}>{error}</p>}

      <div className={styles.form}>
        <label className={styles.label}>Coupon</label>
        <select className={styles.select} value={typeCode} onChange={e => setTypeCode(e.target.value)}>
          {types.map(t => (
            <option key={t.code} value={t.code}>{t.label}</option>
          ))}
        </select>
        {/* The two multi-market sheets are offered as SHEETS: the photo is read
            whole and every market on it becomes its own coupon, so asking which
            one the photo is "of" has no right answer. */}
        {types.find(t => t.code === typeCode)?.types?.length > 1 && (
          <p className={styles.hint}>
            One photo fills all {types.find(t => t.code === typeCode).types.length} markets on this sheet.
          </p>
        )}

        <label className={styles.label}>Coupon period</label>
        <select className={styles.select} value={period} onChange={e => setPeriod(e.target.value)}>
          {periods.map(p => (
            <option key={p.code} value={p.code}>{p.label}</option>
          ))}
        </select>

        <label className={styles.label}>Coupon image</label>
        <div
          className={`${styles.dropZone} ${dragOver ? styles.dragOver : ''} ${file ? styles.hasFile : ''}`}
          onDragOver={e => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => document.getElementById('fileInput').click()}
        >
          {file
            ? <span className={styles.fileName}>{file.name}</span>
            : <span className={styles.dropText}>Tap or drag image here</span>
          }
          <input
            id="fileInput"
            type="file"
            accept="image/*"
            style={{ display: 'none' }}
            onChange={e => setFile(e.target.files?.[0] ?? null)}
          />
        </div>

        <button
          className={styles.btnPrimary}
          onClick={handleProcess}
          disabled={!file || !typeCode || !period}
        >
          PROCESS IMAGE
        </button>
      </div>

      {prevCoupons.length > 0 && (
        <>
          <div className={styles.sectionHeader} style={{ marginTop: 12 }}>
            <span className={styles.sectionTitle}>PREVIOUS UPLOADS</span>
          </div>
          <div className={styles.prevList}>
            {prevCoupons.map(c => (
              <div key={c.id} className={styles.prevRow}>
                <div className={styles.prevInfo}>
                  <span className={styles.prevName}>{c.name}</span>
                  <span className={styles.prevMeta}>{fmtDate(c.match_day)} · {c.leg_count} legs</span>
                </div>
                <span className={`${styles.statusChip} ${c.status === 'confirmed' ? styles.live : styles.pending}`}>
                  {c.status.toUpperCase()}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
