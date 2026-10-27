const BASE = '/api'

/**
 * A failed API call, carrying what the browser knows and the server can't.
 *
 * `error_id` is the reference the backend put in its own record, so one string
 * links what the user saw to the traceback. `body` is the first slice of the
 * response when it WASN'T the API's JSON — a 502 from Render is an HTML error
 * page, and knowing that is the difference between "the app threw" and "the
 * container never answered".
 */
export class ApiError extends Error {
  constructor(message, { status, path, method, errorId, body } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.path = path
    this.method = method
    this.errorId = errorId
    this.body = body
  }
}

// Reporting must never itself throw or recurse: a failing reporter that reports
// its own failure is an infinite loop, and it fires exactly when the network is
// already unhappy. Fire-and-forget, capped, and it never reports itself.
let reported = 0
const MAX_REPORTS = 20

export function reportClientError(message, extra = {}) {
  if (reported >= MAX_REPORTS) return
  reported += 1
  try {
    const payload = JSON.stringify({
      message: String(message).slice(0, 2000),
      route: window.location.pathname,
      request: extra.request ? String(extra.request).slice(0, 300) : null,
      status: typeof extra.status === 'number' ? extra.status : null,
      detail: extra.detail ? String(extra.detail).slice(0, 4000) : null,
    })
    // keepalive so a report survives the page being navigated away or closed,
    // which is when the interesting crashes happen.
    fetch(`${BASE}/diagnostics/client`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true,
    }).catch(() => {})
  } catch { /* never let logging break the app */ }
}

/**
 * Turn a non-OK response into an ApiError, and report it.
 *
 * Reads the body ONCE and decides what it was: the API's JSON error (take its
 * detail and error_id) or something else entirely (keep a slice — that slice is
 * how a proxy error page announces itself).
 */
/**
 * A FastAPI `detail` rendered as text.
 *
 * A 422 sends detail as an ARRAY of {loc, msg, type} objects. Interpolating
 * that into a string produced "[object Object],[object Object]" on screen —
 * an error message that says nothing at all, which is worse than the raw
 * status. Name the offending field and what was wrong with it.
 */
function describeDetail(detail) {
  if (detail == null) return null
  if (typeof detail === 'string') return detail
  const items = Array.isArray(detail) ? detail : [detail]
  const parts = items.map(d => {
    if (typeof d === 'string') return d
    if (!d || typeof d !== 'object') return String(d)
    // loc is like ["body", "type_code"] — the last hop is the field name.
    const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : null
    const msg = d.msg || d.message || d.type || 'invalid'
    return field ? `${field}: ${msg}` : msg
  }).filter(Boolean)
  return parts.length ? parts.join(' · ') : null
}

async function fail(res, path, method) {
  let detail = null, errorId = null, body = null
  const text = await res.text().catch(() => '')
  try {
    const json = JSON.parse(text)
    detail = json.detail ?? null
    errorId = json.error_id ?? null
  } catch {
    body = text.slice(0, 300)
  }
  const isProxy = res.status === 502 || res.status === 503 || res.status === 504
  const message = describeDetail(detail)
    ?? (isProxy
      ? `The server didn't respond (${res.status}). It may be restarting after a deploy.`
      : `${method} ${path} → ${res.status}`)
  const err = new ApiError(message, {
    status: res.status, path, method, errorId, body,
  })
  reportClientError(message, {
    request: `${method} ${path}`,
    status: res.status,
    detail: errorId ? `error_id=${errorId}` : body,
  })
  return err
}

// A fetch that REJECTS never reached the server at all — offline, DNS, TLS, or
// the container gone. Worth reporting under its own shape, though the report
// itself will likely fail too; it lands next time the network is back.
async function send(path, init, method) {
  try {
    return await fetch(`${BASE}${path}`, init)
  } catch (e) {
    const err = new ApiError(
      `Could not reach the server (${e?.message || 'network error'})`,
      { path, method },
    )
    reportClientError(err.message, { request: `${method} ${path}` })
    throw err
  }
}

/**
 * A session that lapsed mid-use: bounce to login, remembering where we were.
 *
 * This is a FULL page load, so the router's location state can't carry the
 * path the way the in-app guard does (App.jsx::Protected) — it has to ride in
 * the URL. Without it, a request that 401s dumps you on the default landing
 * page and the page you were actually on is lost.
 */
function redirectToLogin() {
  if (window.location.pathname === '/login') return
  const next = window.location.pathname + window.location.search
  window.location.href = `/login?next=${encodeURIComponent(next)}`
}

async function get(path) {
  const res = await send(path, undefined, 'GET')
  if (res.status === 401) { redirectToLogin(); throw new ApiError('Not authenticated', { status: 401, path }) }
  if (!res.ok) throw await fail(res, path, 'GET')
  return res.json()
}

async function post(path, body) {
  const res = await send(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, 'POST')
  if (res.status === 401) { redirectToLogin(); throw new ApiError('Not authenticated', { status: 401, path }) }
  if (!res.ok) throw await fail(res, path, 'POST')
  return res.json()
}

// Auth
export async function getMe() {
  const res = await send('/auth/me', undefined, 'GET')
  if (res.status === 401) return null   // not logged in — not an error
  if (!res.ok) throw await fail(res, '/auth/me', 'GET')
  return res.json()
}

export async function login(username, password) {
  const res = await send('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }, 'POST')
  // A bad password is a 401 here and NOT a diagnostic — reporting every typo
  // would bury real failures, so it's raised without a report.
  if (res.status === 401) throw new ApiError('Login failed', { status: 401, path: '/auth/login' })
  if (!res.ok) throw await fail(res, '/auth/login', 'POST')
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
// What can be photographed: the two multi-market sheets (Win And, Team To
// Score — one photo fills every market on them) and the single-market sheets.
export const getUploadOptions = () => get('/coupons/upload-options')
// Coupons cover a fixed window (Mon–Wed / Wed–Fri / Sat / Sun), not one day.
export const getCouponPeriods = (typeCode) =>
  get(`/coupons/periods${typeCode ? `?type_code=${typeCode}` : ''}`)
// A type's whole fixture list, or ONE issue of it. Two issues of a type can be
// live at once (a GG midweek and a GG weekend), and an issue is only readable
// on its own — unscoped, the newer one sits below every row of the live one.
export const getTypeSelections = (code, couponId = null) =>
  get(`/coupons/types/${code}/selections${couponId ? `?coupon_id=${couponId}` : ''}`)
// A bundle coupon's ready-made accas, best value first. A separate call from
// selections because the unit is the BUNDLE: the flat row list would show every
// leg and none of the bets they belong to.
export const getTypeBundles = (code, couponId = null) =>
  get(`/coupons/types/${code}/bundles${couponId ? `?coupon_id=${couponId}` : ''}`)
export const refreshCoupon = (id) => post(`/coupons/${id}/refresh`, {})

export async function uploadCoupon(typeCode, period, file, name = null) {
  const form = new FormData()
  form.append('type_code', typeCode)
  form.append('period', period)
  if (name) form.append('name', name)
  form.append('file', file)
  const res = await send('/upload', { method: 'POST', body: form }, 'POST')
  if (res.status === 401) { redirectToLogin(); throw new ApiError('Not authenticated', { status: 401, path: '/upload' }) }
  if (!res.ok) throw await fail(res, '/upload', 'POST')
  return res.json()
}

export const rescanCoupon = (couponId) => post(`/upload/${couponId}/rescan`, {})

export async function editLeg(couponId, legId, fields) {
  const path = `/coupons/${couponId}/legs/${legId}`
  const res = await send(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  }, 'PATCH')
  if (res.status === 401) { redirectToLogin(); throw new ApiError('Not authenticated', { status: 401, path }) }
  if (!res.ok) throw await fail(res, path, 'PATCH')
  return res.json()
}

// AccaBonus arbs
export const getAccaBonusFeed = (minEdge = -0.02) =>
  get(`/accabonus/feed?min_edge=${minEdge}`)

// Cards & corners
// Why the cards screen looks the way it does. An empty feed has several
// distinct causes that are identical from the outside, and one of them
// ("nothing cleared the guards") is a SUCCESS.
export const getCardsDiagnostics = () => get('/paddy/diagnostics')
export const getCardsFeed = (minEdge = -0.02) =>
  get(`/paddy/feed?min_edge=${minEdge}`)

// First-goalscorer value (bet365 vs de-vigged Pinnacle). Rows are produced by
// the scheduler near kickoff and stored, so this just reads the current set.
export const getGoalscorerFeed = () => get('/goalscorers/feed')

// Private P&L bet tracker (per-user ledger). Each user has two independent
// ledgers, `category` = "dogs" | "virtuals", carried as a path segment.
export const getBets = (category) => get(`/pnl/${category}/bets`)
// Two entry modes — flat net result `{ amount }`, or `{ stake, returned }`
// when both figures are to hand. Pass exactly one shape; the API rejects both.
export const addBet = (category, payload) => post(`/pnl/${category}/bets`, payload)
export async function deleteBet(category, id) {
  const path = `/pnl/${category}/bets/${id}`
  const res = await send(path, { method: 'DELETE' }, 'DELETE')
  if (res.status === 401) { redirectToLogin(); throw new ApiError('Not authenticated', { status: 401, path }) }
  if (!res.ok && res.status !== 204) throw await fail(res, path, 'DELETE')
}

// Shop visit route tracker (per-user). One GET returns both the route's model
// (the £500 × 5 × 4 × 40 arithmetic) and every logged visit — the page derives
// its counters from those, so there is nothing else to fetch.
export const getShopRoute = () => get('/shop')
export const logShopVisit = () => post('/shop/visits', {})
export const deleteShopVisit = (id) => del(`/shop/visits/${id}`)
export const resetShopRoute = () => del('/shop/visits')

async function del(path) {
  const res = await send(path, { method: 'DELETE' }, 'DELETE')
  if (res.status === 401) { redirectToLogin(); throw new ApiError('Not authenticated', { status: 401, path }) }
  if (!res.ok && res.status !== 204) throw await fail(res, path, 'DELETE')
}

// AW 26 — the acca project's slips
export const getAwSlips = () => get('/aw26/slips')
export async function uploadAwSlip(offer, file) {
  const form = new FormData()
  form.append('offer', offer)
  form.append('file', file)
  const res = await send('/aw26/slips', { method: 'POST', body: form }, 'POST')
  if (res.status === 401) { redirectToLogin(); throw new ApiError('Not authenticated', { status: 401, path: '/aw26/slips' }) }
  if (!res.ok) throw await fail(res, '/aw26/slips', 'POST')
  return res.json()
}
export async function editAwSlip(id, fields) {
  const path = `/aw26/slips/${id}`
  const res = await send(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  }, 'PATCH')
  if (res.status === 401) { redirectToLogin(); throw new ApiError('Not authenticated', { status: 401, path }) }
  if (!res.ok) throw await fail(res, path, 'PATCH')
  return res.json()
}
export const deleteAwSlip = (id) => del(`/aw26/slips/${id}`)

// Scraper control (routers/scraper.py): leagues, markets and per-site timings
// the scraper box reads, and its last status report. Every edit answers with
// the whole state, so the page just re-renders from it.
export const getScraper = () => get('/scraper')
async function put(path, body) {
  const res = await send(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, 'PUT')
  if (res.status === 401) { redirectToLogin(); throw new ApiError('Not authenticated', { status: 401, path }) }
  if (!res.ok) throw await fail(res, path, 'PUT')
  return res.json()
}
export const putScraperLeague = (id, fields) => put(`/scraper/leagues/${id}`, fields)
export const postScraperLeague = (fields) => post('/scraper/leagues', fields)
export const deleteScraperLeague = (id) => del(`/scraper/leagues/${id}`)
export const putScraperMarket = (key, fields) => put(`/scraper/markets/${key}`, fields)
export const putScraperTiming = (site, fields) => put(`/scraper/timings/${site}`, fields)
