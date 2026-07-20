import { useState } from 'react'
import styles from './ThresholdControl.module.css'

// Operates in %EV: the bet's odds as a percentage of de-vigged fair odds
// (100 = fair, 98 = within 2% of fair). The API still takes an edge fraction;
// pages convert with ev / 100 - 1.
export default function ThresholdControl({ value, onChange }) {
  const [raw, setRaw] = useState(String(value))

  function handleChange(e) {
    setRaw(e.target.value)
    const num = parseFloat(e.target.value)
    if (!isNaN(num) && num >= 50 && num <= 150) onChange(num)
  }

  return (
    <div className={styles.wrapper}>
      <span className={styles.label}>Min %EV vs fair</span>
      <input
        className={styles.input}
        type="number"
        min="50"
        max="150"
        step="0.5"
        value={raw}
        onChange={handleChange}
      />
      <span className={styles.hint}>100 = fair value · 98 = within 2% of fair</span>
    </div>
  )
}
