// src/lib/api.js

const configuredBase = import.meta.env?.VITE_API_BASE ?? '/api/v1'
export const API_BASE = String(configuredBase || '/api/v1').replace(/\/+$/, '')

function storedToken() {
  if (typeof sessionStorage === 'undefined') return ''
  try {
    return JSON.parse(sessionStorage.getItem('user') || 'null')?.token || ''
  } catch {
    return ''
  }
}

export function apiUrl(path) {
  return `${API_BASE}/${String(path).replace(/^\/+/, '')}`
}

export function bearerHeaders(token = storedToken()) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function apiFetch(path, opts = {}) {
  const {
    auth = true,
    headers: optionHeaders = {},
    ...requestOptions
  } = opts
  const headers = new Headers(optionHeaders)

  if (requestOptions.body != null && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (auth && !headers.has('Authorization')) {
    const token = storedToken()
    if (token) headers.set('Authorization', `Bearer ${token}`)
  }

  const init = {
    method: 'GET',
    ...requestOptions,
    headers,
  }

  console.debug('[api]', init.method, path)
  const res = await fetch(apiUrl(path), init)
  const text = res.status === 204 || res.status === 304 ? '' : await res.text()
  let body = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }

  console.debug('[api]', init.method, 'status', res.status)
  if (!res.ok && res.status !== 304) {
    const detail = typeof body?.detail === 'string'
      ? body.detail
      : body?.detail?.message
    const error = new Error(detail || `HTTP ${res.status}`)
    error.status = res.status
    error.body = body
    throw error
  }
  return { status: res.status, body, headers: res.headers }
}

/** POST /routes をレッグごとに呼び、既存ナビ画面向けの形へ結合する。 */
export async function createRoutePlan(options) {
  if (!options?.origin) throw new Error('origin is required')
  const waypointIds = (options.waypoints || []).map((value) => value?.spot_id).filter(Boolean)
  if (waypointIds.length === 0) throw new Error('waypoints are required')

  const endpoints = [
    { lat: options.origin.lat, lon: options.origin.lon },
    ...waypointIds.map((spotId) => ({ spot_id: spotId })),
  ]
  if (options.return_to_origin !== false) {
    endpoints.push({ lat: options.origin.lat, lon: options.origin.lon })
  }

  const legs = await Promise.all(
    endpoints.slice(0, -1).map((from, index) => createRoute(from, endpoints[index + 1]))
  )

  return {
    feature_collection: {
      type: 'FeatureCollection',
      features: legs.flatMap((leg) => leg.geojson?.features || []),
    },
    segments: legs.flatMap((leg) => leg.segments || []),
    legs,
    waypoints_info: waypointIds.map((spotId) => ({ spot_id: spotId, name: spotId })),
  }
}

export async function createRoute(from, to) {
  const { status, body } = await apiFetch('/routes', {
    method: 'POST',
    body: JSON.stringify({ from, to }),
  })
  if (status !== 200) throw new Error(`unexpected status ${status}`)
  return body
}

export async function getRoute(routeId) {
  const { status, body } = await apiFetch(`/routes/${encodeURIComponent(routeId)}`)
  if (status !== 200) throw new Error(`unexpected status ${status}`)
  return body
}

export async function createPack(itineraryVersion, options = null) {
  const payload = { itinerary_version: itineraryVersion }
  if (options) payload.options = options
  const { status, body } = await apiFetch('/packs', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (status !== 202) throw new Error(`unexpected status ${status}`)
  return body
}

export async function getPackJob(jobId) {
  const { status, body } = await apiFetch(`/jobs/${encodeURIComponent(jobId)}`)
  if (status !== 200) throw new Error(`unexpected status ${status}`)
  return body
}

export async function getPack(packId) {
  const { status, body } = await apiFetch(`/packs/${encodeURIComponent(packId)}`)
  if (status !== 200) throw new Error(`unexpected status ${status}`)
  return body
}

export async function fetchPackJson(path) {
  const response = await fetch(path, { cache: 'no-cache' })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json()
}

// 既存の NavStore 呼び出し名は残し、非同期パック API へ接続する。
export async function createPlan(itineraryVersion, options = null) {
  return createPack(itineraryVersion, options)
}

// --- Realtime (計画フェーズ表示用) ---
export async function fetchRealtimeBySpotId(spotId, opts = {}) {
  const headers = {}
  if (Object.prototype.hasOwnProperty.call(opts, 'etag')) {
    headers['If-None-Match'] = String(opts.etag)
  }

  const { status, body } = await apiFetch(
    `/realtime/spots/${encodeURIComponent(spotId)}`,
    { headers }
  )
  if (status === 204 || status === 304) return { status, body: null }
  return { status, body }
}

// --- User Authentication ---

export async function createUser(userName) {
  const { status, body } = await apiFetch('/users', {
    auth: false,
    method: 'POST',
    body: JSON.stringify({ user_name: userName }),
  })
  if (status !== 201) throw new Error(`unexpected status ${status}`)
  return body
}

export async function loginUser(userName) {
  const { status, body } = await apiFetch('/login', {
    auth: false,
    method: 'POST',
    body: JSON.stringify({ user_name: userName }),
  })
  if (status !== 200) throw new Error(`unexpected status ${status}`)
  return body
}

export async function getThread() {
  const { status, body } = await apiFetch('/thread')
  if (status !== 200) throw new Error(`unexpected status ${status}`)
  return body
}

export async function getSpots(etag = null) {
  const headers = etag ? { 'If-None-Match': etag } : {}
  const { status, body, headers: responseHeaders } = await apiFetch('/spots', { headers })
  return {
    status,
    spots: status === 304 ? null : body,
    etag: responseHeaders.get('ETag') || etag,
  }
}

export async function getItinerary() {
  const { status, body } = await apiFetch('/itinerary')
  if (status !== 200) throw new Error(`unexpected status ${status}`)
  return body
}

export async function undoItinerary(expectedCurrentVersion) {
  const { status, body } = await apiFetch('/itinerary/undo', {
    method: 'POST',
    body: JSON.stringify({ expected_current_version: expectedCurrentVersion }),
  })
  if (status !== 200) throw new Error(`unexpected status ${status}`)
  return body
}
