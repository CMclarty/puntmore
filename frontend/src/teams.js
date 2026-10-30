// The names Shalgo shows clubs by: the short, common name a punter uses, not
// the feed's official one. OddsPapi says "Tottenham Hotspur", Paddy says
// "Nottm Forest"; both are shown as people say them ("Spurs", "Forest").
// Display only: the API still matches teams on their full names
// (api/app/normalise.py).
//
// Each entry is the short name, then every spelling the feeds use for it.
// Matching ignores case and only replaces a whole name, so "Inter Miami" is
// left alone by "Inter Milan", and "Bristol Rovers" by "Bristol City".
const TABLE = [
  // Premier League
  ['Villa', 'Aston Villa'],
  ['Bournemouth', 'AFC Bournemouth'],
  ['Brighton', 'Brighton & Hove Albion', 'Brighton and Hove Albion', 'Brighton Hove Albion'],
  ['Palace', 'Crystal Palace'],
  ['Leeds', 'Leeds United', 'Leeds Utd'],
  ['Man City', 'Manchester City'],
  ['Man Utd', 'Manchester United', 'Manchester Utd', 'Man United'],
  ['Newcastle', 'Newcastle United', 'Newcastle Utd'],
  ['Forest', 'Nottingham Forest', "Nott'm Forest", 'Nottm Forest', 'Nottingham'],
  ['Sunderland', 'AFC Sunderland'],
  ['Spurs', 'Tottenham Hotspur', 'Tottenham'],
  ['West Ham', 'West Ham United', 'West Ham Utd'],
  ['Wolves', 'Wolverhampton Wanderers', 'Wolverhampton'],
  ['Leicester', 'Leicester City'],
  ['Ipswich', 'Ipswich Town'],
  // Championship
  ['Birmingham', 'Birmingham City'],
  ['Blackburn', 'Blackburn Rovers'],
  ['Cardiff', 'Cardiff City'],
  ['Charlton', 'Charlton Athletic'],
  ['Coventry', 'Coventry City'],
  ['Derby', 'Derby County'],
  ['Hull', 'Hull City'],
  ['Boro', 'Middlesbrough'],
  ['Norwich', 'Norwich City'],
  ['Oxford', 'Oxford United', 'Oxford Utd'],
  ['Luton', 'Luton Town'],
  ['Plymouth', 'Plymouth Argyle'],
  ['Preston', 'Preston North End', 'Preston NE'],
  ['QPR', 'Queens Park Rangers'],
  ['Sheff Utd', 'Sheffield United', 'Sheffield Utd'],
  ['Sheff Wed', 'Sheffield Wednesday', 'Sheffield Wed'],
  ['Stoke', 'Stoke City'],
  ['Swansea', 'Swansea City'],
  ['West Brom', 'West Bromwich Albion', 'West Bromwich'],
  // League One / Two
  ['MK Dons', 'Milton Keynes Dons', 'Milton Keynes', 'MK Dons FC'],
  ['Wycombe', 'Wycombe Wanderers'],
  ['Bolton', 'Bolton Wanderers'],
  ['Huddersfield', 'Huddersfield Town'],
  ['Peterborough', 'Peterborough United', 'Peterborough Utd'],
  ['Stockport', 'Stockport County'],
  ['Wigan', 'Wigan Athletic'],
  ['Rotherham', 'Rotherham United', 'Rotherham Utd'],
  ['Doncaster', 'Doncaster Rovers'],
  ['Bradford', 'Bradford City'],
  ['Orient', 'Leyton Orient'],
  ['Lincoln', 'Lincoln City'],
  ['Exeter', 'Exeter City'],
  ['Mansfield', 'Mansfield Town'],
  ['Northampton', 'Northampton Town'],
  ['Burton', 'Burton Albion'],
  ['Shrewsbury', 'Shrewsbury Town'],
  ['Cambridge', 'Cambridge United', 'Cambridge Utd'],
  ['Crawley', 'Crawley Town'],
  ['Swindon', 'Swindon Town'],
  ['Grimsby', 'Grimsby Town'],
  ['Carlisle', 'Carlisle United', 'Carlisle Utd'],
  ['Colchester', 'Colchester United', 'Colchester Utd'],
  ['Crewe', 'Crewe Alexandra'],
  ['Tranmere', 'Tranmere Rovers'],
  ['Newport', 'Newport County'],
  ['Accrington', 'Accrington Stanley'],
  ['Harrogate', 'Harrogate Town'],
  ['Salford', 'Salford City'],
  ['Cheltenham', 'Cheltenham Town'],
  ['Fleetwood', 'Fleetwood Town'],
  ['Oldham', 'Oldham Athletic'],
  // Scotland
  ['Hearts', 'Heart of Midlothian'],
  ['Hibs', 'Hibernian'],
  ['Dundee Utd', 'Dundee United'],
  // Europe
  ['PSG', 'Paris Saint-Germain', 'Paris Saint Germain', 'Paris SG'],
  ['Inter', 'Inter Milan', 'Internazionale', 'FC Internazionale Milano', 'Inter Milano'],
  ['Bayern', 'Bayern Munich', 'Bayern München', 'FC Bayern München', 'Bayern Munchen'],
  ['Dortmund', 'Borussia Dortmund'],
  ['Leverkusen', 'Bayer Leverkusen', 'Bayer 04 Leverkusen'],
  ['Gladbach', 'Borussia Mönchengladbach', 'Borussia Monchengladbach', "Borussia M'gladbach"],
  ['Leipzig', 'RB Leipzig'],
  ['Frankfurt', 'Eintracht Frankfurt'],
  ['Stuttgart', 'VfB Stuttgart'],
  ['Atletico', 'Atletico Madrid', 'Atlético Madrid', 'Atletico de Madrid', 'Atlético de Madrid'],
  ['Marseille', 'Olympique Marseille', 'Olympique de Marseille'],
  ['Lyon', 'Olympique Lyonnais', 'Olympique Lyon'],
  ['Sporting', 'Sporting CP', 'Sporting Lisbon'],
  ['PSV', 'PSV Eindhoven'],
]

const SHORT = new Map()
for (const [short, ...names] of TABLE) for (const n of names) SHORT.set(n.toLowerCase(), short)

// One pass, longest name first, so "Tottenham Hotspur" wins over "Tottenham".
const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
const NAMES = new RegExp(
  `(?<![\\p{L}\\p{N}])(${[...SHORT.keys()].sort((a, b) => b.length - a.length).map(escape).join('|')})(?![\\p{L}\\p{N}])`,
  'giu')

/** `text` (a team, a match "A vs B", a pick "Spurs Over 4.5 corners") with
 * every club in the table shown by its short name. */
export const shortTeams = text =>
  typeof text === 'string' ? text.replace(NAMES, m => SHORT.get(m.toLowerCase()) ?? m) : text
