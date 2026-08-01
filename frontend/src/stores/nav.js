import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import * as api from '@/lib/api'

import { getDeviceUUID } from '@/lib/uuid'

// 30分
const PLAN_TTL = 30 * 60 * 1000

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

  let spotsRequest = null
  let itineraryApplySequence = 0
  let packPollTimer = null

  // --- Getters ---
  const isRouteReady = computed(() => !!plan.value?.route)
  const isNavigationReady = computed(() => !!plan.value?.manifest)
  const isPackGenerating = computed(() => ['queued', 'running'].includes(packJob.value?.state))
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
    itineraryApplySequence += 1
    console.log('[NavStore] Navigation state has been reset.')
  }

  const fetchSpots = async ({ force = false } = {}) => {
    if (spotsRequest && !force) return spotsRequest

    spotsRequest = (async () => {
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

    try {
      return await spotsRequest
    } catch (fetchError) {
      spotsRequest = null
      throw fetchError
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
      const routes = await Promise.all(routeIds.map((routeId) => api.getRoute(routeId)))
      if (applySequence !== itineraryApplySequence) return
      plan.value = {
        ...plan.value,
        route: {
          type: 'FeatureCollection',
          features: routes.flatMap((route) => route.geojson?.features || []),
        },
        segments: routes.flatMap((route) => route.segments || []),
        legs: routes,
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
    if (!isRouteReady.value || isNavigating.value) return

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

  const loadGeneratedPack = async (packId) => {
    const metadata = await api.getPack(packId)
    packMetadata.value = metadata
    if (!metadata.manifest_url) return
    const manifest = await api.fetchPackJson(metadata.manifest_url)
    const manifestUrl = new URL(metadata.manifest_url, window.location.origin)
    const routeUrl = new URL('route.geojson', manifestUrl)
    const route = await api.fetchPackJson(routeUrl.toString())
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
        audio_url: asset.file ? new URL(asset.file, manifestUrl).toString() : null,
        text: asset.text || null,
      }))
    )
    plan.value = {
      ...plan.value,
      pack_id: packId,
      pack_state: metadata.state,
      manifest_url: metadata.manifest_url,
      manifest,
      playback_rules: manifest.playback_rules,
      route,
      waypoints_info: visitSpots,
      along_pois: alongPois,
      assets,
      missing: metadata.missing || manifest.missing || [],
      createdAt: Date.now(),
    }
  }

  function stopPackPolling() {
    if (packPollTimer != null) {
      clearTimeout(packPollTimer)
      packPollTimer = null
    }
  }

  const resumePackPolling = () => {
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
    // Getters
    isRouteReady,
    isNavigationReady,
    isPackGenerating,
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
  }
}, {
  persist: true // LocalStorageに保存
})
