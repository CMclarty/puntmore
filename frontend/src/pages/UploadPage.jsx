import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getCoupons, uploadCoupon, confirmUpload } from '../api'
import styles from './UploadPage.module.css'

const TYPE_CODES = [
  { value: 'TTS2', label: 'Team to Score 2+' },
  { value: 'TTS3', label: 'Team to Score 3+' },
  { value: 'WO25', label: 'Win and Over 2.5' },
  { value: 'WO35', label: 'Win and Over 3.5' },
  { value: 'BTTS', label: 'Both Teams to Score' },
  { value: 'O25',  label: 'Over 2.5 Goals' },
  { value: 'O35',  label: 'Over 3.5 Goals' },
  { value: 'AH05', label: 'Asian Handicap -0.5' },
  { value: 'DNB',  label: 'Draw No Bet' },
]

function fmtDate(d) {
  if (!d) return ''
  const dt = new Date(d)
  return dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

export default function UploadPage() {
  const navigate = useNavigate()
  const [phase, setPhase] = useState('form') // form | loading | review | confirming | done
  const [error, setError] = useState(null)

  // Form state
  const [typeCode, setTypeCode] = useState('TTS2')
  const [matchDay, setMatchDay] = useState(new Date().toISOString().slice(0, 10))
  const [file, setFile] = useState(null)
  const [dragOver, setDragOver] = useState(false)

  // Review state
  const [couponId, setCouponId] = useState(null)
  const [rows, setRows] = useState([])

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
      setCouponId(result.coupon_id)
      setRows(result.rows.map((r, i) => ({ ...r, key: i })))
      setPhase('review')
    } catch (e) {
      setError(e.message)
      setPhase('form')
    }
  }

  async function handleConfirm() {
    setError(null)
    setPhase('confirming')
    try {
      await confirmUpload(couponId, rows.map(r => ({ match: r.match, pick: r.pick, odds: Number(r.odds) })))
      setPhase('done')
      getCoupons().then(setPrevCoupons).catch(() => {})
      setTimeout(() => navigate('/'), 800)
    } catch (e) {
      setError(e.message)
      setPhase('review')
    }
  }

  function updateRow(idx, field, value) {
    setRows(prev => prev.map((r, i) => i === idx ? { ...r, [field]: value } : r))
  }

  function removeRow(idx) {
    setRows(prev => prev.filter((_, i) => i !== idx))
  }

  function addRow() {
    setRows(prev => [...prev, { match: '', pick: '', odds: '', key: Date.now() }])
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
        <p className={styles.loadingMsg}>Running OCR…</p>
      </div>
    )
  }

  if (phase === 'done') {
    return (
      <div className={styles.page}>
        <p className={styles.loadingMsg}>✓ Saved</p>
      </div>
    )
  }

  if (phase === 'review' || phase === 'confirming') {
    return (
      <div className={styles.page}>
        <div className={styles.reviewHeader}>
          <span className={styles.sectionTitle}>REVIEW LEGS</span>
          <span className={styles.reviewMeta}>{rows.length} rows</span>
        </div>

        {error && <p className={styles.error}>{error}</p>}

        <div className={styles.reviewTable}>
          <div className={styles.reviewHead}>
            <span>Match</span>
            <span>Pick</span>
            <span>Odds</span>
            <span />
          </div>
          {rows.map((row, idx) => (
            <div key={row.key} className={styles.reviewRow}>
              <input
                className={styles.cell}
                value={row.match}
                onChange={e => updateRow(idx, 'match', e.target.value)}
                placeholder="Match"
              />
              <input
                className={styles.cell}
                value={row.pick}
                onChange={e => updateRow(idx, 'pick', e.target.value)}
                placeholder="Pick"
              />
              <input
                className={`${styles.cell} ${styles.mono}`}
                value={row.odds}
                onChange={e => updateRow(idx, 'odds', e.target.value)}
                placeholder="1.85"
                type="number"
                step="0.01"
                min="1"
              />
              <button className={styles.removeBtn} onClick={() => removeRow(idx)}>×</button>
            </div>
          ))}
        </div>

        <button className={styles.addRow} onClick={addRow}>+ Add row</button>

        <div className={styles.reviewActions}>
          <button className={styles.btnSecondary} onClick={() => setPhase('form')}>← Back</button>
          <button
            className={styles.btnPrimary}
            onClick={handleConfirm}
            disabled={phase === 'confirming' || rows.length === 0}
          >
            {phase === 'confirming' ? 'Saving…' : 'CONFIRM & SAVE'}
          </button>
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
                <span className={`${styles.statusChip} ${c.status === 'live' ? styles.live : styles.pending}`}>
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
