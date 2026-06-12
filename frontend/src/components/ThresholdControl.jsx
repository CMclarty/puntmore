import { useState } from 'react'
import styles from './ThresholdControl.module.css'

export default function ThresholdControl({ value, onChange }) {
  const [raw, setRaw] = useState(String(value))

  function handleChange(e) {
    setRaw(e.target.value)
    const num = parseFloat(e.target.value)
    if (!isNaN(num) && num >= -0.50 && num <= 0.50) onChange(num)
  }

  return (
    <div className={styles.wrapper}>
      <span className={styles.label}>Min edge vs fair</span>
      <input
        className={styles.input}
        type="number"
        min="-0.50"
        max="0.50"
        step="0.01"
        value={raw}
        onChange={handleChange}
      />
      <span className={styles.hint}>0.00 = value only · −0.05 = near-arbs too</span>
    </div>
  )
}
