import { Fragment, useState } from 'react'
import { money, pounds, dayName, sections, pickText, koTime } from '../aw26'
import f from './CardsPage.module.css'
import s from './Aw26Page.module.css'

// AW 26's Summary screen: the chosen day's figures beside the figures to
// date, then the day's selections by day, a game with several picks on one
// row that drops them down. A result is marked from the chips under its row.

const WORD = { W: 'Won', L: 'Lost', V: 'Void', '–': '–', '?': '?' }
const TONE = { W: s.won, L: s.lost, '–': s.none }
const RESULTS = [['won', 'Won'], ['lost', 'Lost'], ['void', 'Void'], ['pending', 'Pending']]
// EV in plain pounds as the mock has it, but a loss keeps its sign.
const plain = n => (n < 0 ? money(n) : pounds(n))
const odds = n => (n == null ? '—' : n.toFixed(2))
const signed = n => (n > 0 ? s.good : n < 0 ? s.bad : '')

function Figure({ label, cells, tone = () => '' }) {
  return (
    <div className={`${f.row} ${s.fig}`}>
      <span className={s.figLabel}>{label}</span>
      {cells.map(([value, raw, sub], i) => (
        <span key={i} className={s.figure}>
          <b className={tone(raw)}>{value}</b>
          {sub && <small>{sub}</small>}
        </span>
      ))}
    </div>
  )
}

function Result({ pick, open, onToggle }) {
  return (
    <button type="button" className={`${s.result} ${TONE[pick.mark] ?? ''}`}
      aria-expanded={open} onClick={onToggle}>{WORD[pick.mark]}</button>
  )
}

function Marking({ pick, onPick }) {
  return (
    <div className={s.marking} role="group" aria-label="Result">
      {RESULTS.map(([k, label]) => (
        <button key={k} type="button" className={`${f.chip} ${pick.result === k ? f.chipOn : ''}`}
          aria-pressed={pick.result === k} onClick={() => onPick(k)}>{label}</button>
      ))}
    </div>
  )
}

const Assumed = ({ pick }) => pick.fair_assumed && <span className={f.more}>~</span>

export default function Aw26Summary({ summary, now, onMark, markError }) {
  const [down, setDown] = useState(() => new Set())   // games dropped down
  const [marking, setMarking] = useState(null)          // the leg whose chips are open
  const { day, today, chosen, to_date: all } = summary
  const days = sections(summary.selections, today, now)

  const toggle = game => setDown(d => { const n = new Set(d); n.has(game) ? n.delete(game) : n.add(game); return n })
  const result = p => (
    <Result pick={p} open={marking === p.leg_id} onToggle={() => setMarking(m => (m === p.leg_id ? null : p.leg_id))} />
  )
  const chips = p => marking === p.leg_id && (
    <Marking pick={p} onPick={r => { setMarking(null); onMark(p.leg_id, r) }} />
  )

  const game = g => {
    if (g.picks.length === 1) {
      const p = g.picks[0]
      return (
        <Fragment key={g.game}>
          <div className={`${f.row} ${s.sel}`}>
            <span className={f.what}>
              <span className={f.pick}>{pickText(p)}<Assumed pick={p} /></span>
              <span className={f.match}>{p.event}</span>
            </span>
            <span className={f.ko}>{koTime(p.kickoff)}</span>
            <span className={s.slips}>×{p.slips}</span>
            <span className={f.odds}>{odds(p.odds)}</span>
            {result(p)}
          </div>
          {chips(p)}
        </Fragment>
      )
    }
    const open = down.has(g.game)
    return (
      <Fragment key={g.game}>
        <button type="button" className={`${f.row} ${s.sel} ${s.game}`} aria-expanded={open}
          onClick={() => toggle(g.game)}>
          <span className={f.what}>
            <span className={f.pick}>{g.event}<span className={f.more}> {open ? '▴' : '▾'} {g.picks.length}</span></span>
          </span>
          <span className={f.ko}>{koTime(g.kickoff)}</span>
          <span className={s.slips}>×{g.slips}</span>
          <span className={s.marks}>
            {g.picks.map(p => <span key={p.leg_id} className={TONE[p.mark]}>{p.mark}</span>)}
          </span>
        </button>
        {open && (
          <div className={f.sub}>
            {g.picks.map(p => (
              <Fragment key={p.leg_id}>
                <div className={`${f.subRow} ${s.sel}`}>
                  <span className={f.subLine}>{pickText(p)}<Assumed pick={p} /></span>
                  <span />
                  <span className={s.slips}>×{p.slips}</span>
                  <span className={f.odds}>{odds(p.odds)}</span>
                  {result(p)}
                </div>
                {chips(p)}
              </Fragment>
            ))}
          </div>
        )}
      </Fragment>
    )
  }

  return (
    <div className={`${f.list} ${s.summary}`}>
      <div className={`${f.row} ${f.colHead} ${s.fig}`}>
        <span /><span>{dayName(day, today)}</span><span>To date</span>
      </div>
      <Figure label="EV" cells={[
        [plain(chosen.ev), chosen.ev, `of ${pounds(chosen.ev_target)}`],
        [plain(all.ev), all.ev, `of ${pounds(all.ev_target)}`]]} />
      <Figure label="Profit" tone={signed} cells={[
        [money(chosen.profit), chosen.profit], [money(all.profit), all.profit]]} />
      <Figure label="Staked" cells={[[pounds(chosen.staked)], [pounds(all.staked)]]} />
      <Figure label="Slips" cells={[
        [chosen.slips, chosen.slips, `${chosen.open} open`], [all.slips, all.slips, `${all.open} open`]]} />

      {markError && <p className={f.error}>{markError}</p>}
      <div className={`${f.row} ${f.colHead} ${s.sel}`}>
        <span /><span>KO</span><span>Slips</span><span>Odds</span><span>Result</span>
      </div>
      {days.length ? days.map(d => (
        <Fragment key={d.day}>
          <div className={s.dayHead}><b>{d.title}</b><span>{d.tally}</span></div>
          {d.games.map(game)}
        </Fragment>
      )) : <p className={f.empty}>No selections.</p>}
    </div>
  )
}
