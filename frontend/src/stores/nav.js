import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import * as api from '../lib/api.js'
import {
  cacheOfflinePack,
  loadStoredOfflinePack,
  verifyOfflinePack,
} from '../lib/offlinePack.js'
import { fetchRouteWithRetry, partitionSettledRoutes } from '../lib/routeRetry.js'

import { getDeviceUUID } from '../lib/uuid.js'

// 30分
const PLAN_TTL = 30 * 60 * 1000

const emptyPackImport = () => ({
  state: 'idle',
  route: { done: 0, total: 1, failed: 0 },
  audio: { done: 0, total: 0, failed: 0 },
  tiles: { done: 0, total: 0, failed: 0, quotaExceeded: false },
  verification: null,
  error: null,
})

const createRouteCacheKey = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    try {
      return crypto.randomUUID()
    } catch {}
  }
  const rand = Math.random().toString(16).slice(2, 10)
  return `route-${Date.now()}-${rand}`
}

export const useNavStore = defineStore('nav', () => {
  // --- State ---
  const lang = ref('ja')
  const origin = ref(null)
  const waypointsByIds = ref([])
  const plan = ref(null)
  const isRouteLoading = ref(false)
  const isNavigating = ref(false)
  const error = ref(null)
  const deviceId = ref(null)
  const spots = ref([])
  const spotsEtag = ref(null)
  const candidates = ref(null)
  const currentItinerary = ref(null)
  const packJob = ref(null)
  const packMetadata = ref(null)
  const packStartedAt = ref(null)
  const packImport = ref(emptyPackImport())
  const installedPack = ref(null)
  const isTourMode = ref(false)

  let spotsRequest = null
  let itineraryApplySequence = 0
  let packPollTimer = null

  // --- Getters ---
  const isRouteReady = computed(() => !!plan.value?.route)
  const isNavigationReady = computed(() => !!plan.value?.manifest)
  const isPackGenerating = computed(() => ['queued', 'running'].includes(packJob.value?.state))
  const isPackImporting = computed(() => ['route', 'audio', 'tiles', 'verify'].includes(packImport.value.state))
  const waypoints = computed(() => plan.value?.waypoints_info || [])
  const alongPois = computed(() => plan.value?.along_pois || [])

  // --- Actions ---

  const reset = () => {
    console.log('[NavStore] Resetting navigation state...')
    lang.value = 'ja'
    origin.value = null
    waypointsByIds.value = []
    plan.value = null
    isRouteLoading.value = false
    isNavigating.value = false
    error.value = null
    candidates.value = null
    currentItinerary.value = null
    stopPackPolling()
    packJob.value = null
    packMetadata.value = null
    packStartedAt.value = null
    packImport.value = emptyPackImport()
    installedPack.value = null
    isTourMode.value = false
    itineraryApplySequence += 1
    console.log('[NavStore] Navigation state has been reset.')
  }

  const fetchSpots = async ({ force = false } = {}) => {
    if (isTourMode.value) return spots.value
    if (!api.bearerHeaders().Authorization) return spots.value
    if (spotsRequest && !force) return spotsRequest

    const request = (async () => {
      const response = await api.getSpots(force ? null : spotsEtag.value)
      if (response.status === 200) {
        spots.value = Array.isArray(response.spots) ? response.spots : []
      } else if (response.status === 304 && spots.value.length === 0) {
        const uncached = await api.getSpots()
        spots.value = Array.isArray(uncached.spots) ? uncached.spots : []
        spotsEtag.value = uncached.etag
        return spots.value
      }
      spotsEtag.value = response.etag
      return spots.value
    })()
    spotsRequest = request

    try {
      return await request
    } finally {
      if (spotsRequest === request) spotsRequest = null
    }
  }

  const spotInfo = (spotId) => {
    const spot = spots.value.find((value) => value.spot_id === spotId)
    if (!spot) return { spot_id: spotId, name: spotId }
    const name = spot.name_ja || spot.name || spotId
    return {
      ...spot,
      name,
      names: spot.names || { ja: name },
    }
  }

  const setCandidates = (state) => {
    if (!state || state.kind !== 'candidates') return
    candidates.value = state
  }

  const applyItineraryState = async (state) => {
    if (!state || state.kind !== 'itinerary') return
    if (
      state.phase === 'provisional'
      && currentItinerary.value?.phase === 'final'
      && currentItinerary.value?.version === state.version
    ) return

    const applySequence = ++itineraryApplySequence
    const itineraryChanged = currentItinerary.value?.version !== state.version
    if (itineraryChanged) {
      stopPackPolling()
      packJob.value = null
      packMetadata.value = null
      packStartedAt.value = null
      packImport.value = emptyPackImport()
      installedPack.value = null
      isTourMode.value = false
      isNavigating.value = false
    }
    currentItinerary.value = state
    const days = Array.isArray(state.itinerary?.days) ? state.itinerary.days : []
    const items = days.flatMap((day) => Array.isArray(day.items) ? day.items : [])
    const spotIds = items.map((item) => item.spot_id).filter(Boolean)
    const routeIds = [...new Set(
      items.map((item) => item.leg_from_prev?.route_id).filter(Boolean)
    )]
    const routeKey = routeIds.join(',')
    const existingRoute = plan.value?.routeKey === routeKey ? plan.value?.route : null

    waypointsByIds.value = spotIds
    plan.value = {
      ...(plan.value || {}),
      itinerary_state: state,
      itinerary_version: state.version,
      waypoints_info: spotIds.map(spotInfo),
      route_ids: routeIds,
      routeKey,
      route: existingRoute,
      along_pois: [],
      pack_id: itineraryChanged ? null : plan.value?.pack_id,
      pack_state: itineraryChanged ? null : plan.value?.pack_state,
      manifest_url: itineraryChanged ? null : plan.value?.manifest_url,
      manifest: itineraryChanged ? null : plan.value?.manifest,
      assets: itineraryChanged ? [] : (plan.value?.assets || []),
      missing: itineraryChanged ? [] : (plan.value?.missing || []),
      createdAt: Date.now(),
      cacheKey: createRouteCacheKey(),
    }

    if (routeIds.length === 0 || existingRoute) return

    try {
      // ADR-0020: サーバー契約上は commit 済みのはずだが、防御として
      // 404 のときだけ短い再試行を行う(frontend_nav.md §3 表 #6)。
      // 2026-08-04(レビュー是正・M-5): `Promise.all` ではなく
      // `Promise.allSettled` を使い、1 本の失敗で成功した他レッグの描画
      // まで消さない。欠けたレッグは経路縮退(route_degraded)と同じ扱い。
      const settled = await Promise.allSettled(
        routeIds.map((routeId) => fetchRouteWithRetry(routeId, { getRoute: api.getRoute }))
      )
      if (applySequence !== itineraryApplySequence) return
      const { routes, failures } = partitionSettledRoutes(routeIds, settled)
      plan.value = {
        ...plan.value,
        route: {
          type: 'FeatureCollection',
          features: routes.flatMap((route) => route.geojson?.features || []),
        },
        segments: routes.flatMap((route) => route.segments || []),
        legs: routes,
      }
      if (failures.length) {
        for (const failure of failures) {
          console.error('[NavStore] Failed to restore a route leg:', failure.routeId, failure.error)
        }
        error.value = `一部の経路(${failures.length}件)を読み込めませんでした`
      }
    } catch (routeError) {
      if (applySequence !== itineraryApplySequence) return
      console.error('[NavStore] Failed to restore itinerary routes:', routeError)
      error.value = routeError.message || '旅程の経路を読み込めませんでした'
    }
  }

  const fetchRoute = async (planOptions, opts = {}) => {
    const { navigate = true } = opts
    reset()
    isRouteLoading.value = true
    error.value = null

    lang.value = planOptions.language
    origin.value = planOptions.origin
    waypointsByIds.value = planOptions.waypoints?.map((value) => value.spot_id) ?? []

    try {
      const routeData = await api.createRoutePlan(planOptions)

      plan.value = {
        planOptions,
        route: routeData.feature_collection,
        segments: routeData.segments,
        legs: routeData.legs,
        waypoints_info: waypointsByIds.value.map(spotInfo),
        pack_id: null,
        along_pois: [],
        assets: [],
        manifest_url: null,
        createdAt: Date.now(),
        cacheKey: createRouteCacheKey(),
      }

      if (navigate) {
        const { default: router } = await import('@/router')
        await router.push('/nav')
      }
    } catch (routeError) {
      console.error('Failed to fetch route', routeError)
      error.value = routeError.message || 'ルート計算に失敗しました'
    } finally {
      isRouteLoading.value = false
    }
  }

  const startGuidance = async () => {
    if (isTourMode.value || !isRouteReady.value || isNavigating.value) return

    const itineraryVersion = Number(plan.value?.itinerary_version ?? currentItinerary.value?.version)
    if (!Number.isInteger(itineraryVersion) || itineraryVersion < 1) {
      error.value = 'パック生成には確定済みの旅程版が必要です'
      return
    }

    isNavigating.value = true
    error.value = null
    try {
      const requested = await api.createPlan(itineraryVersion)
      const sameJob = packJob.value?.job_id === requested.job_id
      packStartedAt.value = sameJob && packStartedAt.value ? packStartedAt.value : Date.now()
      packMetadata.value = null
      packJob.value = {
        ...requested,
        progress: {
          done: 0,
          total: requested.total ?? 0,
          failed: 0,
        },
        failures: [],
      }
      plan.value = { ...plan.value, pack_id: requested.pack_id }
      await pollPackJob()
    } catch (navigationError) {
      console.error('Failed to generate navigation plan', navigationError)
      error.value = navigationError.message || 'ナビゲーションの生成に失敗しました'
      isNavigating.value = false
    }
  }

  const pollPackJob = async () => {
    stopPackPolling()
    if (isTourMode.value) return
    const jobId = packJob.value?.job_id
    if (!jobId) return
    try {
      const status = await api.getPackJob(jobId)
      packJob.value = { ...packJob.value, ...status, job_id: jobId }
      if (['queued', 'running'].includes(status.state)) {
        isNavigating.value = true
        packPollTimer = window.setTimeout(() => { void pollPackJob() }, 2000)
        return
      }
      isNavigating.value = false
      if (status.state === 'ready' || status.state === 'partial') {
        await loadGeneratedPack(status.pack_id)
      } else if (status.state === 'failed') {
        packMetadata.value = await api.getPack(status.pack_id)
        const reason = status.failures?.[0]?.detail || status.failures?.[0]?.reason
        error.value = reason || '案内パックを作成できませんでした'
      }
    } catch (pollError) {
      console.error('Failed to poll pack job', pollError)
      error.value = pollError.message || 'パックの進捗を取得できませんでした'
      if (isNavigating.value) {
        packPollTimer = window.setTimeout(() => { void pollPackJob() }, 2000)
      }
    }
  }

  const applyLoadedPack = ({ packId, metadata, manifest, manifestUrl, route }) => {
    const normalizedManifestUrl = new URL(manifestUrl, window.location.origin).toString()
    const visitById = new Map((manifest.spots || []).map((spot) => [spot.spot_id, spot]))
    const itinerarySpotIds = [...new Set(
      (manifest.days || []).flatMap((day) => (day.items || []).map((item) => item.spot_id))
    )]
    const visitSpots = (itinerarySpotIds.length ? itinerarySpotIds : [...visitById.keys()])
      .map((spotId) => visitById.get(spotId))
      .filter(Boolean)
      .map((spot) => ({ ...spot, name: spot.name_ja || spot.spot_id }))
    const alongPois = (manifest.along || []).map((spot, index) => ({
      ...spot,
      name: spot.name_ja || spot.spot_id,
      order_index: index,
    }))
    const assets = [...(manifest.spots || []), ...(manifest.along || [])].flatMap((spot) =>
      Object.entries(spot.assets || {}).map(([variant, asset]) => ({
        spot_id: spot.spot_id,
        variant,
        situation: variant === 'base' ? null : variant,
        audio_url: asset.file ? new URL(asset.file, normalizedManifestUrl).toString() : null,
        text: asset.text || null,
      }))
    )
    plan.value = {
      ...plan.value,
      pack_id: packId,
      pack_state: metadata?.state || manifest.state,
      manifest_url: normalizedManifestUrl,
      manifest,
      playback_rules: manifest.playback_rules,
      route,
      polyline: null,
      waypoints_info: visitSpots,
      along_pois: alongPois,
      assets,
      missing: metadata?.missing || manifest.missing || [],
      createdAt: Date.now(),
      cacheKey: `pack-${packId}-${manifest.pack_epoch}`,
    }
  }

  const installLoadedPack = async ({ manifest, manifestUrl, route }) => {
    packImport.value = {
      ...emptyPackImport(),
      state: 'route',
    }
    try {
      const result = await cacheOfflinePack({
        manifest,
        manifestUrl,
        route,
        onProgress(progress) {
          packImport.value = {
            ...packImport.value,
            ...progress,
            error: null,
          }
        },
      })
      installedPack.value = result.record
      packImport.value = {
        ...packImport.value,
        state: result.record?.state || 'partial',
        verification: result.verification,
      }
      return result
    } catch (installError) {
      packImport.value = {
        ...packImport.value,
        state: 'failed',
        error: installError.message || '端末への取り込みに失敗しました',
      }
      error.value = packImport.value.error
      return null
    }
  }

  const restoreOfflinePack = async (packId = null) => {
    try {
      const stored = await loadStoredOfflinePack(packId)
      if (!stored?.manifest || !stored.route) return false
      installedPack.value = stored.record
      const restoredState = stored.verification.routeCached
        && stored.verification.missing.length === 0
        && !(stored.record.tiles?.failed > 0)
        ? 'ready'
        : 'partial'
      packImport.value = {
        ...emptyPackImport(),
        state: restoredState,
        audio: stored.record.audio || {
          done: stored.verification.cached,
          total: stored.verification.expected,
          failed: stored.verification.missing.length,
        },
        tiles: stored.record.tiles || emptyPackImport().tiles,
        route: {
          done: 1,
          total: 1,
          failed: stored.verification.routeCached ? 0 : 1,
        },
        verification: stored.verification,
      }
      packMetadata.value = {
        state: stored.manifest.state,
        manifest_url: stored.manifestUrl,
        missing: stored.manifest.missing || [],
      }
      applyLoadedPack({
        packId: stored.manifest.pack_id,
        metadata: packMetadata.value,
        manifest: stored.manifest,
        manifestUrl: stored.manifestUrl,
        route: stored.route,
      })
      if (packId) packJob.value = null
      return true
    } catch {
      return false
    }
  }

  const loadGeneratedPack = async (packId) => {
    if (await restoreOfflinePack(packId)) return
    const metadata = await api.getPack(packId)
    packMetadata.value = metadata
    if (!metadata.manifest_url) return
    const manifest = await api.fetchPackJson(metadata.manifest_url)
    const manifestUrl = new URL(metadata.manifest_url, window.location.origin)
    const routeUrl = new URL('route.geojson', manifestUrl)
    const route = await api.fetchPackJson(routeUrl.toString())
    applyLoadedPack({
      packId,
      metadata,
      manifest,
      manifestUrl: manifestUrl.toString(),
      route,
    })
    await installLoadedPack({ manifest, manifestUrl: manifestUrl.toString(), route })
  }

  const verifyInstalledPack = async () => {
    const manifest = plan.value?.manifest
    const manifestUrl = plan.value?.manifest_url
    if (!manifest || !manifestUrl) return null
    const verification = await verifyOfflinePack(manifest, manifestUrl)
    const verifiedState = verification.routeCached
      && verification.missing.length === 0
      && !(packImport.value.tiles?.failed > 0)
      ? 'ready'
      : 'partial'
    packImport.value = {
      ...packImport.value,
      state: verifiedState,
      verification,
    }
    return verification
  }

  const enterTourMode = async () => {
    const verification = await verifyInstalledPack()
    if (!installedPack.value || !verification?.routeCached) {
      error.value = '先に案内パックを端末へ取り込んでください'
      return false
    }
    stopPackPolling()
    isTourMode.value = true
    error.value = null
    return true
  }

  const exitTourMode = () => {
    isTourMode.value = false
  }

  function stopPackPolling() {
    if (packPollTimer != null) {
      clearTimeout(packPollTimer)
      packPollTimer = null
    }
  }

  const resumePackPolling = () => {
    if (isTourMode.value) return
    if (!['queued', 'running'].includes(packJob.value?.state)) return
    isNavigating.value = true
    if (!packStartedAt.value) packStartedAt.value = Date.now()
    void pollPackJob()
  }

  if (plan.value?.createdAt && Date.now() - plan.value.createdAt > PLAN_TTL) {
    console.log('Plan expired, resetting...')
    reset()
  }

  const initializeDeviceId = () => {
    deviceId.value = getDeviceUUID()
    console.log('Device ID initialized:', deviceId.value)
  }

  const setItinerary = (itinerary) => {
    if (itinerary?.kind === 'itinerary') {
      void applyItineraryState(itinerary)
      return
    }
    if (!Array.isArray(itinerary)) {
      console.warn('[NavStore] setItinerary received non-array value:', itinerary)
      return
    }
    waypointsByIds.value = itinerary
    const waypointInfo = itinerary.map(spotInfo)
    if (plan.value) {
      plan.value.waypoints_info = waypointInfo
    } else {
      plan.value = { waypoints_info: waypointInfo }
    }
  }

  return {
    // State
    lang,
    origin,
    waypointsByIds,
    plan,
    isRouteLoading,
    isNavigating,
    error,
    deviceId,
    spots,
    spotsEtag,
    candidates,
    currentItinerary,
    packJob,
    packMetadata,
    packStartedAt,
    packImport,
    installedPack,
    isTourMode,
    // Getters
    isRouteReady,
    isNavigationReady,
    isPackGenerating,
    isPackImporting,
    waypoints,
    alongPois,
    // Actions
    fetchRoute,
    startGuidance,
    resumePackPolling,
    reset,
    initializeDeviceId,
    setItinerary,
    fetchSpots,
    setCandidates,
    applyItineraryState,
    restoreOfflinePack,
    verifyInstalledPack,
    enterTourMode,
    exitTourMode,
  }
}, {
  persist: true // LocalStorageに保存
})
