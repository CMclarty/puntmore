import { useState, useEffect, useRef, useCallback } from 'react'
import { getAwSummary, setAwResult } from '../api'
import useAutoRefresh from '../hooks/useAutoRefresh'
import { dayName, stepDay, money, progress } from '../aw26'
import f from './CardsPage.module.css'
import s from './Aw26Page.module.css'
import Aw26Summary from './Aw26Summary'
import Aw26Slips from './Aw26Slips'

/**
 * AW 26: every slip Callum places at the Paddy terminal, against £1,000 EV a
 * day and £100,000 profit. The band holds the day pill (and, on Summary, the
 * all-time profit against the target, on Slips the day's count); a floating
 * switch picks Summary or Slips. The day is null for today.
 */

const BAND = '#004833'
const VIEWS = [['summary', 'Summary'], ['slips', 'Slips']]
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

export default function Aw26Page() {
  const [day, setDay] = useState(null)
  const [view, setView] = useState('summary')
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState(null)          // the last load or refresh failed
  const [markError, setMarkError] = useState(null)  // the last mark failed
  const [now, setNow] = useState(() => Date.now())
  const [picking, setPicking] = useState(false)   // the day panel is open
  const [count, setCount] = useState(null)        // the Slips screen's slips on the day, once loaded

  // The tool switcher below takes the band's colour while this page is open.
  useEffect(() => {
    const root = document.documentElement.style
    root.setProperty('--tool-band', BAND)
    root.setProperty('--dock-bg', '#fff')
    return () => { root.removeProperty('--tool-band'); root.removeProperty('--dock-bg') }
  }, [])

  // Load, then refresh in place. A failed load or refresh hides everything
  // that is data (the headline, "Day N", the figures and the list); the band
  // keeps its title, the day pill and the day panel, so Today is one tap away.
  // `summary` keeps the last good answer only for the pill's day name and the
  // panel's bounds.
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

  // A mark's answer may come after the day has changed: it refetches the day
  // chosen then, and a failure on a day no longer shown says nothing.
  const current = useRef({ refresh, day })
  useEffect(() => { current.current = { refresh, day } })

  // A failed mark is not a failed load: the screen stays as it is, its own
  // message shows above the selections, and the summary is fetched once more
  // (the mark may have landed though its answer was lost). The message goes on
  // the next good mark or a change of day.
  const onMark = (legId, result) => {
    const markedOn = day
    return setAwResult(legId, result).then(
      () => { setMarkError(null); return current.current.refresh() },
      err => {
        if (current.current.day !== markedOn) return
        setMarkError(err.message)
        return current.current.refresh()
      })
  }

  const today = summary?.today
  const chosen = day ?? today
  const shown = error ? null : summary   // the data on screen
  const step = by => (summary ? stepDay(chosen, by, summary.first_day, today) : null)
  const choose = next => { setDay(next === today ? null : next); setMarkError(null); setCount(null) }
  const show = next => { if (next !== view) { setView(next); setCount(null) } }
  const bar = shown && progress(shown.to_date.profit, shown.targets.profit)

  return (
    <div className={f.page} style={{ '--band': BAND }}>
      <div className={`${f.band} ${s.band}`}>
        <div className={f.title}>
          <span className={f.name}>AW 26</span>
          {shown && view === 'summary' && <span className={f.updated}>Day {shown.days}</span>}
          {view === 'slips' && count != null && (
            <span className={f.updated}>{plural(count, 'slip')}{day == null ? ' today' : ''}</span>
          )}
        </div>
        <button type="button" className={`${f.pill} ${s.dayPill} ${picking ? f.pillOpen : ''}`}
          aria-expanded={picking} onClick={() => setPicking(p => !p)}>
          {summary ? dayName(chosen, today) : 'Today'} ▾
        </button>
        {shown && view === 'summary' && (
          <div className={s.headline}>
            <div className={s.profit}>
              <span className={s.big}>{money(shown.to_date.profit)}</span>
              <span className={s.allTime}>all-time profit</span>
            </div>
            <div className={s.bar}><div className={s.fill} style={{ width: `${bar.width}%` }} /></div>
            <div className={s.under}>
              <span>{bar.label}</span>
              <span>{plural(shown.to_date.slips, 'slip')} · {plural(shown.days, 'day')}</span>
            </div>
          </div>
        )}
      </div>
      {picking && (
        <div className={f.panel}>
          <div className={`${f.group} ${s.days}`} role="group" aria-label="Day">
            <button type="button" className={f.chip} disabled={!step(-1)} onClick={() => choose(step(-1))}>‹ Earlier</button>
            <button type="button" className={f.chip} disabled={!step(1)} onClick={() => choose(step(1))}>Later ›</button>
            <button type="button" className={f.chip} onClick={() => { choose(null); setPicking(false) }}>Today</button>
          </div>
        </div>
      )}

      {/* Slips loads its own rows and says when they fail: the summary's
          failure shows there only while it has no day to go on. */}
      {error && (view === 'summary' || !summary) && <p className={f.error}>{error}</p>}
      {!error && !summary && <p className={f.empty}>Loading…</p>}
      {shown && view === 'summary' && <Aw26Summary summary={shown} now={now} onMark={onMark} markError={markError} />}
      {summary && view === 'slips' && <Aw26Slips day={day} today={today} onChange={refresh} onCount={setCount} />}

      <div className={`${f.offerSwitch} ${s.views}`} role="group" aria-label="View">
        {VIEWS.map(([k, label]) => (
          <button key={k} type="button" className={`${f.offerBtn} ${view === k ? f.offerOn : ''}`}
            aria-pressed={view === k} onClick={() => show(k)}>{label}</button>
        ))}
      </div>
    </div>
  )
}
