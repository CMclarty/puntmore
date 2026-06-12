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
export const refreshCoupon = (id) => post(`/coupons/${id}/refresh`, {})

export async function uploadCoupon(typeCode, matchDay, file, name = null) {
  const form = new FormData()
  form.append('type_code', typeCode)
  form.append('match_day', matchDay)
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

export const confirmUpload = (couponId, rows) =>
  post(`/upload/${couponId}/confirm`, { rows })

// Tennis arbs
export const getTennisFeed = (minEdge = -0.05) =>
  get(`/tennis/feed?min_edge=${minEdge}`)

// Cards & corners
export const getCardsFeed = (minEdge = -0.05) =>
  get(`/cards/feed?min_edge=${minEdge}`)
