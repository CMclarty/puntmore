// AW 26's sums and words (pages/Aw26Page.jsx), pure and pinned by aw26.test.js.
// The API's times are naive UTC; they are read as UTC and printed in London time.

const HOUR = 3_600_000
const DAY = 24 * HOUR
const MINUS = '−'

// A naive API time ("2026-11-09T15:00:00") as UTC milliseconds; null stays null.
function utc(iso) {
  if (!iso) return null
  return Date.parse(/(Z|[+-]\d\d:?\d\d)$/i.test(iso) ? iso : `${iso}Z`)
}

// An ISO day ("2026-11-09") as UTC midnight.
const midnight = (day) => Date.parse(`${day}T00:00:00Z`)

// Signed pounds: "+£3,240", "−£12.50", "£0"; no figure is "—".
export function money(n, pence = false) {
  if (n == null) return '—'
  const places = pence ? 2 : 0
  const size = Math.abs(n).toLocaleString('en-GB', { minimumFractionDigits: places, maximumFractionDigits: places })
  // The sign follows the figure as printed: −0.004 is "£0", not "−£0".
  if (Number(Math.abs(n).toFixed(places)) === 0) return `£${size}`
  return `${n < 0 ? MINUS : '+'}£${size}`
}

// Unsigned pounds: "£10,700".
export function pounds(n) {
  if (n == null) return '—'
  return '£' + n.toLocaleString('en-GB', { maximumFractionDigits: 0 })
}

// The target bar: its width, 0 to 100, and "3.2% of £100,000" from the same figure.
export function progress(profit, target) {
  const width = target > 0 ? Math.min(100, Math.max(0, (profit * 100) / target)) : 0
  return { width, label: `${Number(width.toFixed(1))}% of ${pounds(target)}` }
}

// "Today", "Tomorrow", else "Thu 12 Nov".
export function dayName(day, today) {
  const gap = Math.round((midnight(day) - midnight(today)) / DAY)
  if (gap === 0) return 'Today'
  if (gap === 1) return 'Tomorrow'
  return new Date(midnight(day)).toLocaleDateString('en-GB',
    { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' })
}

// The ISO day `by` days on from `day`, or null outside first..today.
export function stepDay(day, by, first, today) {
  const next = new Date(midnight(day) + by * DAY).toISOString().slice(0, 10)
  return next < first || next > today ? null : next
}

// A kick-off's time in London, "15:00"; no kick-off is blank.
export function koTime(iso) {
  const t = utc(iso)
  if (t == null) return ''
  return new Date(t).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/London' })
}

// A kick-off's short London weekday, "Thu", when it falls on a day other than
// `day` (ISO); on that day, or with no kick-off, blank.
export function koDay(iso, day) {
  const t = utc(iso)
  if (t == null) return ''
  const london = new Date(t).toLocaleDateString('en-CA', { timeZone: 'Europe/London' })
  return london === day ? '' : new Date(t).toLocaleDateString('en-GB', { weekday: 'short', timeZone: 'Europe/London' })
}

// A selection's mark: its result, or "?" once it is a day past kick-off and still pending.
export function mark(selection, now) {
  const { result, kickoff } = selection
  if (result === 'won') return 'W'
  if (result === 'lost') return 'L'
  if (result === 'void') return 'V'
  const k = utc(kickoff)
  return k != null && now - k > DAY ? '?' : '–'
}

// "4 won · 3 lost · 3 to come": parts that are zero left out, void not counted.
export function tally(selections) {
  const count = (r) => selections.filter((s) => s.result === r).length
  return [[count('won'), 'won'], [count('lost'), 'lost'], [count('pending'), 'to come']]
    .filter(([n]) => n > 0).map(([n, words]) => `${n} ${words}`).join(' · ')
}

// The selections by day, each day's games by kick-off then event; a game with
// several selections is one row, its slips the sum of its picks'.
export function sections(selections, today, now) {
  const days = [...new Set(selections.map((s) => s.day))].sort()
  return days.map((day) => {
    const ofDay = selections.filter((s) => s.day === day)
    const games = new Map()
    for (const s of ofDay) {
      const pick = { ...s, mark: mark(s, now) }
      const g = games.get(s.game)
      if (g) {
        g.picks.push(pick)
        g.slips += s.slips
        if (g.kickoff == null || (s.kickoff != null && utc(s.kickoff) < utc(g.kickoff))) g.kickoff = s.kickoff
      } else {
        games.set(s.game, { game: s.game, event: s.event, kickoff: s.kickoff, slips: s.slips, picks: [pick] })
      }
    }
    const at = (g) => utc(g.kickoff) ?? Infinity
    const rows = [...games.values()].sort((a, b) => at(a) - at(b) || (a.event ?? '').localeCompare(b.event ?? ''))
    return { day, title: dayName(day, today), tally: tally(ofDay), games: rows }
  })
}

// A pick in few words: "Over 10.5 corners" → "O10.5 corners". The market goes
// in front only when the selection has no words after its line ("Over 8.5").
export function pickText(leg) {
  const { market, selection } = leg
  if (!selection) return market ?? ''
  const short = selection.replace(/^Over\s+(?=\d)/i, 'O').replace(/^Under\s+(?=\d)/i, 'U')
  const line = selection.search(/\d/)
  const bare = line >= 0 && !/\p{L}/u.test(selection.slice(line))
  return market && bare ? `${market} ${short}` : short
}

// The short London weekday of the latest kick-off among a slip's pending legs.
export function openTo(slip) {
  const times = slip.legs.filter((l) => l.result === 'pending').map((l) => utc(l.kickoff)).filter((t) => t != null)
  if (!times.length) return null
  return new Date(Math.max(...times)).toLocaleDateString('en-GB', { weekday: 'short', timeZone: 'Europe/London' })
}

// A slip Render could not price.
export const flagged = (slip) => slip.ev == null
