import { useState, useEffect, useRef, useCallback } from 'react'
import { getAwSummary, setAwResult } from '../api'
import useAutoRefresh from '../hooks/useAutoRefresh'
import { dayName, stepDay, money, progress } from '../aw26'
import f from './CardsPage.module.css'
import s from './Aw26Page.module.css'
import Aw26Summary from './Aw26Summary'

/**
 * AW 26: every slip Callum places at the Paddy terminal, against £1,000 EV a
 * day and £100,000 profit. The band holds the day pill (and, on Summary, the
 * all-time profit against the target); a floating switch picks Summary or
 * Slips. The day is null for today.
 */

const BAND = '#004833'
const VIEWS = [['summary', 'Summary'], ['slips', 'Slips']]
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

export default function Aw26Page() {
  const [day, setDay] = useState(null)
  const [view, setView] = useState('summary')
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState(null)
  const [now, setNow] = useState(() => Date.now())
  const [picking, setPicking] = useState(false)   // the day panel is open

  // The tool switcher below takes the band's colour while this page is open.
  useEffect(() => {
    const root = document.documentElement.style
    root.setProperty('--tool-band', BAND)
    root.setProperty('--dock-bg', '#fff')
    return () => { root.removeProperty('--tool-band'); root.removeProperty('--dock-bg') }
  }, [])

  // Load, then refresh in place; a failed refresh hides the figures rather
  // than leave old ones on screen.
  const live = useRef(true)
  const seq = useRef(0)   // refreshes overlap (interval + focus + a mark): only the latest may land
  const refresh = useCallback(() => {
    const id = ++seq.current
    return getAwSummary(day)
      .then(data => {
        if (!live.current || id !== seq.current) return
        setSummary(data)
        setError(null)
        setNow(Date.now())
      })
      .catch(err => live.current && id === seq.current && setError(err.message))
  }, [day])
  useEffect(() => {
    live.current = true
    refresh()
    return () => { live.current = false }
  }, [refresh])
  useAutoRefresh(refresh)

  const onMark = (legId, result) => setAwResult(legId, result).then(refresh, err => setError(err.message))

  const today = summary?.today
  const chosen = day ?? today
  const step = by => (summary ? stepDay(chosen, by, summary.first_day, today) : null)
  const goTo = next => setDay(next === today ? null : next)
  const bar = summary && progress(summary.to_date.profit, summary.targets.profit)

  return (
    <div className={f.page} style={{ '--band': BAND }}>
      <div className={`${f.band} ${s.band}`}>
        <div className={f.title}>
          <span className={f.name}>AW 26</span>
          {summary && <span className={f.updated}>Day {summary.days}</span>}
        </div>
        {summary && (
          <button type="button" className={`${f.pill} ${s.dayPill} ${picking ? f.pillOpen : ''}`}
            aria-expanded={picking} onClick={() => setPicking(p => !p)}>{dayName(chosen, today)} ▾</button>
        )}
        {summary && view === 'summary' && (
          <div className={s.headline}>
            <div className={s.profit}>
              <span className={s.big}>{money(summary.to_date.profit)}</span>
              <span className={s.allTime}>all-time profit</span>
            </div>
            <div className={s.bar}><div className={s.fill} style={{ width: `${bar.width}%` }} /></div>
            <div className={s.under}>
              <span>{bar.label}</span>
              <span>{plural(summary.to_date.slips, 'slip')} · {plural(summary.days, 'day')}</span>
            </div>
          </div>
        )}
      </div>
      {summary && picking && (
        <div className={f.panel}>
          <div className={`${f.group} ${s.days}`} role="group" aria-label="Day">
            <button type="button" className={f.chip} disabled={!step(-1)} onClick={() => goTo(step(-1))}>‹ Earlier</button>
            <button type="button" className={f.chip} disabled={!step(1)} onClick={() => goTo(step(1))}>Later ›</button>
            <button type="button" className={f.chip} onClick={() => { setDay(null); setPicking(false) }}>Today</button>
          </div>
        </div>
      )}

      {error && <p className={f.error}>{error}</p>}
      {!error && !summary && <p className={f.empty}>Loading…</p>}
      {!error && summary && view === 'summary' && <Aw26Summary summary={summary} now={now} onMark={onMark} />}
      {/* Task 9: view === 'slips' renders <Aw26Slips day={day} today={today} onChange={refresh} /> */}

      <div className={`${f.offerSwitch} ${s.views}`} role="group" aria-label="View">
        {VIEWS.map(([k, label]) => (
          <button key={k} type="button" className={`${f.offerBtn} ${view === k ? f.offerOn : ''}`}
            aria-pressed={view === k} onClick={() => setView(k)}>{label}</button>
        ))}
      </div>
    </div>
  )
}
