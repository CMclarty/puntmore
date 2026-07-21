import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getCoupons, uploadCoupon } from '../api'
import styles from './UploadPage.module.css'

// The seven coupon types the syndicate actually plays. Codes follow the
// existing convention (market + line, no decimal point) and are stored on
// Coupon.type_code as a label — pricing keys off each leg's pick text, not
// this, so adding a type here needs no backend change.
const TYPE_CODES = [
  { value: 'TTS1', label: 'Team to Score 1' },
  { value: 'TTS2', label: 'Team to Score 2' },
  { value: 'TTS3', label: 'Team to Score 3' },
  { value: 'WO15', label: 'Win and Over 1.5' },
  { value: 'WO25', label: 'Win and Over 2.5' },
  { value: 'GGB',  label: 'GG Bonus' },
  { value: 'GGND', label: 'GG No Draw' },
]

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
  const [typeCode, setTypeCode] = useState('TTS2')
  const [matchDay, setMatchDay] = useState(new Date().toISOString().slice(0, 10))
  const [file, setFile] = useState(null)
  const [dragOver, setDragOver] = useState(false)

  // Outcome of the last upload, shown on the done screen
  const [savedCount, setSavedCount] = useState(0)
  const [flaggedCount, setFlaggedCount] = useState(0)

  // Previous uploads list
  const [prevCoupons, setPrevCoupons] = useState([])

  useEffect(() => {
    getCoupons().then(setPrevCoupons).catch(() => {})
  }, [])

  async function handleProcess() {
    if (!file) return
    setError(null)
    setPhase('loading')
    try {
      const result = await uploadCoupon(typeCode, matchDay, file)
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
          {TYPE_CODES.map(t => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>

        <label className={styles.label}>Match day</label>
        <input
          className={styles.input}
          type="date"
          value={matchDay}
          onChange={e => setMatchDay(e.target.value)}
        />

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
          disabled={!file || !typeCode || !matchDay}
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
