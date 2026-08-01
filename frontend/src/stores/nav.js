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

  let spotsRequest = null
  let itineraryApplySequence = 0

  // --- Getters ---
  const isRouteReady = computed(() => !!plan.value?.route)
  const isNavigationReady = computed(() => !!plan.value?.assets && plan.value.assets.length > 0)
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

    isNavigating.value = true
    error.value = null
    try {
      const navigationPlan = await api.createPlan()
      plan.value = {
        ...plan.value,
        pack_id: navigationPlan.pack_id,
        along_pois: navigationPlan.along_pois,
        assets: navigationPlan.assets,
        manifest_url: navigationPlan.manifest_url,
        createdAt: Date.now(),
      }
    } catch (navigationError) {
      console.error('Failed to generate navigation plan', navigationError)
      error.value = navigationError.message || 'ナビゲーションの生成に失敗しました'
    } finally {
      isNavigating.value = false
    }
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
    // Getters
    isRouteReady,
    isNavigationReady,
    waypoints,
    alongPois,
    // Actions
    fetchRoute,
    startGuidance,
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
