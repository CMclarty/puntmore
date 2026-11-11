// BB Boost's bet log: who settled a bet. A vendor's name means the legs settled
// themselves from that vendor's results (api/app/hills/settle.py): odds-api.net
// now, OddsPapi for bets logged before Hills moved. Anything else is the person
// who set the result by hand.
export const isAutoSettled = settledBy => settledBy === 'oddspapi' || settledBy === 'odds-api.net'
