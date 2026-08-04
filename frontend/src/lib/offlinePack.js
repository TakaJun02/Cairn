import { tilesForBounds } from './tiles.js'

const STORAGE_PREFIX = 'guidance.offline-pack'
const ACTIVE_PACK_KEY = `${STORAGE_PREFIX}.active`

export function manifestStorageKey(packId) {
  return `${STORAGE_PREFIX}.${packId}.manifest`
}

function recordStorageKey(packId) {
  return `${STORAGE_PREFIX}.${packId}.record`
}

function browserStorage(storage) {
  if (storage) return storage
  return typeof localStorage === 'undefined' ? null : localStorage
}

function browserCaches(cacheStorage) {
  if (cacheStorage) return cacheStorage
  return typeof caches === 'undefined' ? null : caches
}

function pageOrigin() {
  return typeof window === 'undefined' ? 'http://localhost' : window.location.origin
}

function absoluteUrl(path, base = pageOrigin()) {
  return new URL(path, base).toString()
}

function missingKeys(manifest) {
  return new Set((manifest?.missing || []).map((item) => `${item.spot_id}:${item.variant}`))
}

export function expectedAudioAssets(manifest, manifestUrl) {
  const excluded = missingKeys(manifest)
  const byUrl = new Map()
  for (const spot of [...(manifest?.spots || []), ...(manifest?.along || [])]) {
    for (const [variant, asset] of Object.entries(spot.assets || {})) {
      if (!asset?.file || excluded.has(`${spot.spot_id}:${variant}`)) continue
      const url = absoluteUrl(asset.file, manifestUrl)
      byUrl.set(url, { spotId: spot.spot_id, variant, file: asset.file, url })
    }
  }
  return [...byUrl.values()]
}

export function readStoredPackRecord(packId = null, { storage } = {}) {
  const target = browserStorage(storage)
  if (!target) return null
  try {
    const resolvedPackId = packId || target.getItem(ACTIVE_PACK_KEY)
    if (!resolvedPackId) return null
    return JSON.parse(target.getItem(recordStorageKey(resolvedPackId)) || 'null')
  } catch {
    return null
  }
}

function saveStoredPack(manifest, manifestUrl, summary, storage) {
  const target = browserStorage(storage)
  if (!target) return null
  const record = {
    pack_id: manifest.pack_id,
    pack_epoch: manifest.pack_epoch,
    manifest_url: absoluteUrl(manifestUrl),
    installed_at: Date.now(),
    ...summary,
  }
  target.setItem(manifestStorageKey(manifest.pack_id), JSON.stringify(manifest))
  target.setItem(recordStorageKey(manifest.pack_id), JSON.stringify(record))
  target.setItem(ACTIVE_PACK_KEY, String(manifest.pack_id))
  return record
}

function readStoredManifest(packId, storage) {
  const target = browserStorage(storage)
  if (!target) return null
  try {
    return JSON.parse(target.getItem(manifestStorageKey(packId)) || 'null')
  } catch {
    return null
  }
}

function snapshot(progress) {
  return {
    stage: progress.stage,
    route: { ...progress.route },
    audio: { ...progress.audio },
    tiles: { ...progress.tiles },
  }
}

async function cacheResponse(cache, url, response) {
  await cache.put(new Request(url), response.clone())
}

async function fetchAndCache(cache, url, fetchImpl, { opaque = false } = {}) {
  const request = new Request(url, opaque ? { mode: 'no-cors' } : undefined)
  const response = await fetchImpl(request)
  if (!response || (!response.ok && !(opaque && response.type === 'opaque'))) {
    throw new Error(`HTTP ${response?.status ?? 0}`)
  }
  await cache.put(request, response.clone())
}

async function runPool(items, worker, concurrency = 6) {
  let cursor = 0
  const runners = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor
      cursor += 1
      await worker(items[index], index)
    }
  })
  await Promise.all(runners)
}

export async function verifyOfflinePack(manifest, manifestUrl, options = {}) {
  const cacheStorage = browserCaches(options.cacheStorage)
  if (!manifest?.pack_id || !cacheStorage) {
    return { expected: 0, cached: 0, missing: [], routeCached: false }
  }

  const packCache = await cacheStorage.open(`packs-${manifest.pack_id}`)
  const assets = expectedAudioAssets(manifest, manifestUrl)
  const missing = []
  let cached = 0
  for (const asset of assets) {
    if (await packCache.match(new Request(asset.url), { ignoreSearch: true })) {
      cached += 1
    } else {
      missing.push(asset)
    }
  }

  const routeUrl = absoluteUrl('route.geojson', manifestUrl)
  const routeCached = !!(await packCache.match(new Request(routeUrl), { ignoreSearch: true }))
  return { expected: assets.length, cached, missing, routeCached }
}

export async function cacheOfflinePack({
  manifest,
  manifestUrl,
  route,
  onProgress = () => {},
  fetchImpl = globalThis.fetch,
  cacheStorage,
  storage,
}) {
  if (!manifest?.pack_id || !manifestUrl) throw new Error('manifest の pack_id と URL が必要です')
  const cacheApi = browserCaches(cacheStorage)
  if (!cacheApi || typeof fetchImpl !== 'function') throw new Error('Cache Storage を利用できません')

  const packCache = await cacheApi.open(`packs-${manifest.pack_id}`)
  const tileCache = await cacheApi.open('tiles-v1')
  const routeUrl = absoluteUrl('route.geojson', manifestUrl)
  const audioAssets = expectedAudioAssets(manifest, manifestUrl)
  const tileEntries = tilesForBounds(manifest.tiles)
  const progress = {
    stage: 'route',
    route: { done: 0, total: 1, failed: 0 },
    audio: { done: 0, total: audioAssets.length, failed: 0 },
    tiles: { done: 0, total: tileEntries.length, failed: 0, quotaExceeded: false },
  }
  onProgress(snapshot(progress))

  try {
    if (route) {
      const response = new Response(JSON.stringify(route), {
        headers: { 'Content-Type': 'application/geo+json' },
      })
      await cacheResponse(packCache, routeUrl, response)
    } else if (!(await packCache.match(new Request(routeUrl), { ignoreSearch: true }))) {
      await fetchAndCache(packCache, routeUrl, fetchImpl)
    }
    progress.route.done = 1
  } catch {
    progress.route.done = 1
    progress.route.failed = 1
  }
  onProgress(snapshot(progress))

  progress.stage = 'audio'
  await runPool(audioAssets, async (asset) => {
    try {
      const request = new Request(asset.url)
      if (!(await packCache.match(request, { ignoreSearch: true }))) {
        await fetchAndCache(packCache, asset.url, fetchImpl)
      }
    } catch {
      progress.audio.failed += 1
    } finally {
      progress.audio.done += 1
      onProgress(snapshot(progress))
    }
  })

  progress.stage = 'tiles'
  let stopForQuota = false
  await runPool(tileEntries, async (tile) => {
    if (stopForQuota) return
    try {
      const request = new Request(tile.url, { mode: 'no-cors' })
      if (!(await tileCache.match(request, { ignoreSearch: true }))) {
        await fetchAndCache(tileCache, tile.url, fetchImpl, { opaque: true })
      }
    } catch (error) {
      progress.tiles.failed += 1
      if (error?.name === 'QuotaExceededError') {
        progress.tiles.quotaExceeded = true
        stopForQuota = true
      }
    } finally {
      progress.tiles.done += 1
      onProgress(snapshot(progress))
    }
  }, 8)

  progress.stage = 'verify'
  onProgress(snapshot(progress))
  const verification = await verifyOfflinePack(manifest, manifestUrl, { cacheStorage: cacheApi })
  const state = verification.routeCached && verification.missing.length === 0
    && progress.tiles.failed === 0 ? 'ready' : 'partial'
  const record = saveStoredPack(manifest, manifestUrl, {
    state,
    audio: progress.audio,
    tiles: progress.tiles,
    verification,
  }, storage)

  progress.stage = state
  onProgress(snapshot(progress))
  return { record, progress: snapshot(progress), verification }
}

export async function loadStoredOfflinePack(packId = null, options = {}) {
  const record = readStoredPackRecord(packId, options)
  if (!record) return null
  const manifest = readStoredManifest(record.pack_id, options.storage)
  const cacheStorage = browserCaches(options.cacheStorage)
  if (!manifest || !cacheStorage || manifest.pack_epoch !== record.pack_epoch) return null

  const packCache = await cacheStorage.open(`packs-${record.pack_id}`)
  const routeUrl = absoluteUrl('route.geojson', record.manifest_url)
  const response = await packCache.match(new Request(routeUrl), { ignoreSearch: true })
  let route = null
  try {
    route = response ? await response.json() : null
  } catch {
    route = null
  }
  const verification = await verifyOfflinePack(manifest, record.manifest_url, { cacheStorage })
  return { record, manifest, manifestUrl: record.manifest_url, route, verification }
}

export function playbackVariants(manifest, realtimeDoc) {
  const rules = manifest?.playback_rules || {}
  const variants = ['base']
  const order = Array.isArray(rules.order) ? rules.order : ['base', 'weather', 'congestion']
  for (const layer of order) {
    if (layer === 'base') continue
    const code = Number(layer === 'weather' ? realtimeDoc?.w : realtimeDoc?.c)
    if (!Number.isInteger(code) || code === 0 || code === 0x0f) continue
    const variant = rules[layer]?.[String(code)]
    if (variant && !variants.includes(variant)) variants.push(variant)
  }
  return variants
}

export function advanceArrivalState(armed, distanceM, triggerRadiusM) {
  const distance = Number(distanceM)
  const radius = Number(triggerRadiusM)
  if (!Number.isFinite(distance) || !Number.isFinite(radius) || radius <= 0) {
    return { armed: !!armed, triggered: false }
  }
  if (!armed && distance > radius * 2) return { armed: true, triggered: false }
  if (armed && distance <= radius) return { armed: false, triggered: true }
  return { armed: !!armed, triggered: false }
}
