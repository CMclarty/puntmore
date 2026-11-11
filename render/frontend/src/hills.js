// BB Boost's bet log: who settled a bet. A vendor's name means the legs settled
// themselves from that vendor's results (api/app/hills/settle.py), which each
// vendor does only for bets logged on it and only while Hills is on it:
// odds-api.net with ODDSAPINET_API_KEY set, OddsPapi without. Anything else is
// the person who set the result by hand.
export const isAutoSettled = settledBy => settledBy === 'oddspapi' || settledBy === 'odds-api.net'
