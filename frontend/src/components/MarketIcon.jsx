/**
 * One glyph per Paddy market, so a row's market reads at a glance.
 * Monochrome (Callum: no colour clutter) and SOLID shapes — readability over
 * artistry: flag, card, ball, glove must be told apart at 20 px on a phone.
 */
const PATHS = {
  // Corner flag: pole and a solid pennant.
  corners: <><path d="M6 22V2.5" /><path d="M6 3h13l-4 5 4 5H6Z" fill="currentColor" /></>,
  // A booking card, upright and solid.
  cards: <rect x="6" y="2.5" width="12" height="19" rx="2" fill="currentColor" />,
  // Football: outline, solid centre panel, spokes to the edge.
  goals: <>
    <circle cx="12" cy="12" r="9.5" />
    <path d="M12 8.2l3.6 2.6-1.4 4.2H9.8l-1.4-4.2Z" fill="currentColor" />
    <path d="M12 8.2V2.5M15.6 10.8l5.4-1.8M14.2 15l3.3 4.6M9.8 15l-3.3 4.6M8.4 10.8 3 9" />
  </>,
  // Goalkeeper glove: four separate fingers, thumb out, solid palm and cuff.
  saves: <>
    <path d="M8.2 11V4M11.6 11V2.8M15 11V3.2M18.4 11.5V5" strokeWidth="2.6" />
    <path d="M7 10.5h12.6V15a4 4 0 0 1-4 4H11a4 4 0 0 1-4-4Z" fill="currentColor" />
    <path d="M7.5 15 4 9.5" strokeWidth="2.6" />
    <rect x="9" y="19.5" width="8.5" height="3" rx="0.8" fill="currentColor" stroke="none" />
  </>,
}

export default function MarketIcon({ market, size = 20 }) {
  const path = PATHS[market]
  if (!path) return <span style={{ width: size, display: 'inline-block' }} />
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
      style={{ color: 'var(--ink)', flexShrink: 0 }} aria-label={market} role="img">
      {path}
    </svg>
  )
}
