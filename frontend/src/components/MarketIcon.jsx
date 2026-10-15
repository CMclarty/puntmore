/**
 * One small glyph per Paddy market, so a row's market reads at a glance.
 * Monochrome on purpose (Callum: no colour clutter); the card alone is filled.
 */
const PATHS = {
  // Corner flag: pole and pennant.
  corners: <><path d="M5 21V3" /><path d="M5 4h11l-3 4 3 4H5" fill="currentColor" fillOpacity="0.25" /></>,
  // A booking: one card, tilted.
  cards: <rect x="7" y="3.5" width="10" height="15" rx="1.5" transform="rotate(12 12 11)" fill="currentColor" fillOpacity="0.85" />,
  // Goal frame with net.
  goals: <><path d="M3 19V6h18v13" /><path d="M3 10h18M3 14h18M9 6v13M15 6v13" strokeOpacity="0.45" /></>,
  // Goalkeeper glove.
  saves: <path d="M7 21v-6l-3-4 1.5-1 2.5 2V5a1.5 1.5 0 0 1 3 0v5V4a1.5 1.5 0 0 1 3 0v6V5a1.5 1.5 0 0 1 3 0v6-3a1.5 1.5 0 0 1 3 0v6c0 3-2 5-5 5v2Z" />,
}

export default function MarketIcon({ market, size = 18 }) {
  const path = PATHS[market]
  if (!path) return <span style={{ width: size, display: 'inline-block' }} />
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
      style={{ color: 'var(--ink)', flexShrink: 0 }} aria-label={market} role="img">
      {path}
    </svg>
  )
}
