const BASE = '/api'

function redirectToLogin() {
  if (window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

async function get(path) {
  const res = await fetch(`${BASE}${path}`)
  if (res.status === 401) { redirectToLogin(); throw new Error('Not authenticated') }
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`)
  return res.json()
}

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (res.status === 401) { redirectToLogin(); throw new Error('Not authenticated') }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail ?? `POST ${path} → ${res.status}`)
  }
  return res.json()
}

// Auth
export async function getMe() {
  const res = await fetch(`${BASE}/auth/me`)
  if (res.status === 401) return null
  if (!res.ok) throw new Error(`GET /auth/me → ${res.status}`)
  return res.json()
}

export async function login(username, password) {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail ?? 'Login failed')
  }
  return res.json()
}

export const logout = () => post('/auth/logout', {})

// Coupons (new model: accumulators + legs)
export const getCoupons = () => get('/coupons')
export const getCouponLegs = (id) => get(`/coupons/${id}/legs`)

// The seven coupon types are the app's top-level entities — each is its own
// filtered fixture list. Served by the API so the tracker dropdown and the
// upload form cannot drift out of sync.
export const getCouponTypes = () => get('/coupons/types')
// Coupons cover a fixed window (Mon–Wed / Wed–Fri / Sat / Sun), not one day.
export const getCouponPeriods = (typeCode) =>
  get(`/coupons/periods${typeCode ? `?type_code=${typeCode}` : ''}`)
export const getTypeSelections = (code) => get(`/coupons/types/${code}/selections`)
export const refreshCoupon = (id) => post(`/coupons/${id}/refresh`, {})

export async function uploadCoupon(typeCode, period, file, name = null) {
  const form = new FormData()
  form.append('type_code', typeCode)
  form.append('period', period)
  if (name) form.append('name', name)
  form.append('file', file)
  const res = await fetch(`${BASE}/upload`, { method: 'POST', body: form })
  if (res.status === 401) { redirectToLogin(); throw new Error('Not authenticated') }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail ?? `Upload failed (${res.status})`)
  }
  return res.json()
}

export const rescanCoupon = (couponId) => post(`/upload/${couponId}/rescan`, {})

export async function editLeg(couponId, legId, fields) {
  const res = await fetch(`${BASE}/coupons/${couponId}/legs/${legId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  })
  if (res.status === 401) { redirectToLogin(); throw new Error('Not authenticated') }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail ?? `Edit failed (${res.status})`)
  }
  return res.json()
}

// AccaBonus arbs
export const getAccaBonusFeed = (minEdge = -0.02) =>
  get(`/accabonus/feed?min_edge=${minEdge}`)

// Cards & corners
export const getCardsFeed = (minEdge = -0.02) =>
  get(`/cards/feed?min_edge=${minEdge}`)

// First-goalscorer value (bet365 vs de-vigged Pinnacle). Rows are produced by
// the scheduler near kickoff and stored, so this just reads the current set.
export const getGoalscorerFeed = () => get('/goalscorers/feed')

// Private P&L bet tracker (per-user ledger).
export const getBets = () => get('/pnl/bets')
export const addBet = (amount) => post('/pnl/bets', { amount })
export async function deleteBet(id) {
  const res = await fetch(`${BASE}/pnl/bets/${id}`, { method: 'DELETE' })
  if (res.status === 401) { redirectToLogin(); throw new Error('Not authenticated') }
  if (!res.ok && res.status !== 204) throw new Error(`DELETE /pnl/bets/${id} → ${res.status}`)
}
