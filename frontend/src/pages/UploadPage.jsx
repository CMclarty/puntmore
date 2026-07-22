import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getCoupons, uploadCoupon, getCouponTypes, getCouponPeriods } from '../api'
import styles from './UploadPage.module.css'


function fmtDate(d) {
  if (!d) return ''
  const dt = new Date(d)
  return dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

export default function UploadPage() {
  const navigate = useNavigate()
  // OCR auto-confirms — rows save and price on upload, so there is no review
  // phase. Corrections happen inline on the tracker afterwards.
  const [phase, setPhase] = useState('form') // form | loading | done
  const [error, setError] = useState(null)

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
  const [flaggedCount, setFlaggedCount] = useState(0)

  // Previous uploads list
  const [prevCoupons, setPrevCoupons] = useState([])

  useEffect(() => {
    getCoupons().then(setPrevCoupons).catch(() => {})
    getCouponTypes().then(ts => {
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
    setPhase('loading')
    try {
      const result = await uploadCoupon(typeCode, period, file)
      setSavedCount(result.legs.length)
      setFlaggedCount(result.legs.filter(l => l.flags?.length).length)
      setPhase('done')
      getCoupons().then(setPrevCoupons).catch(() => {})
      setTimeout(() => navigate('/'), 1200)
    } catch (e) {
      setError(e.message)
      setPhase('form')
    }
  }

  function handleDrop(e) {
    e.preventDefault()
    setDragOver(false)
    const f = e.dataTransfer?.files?.[0]
    if (f) setFile(f)
  }

  if (phase === 'loading') {
    return (
      <div className={styles.page}>
        <div className={styles.statusScreen}>
          <p className={styles.loadingMsg}>Reading coupon…</p>
          <p className={styles.loadingSub}>Rows save and price automatically.</p>
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
          {flaggedCount > 0 && (
            <p className={styles.loadingSub}>
              ⚠ {flaggedCount} need{flaggedCount === 1 ? 's' : ''} a look — correct on the tracker.
            </p>
          )}
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
        <label className={styles.label}>Coupon type</label>
        <select className={styles.select} value={typeCode} onChange={e => setTypeCode(e.target.value)}>
          {types.map(t => (
            <option key={t.code} value={t.code}>{t.label}</option>
          ))}
        </select>

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
