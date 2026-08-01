<template>
  <div class="nav-view">
    <button
      v-if="isDebug"
      class="debug-toggle"
      type="button"
      @click="isDebugPanelVisible ? hideDebugPanel() : showDebugPanel()"
    >
      <span class="sr-only">{{ isDebugPanelVisible ? 'デバッグパネルを閉じる' : 'デバッグパネルを開く' }}</span>
      <span aria-hidden="true" class="debug-toggle__dot"></span>
    </button>

    <div v-if="isDebug && isDebugPanelVisible" class="debug-panel" role="group">
      <div class="debug-panel__header">
        <h4>デバッグ用パネル</h4>
        <button type="button" class="debug-panel__close" @click="hideDebugPanel" aria-label="デバッグパネルを閉じる">
          ×
        </button>
      </div>
      <div class="debug-panel__row">
        <input
          :value="debugLat"
          type="number"
          step="0.000001"
          placeholder="lat"
          class="debug-panel__input"
          readonly
        />
        <input
          :value="debugLng"
          type="number"
          step="0.000001"
          placeholder="lng"
          class="debug-panel__input"
          readonly
        />
      </div>
      <div class="debug-panel__row">
        <button type="button" class="debug-panel__action" @click="startJourney()" :disabled="isJourneyInProgress">
          ▶ Start
        </button>
        <button type="button" class="debug-panel__action" @click="stopJourney()" :disabled="!isJourneyInProgress">
          ❚❚ Pause
        </button>
        <button type="button" class="debug-panel__action" @click="resetJourney()">
          ↩ Reset
        </button>
      </div>
      <p class="debug-panel__status">
        現在地:
        <span v-if="currentPos">{{ currentPos.lat.toFixed(4) }}, {{ currentPos.lng.toFixed(4) }}</span>
        <span v-else>未取得</span>
      </p>
    </div>

    <div v-if="isRouteReady" class="nav-container">
      <div class="map-wrapper">
        <NavMap
          ref="navMap"
          :plan="plan"
          :current-pos="currentPos"
          @user-pan="handleUserPan"
        />
        <div
          v-if="playbackState"
          class="audio-caption"
          :class="{
            'is-loading': playbackState.isLoading && !playbackState.error,
            'has-error': !!playbackState.error
          }"
          role="status"
          aria-live="polite"
        >
          <div class="audio-caption__header">
            <div
              class="audio-caption__badge"
              :class="{
                'audio-caption__badge--loading': playbackState.isLoading && !playbackState.error,
                'audio-caption__badge--error': playbackState.error
              }"
            >
              <svg
                class="audio-caption__badge-icon"
                width="22"
                height="22"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M5 9v6h4l5 4V5L9 9H5z" />
                <path d="M16 10.82a3 3 0 0 1 0 2.36" />
                <path d="M19 9a6 6 0 0 1 0 6" />
              </svg>
            </div>
            <div class="audio-caption__meta">
              <span class="audio-caption__label">
                <template v-if="playbackState.error">エラー</template>
                <template v-else-if="playbackState.isLoading">読み込み中</template>
                <template v-else>音声ガイド</template>
              </span>
              <span class="audio-caption__title">{{ playbackState.name }}</span>
            </div>
            <div v-if="playbackState.error" class="audio-caption__alert" aria-hidden="true">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 9v4" />
                <path d="M12 17h.01" />
                <path d="M21 18H3l9-15 9 15z" />
              </svg>
            </div>
          </div>

          <div
            class="audio-caption__body"
            :class="{ 'audio-caption__body--error': playbackState.error }"
          >
            <template v-if="playbackState.error">
              {{ playbackState.error }}
            </template>
            <template v-else-if="playbackState.isLoading">
              原稿を読み込み中...
            </template>
            <template v-else-if="playbackState.text">
              {{ playbackState.text }}
            </template>
            <template v-else>
              テキスト情報は提供されていません。
            </template>
          </div>

          <div
            class="audio-caption__wave"
            :class="{ 'is-active': !playbackState.error && !playbackState.isLoading }"
            aria-hidden="true"
          >
            <span v-for="i in 4" :key="i" />
          </div>
        </div>

        <div class="map-actions">
          <button
            type="button"
            class="map-action-btn"
            :class="{ 'is-following': isFollowMode }"
            :disabled="!currentPos"
            @click="isFollowMode ? disableFollowMode() : enableFollowMode()"
            :title="isFollowMode ? '追従を停止' : '現在地に追従'"
          >
            <span class="map-action-btn__halo" aria-hidden="true"></span>
            <svg class="icon-location" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 19c-3.87 0-7-3.13-7-7s3.13-7 7-7 7 3.13 7 7-3.13 7-7 7z"/>
              <path d="M12 8v8M8 12h8"/>
              <circle class="icon-location-dot" cx="12" cy="12" r="2.5" fill="currentColor" />
            </svg>
            <span class="map-action-btn__label">Follow</span>
          </button>
        </div>
      </div>

      <div class="top-left-ui-area">
        <div class="controls">
          <div v-if="isTourMode" class="tour-mode-banner" role="status">
            <div>
              <strong>観光モード</strong>
              <span>{{ realtimeStatusText }}</span>
            </div>
            <button type="button" @click="leaveTourMode">観光モードを終了</button>
          </div>

          <div v-if="!isNavigationReady || packJob" class="start-nav-panel">
            <button
              v-if="!packJob || ['partial', 'failed'].includes(packJob.state)"
              @click="startGuidance"
              :disabled="isNavigating"
              class="start-nav-button"
              :class="{ 'is-loading': isNavigating }"
            >
              <span class="start-nav-button__spark"></span>
              <span class="start-nav-button__inner">
                <svg
                  class="start-nav-button__icon"
                  width="20"
                  height="20"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  aria-hidden="true"
                >
                  <path d="M12 2c2.5 2.5 4 5.5 4 8 0 4.5-4 8-4 8s-4-3.5-4-8c0-2.5 1.5-5.5 4-8Z" />
                  <path d="M12 14v4" />
                  <path d="M9 18h6" />
                </svg>
                <span class="start-nav-button__label">
                  <template v-if="isNavigating">パックを生成中…</template>
                  <template v-else-if="packJob?.state === 'partial'">不足分を再生成</template>
                  <template v-else-if="packJob?.state === 'failed'">もう一度生成</template>
                  <template v-else>端末に取り込む</template>
                </span>
              </span>
              <span class="start-nav-button__progress" aria-hidden="true">
                <span></span><span></span><span></span><span></span>
              </span>
            </button>
            <div v-if="packJob" class="pack-progress" role="status" aria-live="polite">
              <div class="pack-progress__header">
                <span class="pack-state-badge" :class="`is-${packJob.state}`">
                  {{ packStateLabel }}
                </span>
                <span class="pack-progress__elapsed">{{ packElapsedText }}</span>
              </div>
              <div class="pack-progress__counts">
                <strong>{{ packProgress.done }} / {{ packProgress.total }}</strong>
                <span>失敗 {{ packProgress.failed }} 件</span>
              </div>
              <div class="pack-progress__bar" aria-hidden="true">
                <span :style="{ width: `${packProgressPercent}%` }"></span>
              </div>
              <p v-if="packJob.state === 'ready'" class="pack-progress__message is-ready">
                端末への取り込み準備ができました
              </p>
              <p v-else-if="packJob.state === 'partial'" class="pack-progress__message is-partial">
                一部の案内を作れませんでした（{{ packFailures.length }} 件）
              </p>
              <p v-else-if="packJob.state === 'failed'" class="pack-progress__message is-failed">
                案内パックを作成できませんでした
              </p>
              <details v-if="packFailures.length" class="pack-progress__missing">
                <summary>作れなかった案内の内訳</summary>
                <ul>
                  <li v-for="(failure, index) in packFailures" :key="`${failure.spot_id}-${failure.variant}-${index}`">
                    {{ failure.spot_id || 'パック全体' }}
                    <template v-if="failure.variant"> / {{ failure.variant }}</template>
                    — {{ failure.reason }}
                  </li>
                </ul>
              </details>
            </div>
            <div v-if="navError" class="error-box">
              エラー: {{ navError }}
            </div>
          </div>

          <div v-if="isNavigationReady" class="offline-pack-panel" role="status" aria-live="polite">
            <div class="offline-pack-panel__header">
              <strong>オフライン資材</strong>
              <span>{{ packImportStateLabel }}</span>
            </div>
            <div class="offline-pack-panel__progress">
              <span>音声 {{ packImport.audio.done }} / {{ packImport.audio.total }}</span>
              <span>タイル {{ packImport.tiles.done }} / {{ packImport.tiles.total }}</span>
            </div>
            <p v-if="packImport.verification">
              自己検証: {{ packImport.verification.cached }} / {{ packImport.verification.expected }} 本
              <strong v-if="packImport.verification.missing.length" class="offline-pack-panel__warning">
                （{{ packImport.verification.missing.length }} 本足りません）
              </strong>
              <strong v-else>（完了）</strong>
            </p>
            <p v-if="packImport.tiles.quotaExceeded" class="offline-pack-panel__warning">
              保存容量の上限に達したため、表示件数の地点でタイル取得を停止しました。
            </p>
            <p v-if="packImport.verification && !packImport.verification.routeCached" class="offline-pack-panel__warning">
              経路データが足りません。観光モードは開始できません。
            </p>
            <p v-if="packImport.error" class="offline-pack-panel__warning">{{ packImport.error }}</p>
            <div class="offline-pack-panel__actions">
              <button type="button" :disabled="isPackImporting" @click="verifyOfflineAssets">
                取り込みを再検証
              </button>
              <button
                v-if="!isTourMode"
                type="button"
                class="offline-pack-panel__primary"
                :disabled="!canEnterTourMode || isPackImporting"
                @click="beginTourMode"
              >
                観光モードを開始
              </button>
            </div>
          </div>
          
          <div v-if="isRouteReady" class="control-buttons">
            <button @click="togglePolling" class="control-btn data-sync-btn" :class="{'is-active': isPollingEnabled}" title="リアルタイム情報">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 3v6h6" />
                <path d="M21 21v-6h-6" />
                <path d="M21 3 14.12 9.88" />
                <path d="M3 21 9.88 14.12" />
              </svg>
              <span class="data-sync-btn__label">Live Sync</span>
            </button>
            <div v-if="isNavigationReady" class="lora-panel" :class="{ 'is-connected': isLoraConnected, 'is-connecting': isLoraConnecting }">
              <div class="lora-panel__icon" aria-hidden="true">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M12 2v4" />
                  <path d="M5.5 10.5a8.5 8.5 0 0 1 13 0" />
                  <path d="M8.5 13.5a4.5 4.5 0 0 1 7 0" />
                  <circle cx="12" cy="18" r="2" />
                </svg>
              </div>
              <div class="lora-panel__body">
                <span class="lora-panel__label">LoRa Link</span>
                <span class="lora-panel__status">{{ isLoraConnected ? 'Connected' : (isLoraConnecting ? 'Negotiating…' : 'Standby') }}</span>
              </div>
              <button
                @click="isLoraConnected ? disconnectLoraDevice() : connectLoraDevice()"
                :disabled="isLoraConnecting"
                class="lora-toggle-btn"
              >
                <span class="lora-toggle-btn__dot" :class="{ 'is-active': isLoraConnected, 'is-busy': isLoraConnecting }"></span>
                <span class="lora-toggle-btn__text">{{ isLoraConnected ? 'Disconnect' : 'Connect' }}</span>
              </button>
            </div>
          </div>
        </div>

        <div class="spot-list-panel">
          <button @click="toggleSpotList" class="spot-list-toggle">
            <div class="spot-list-toggle__left">
              <span class="spot-list-toggle__eyebrow">Spots</span>
              <span class="spot-list-toggle__title">Spots List</span>
            </div>
            <svg class="chevron-icon" :class="{'is-open': isSpotListVisible}" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="6 9 12 15 18 9"></polyline>
            </svg>
          </button>
          <div class="spot-list-content" :class="{'is-open': isSpotListVisible}">
            <div class="spot-list-content-inner">
              <ul>
                <li v-for="(poi, index) in sortedWaypoints" :key="poi.spot_id">
                  <button @click="focusOnSpot(poi)">
                    <span class="order-index">{{ index + 1 }}</span>
                    <span class="poi-label">
                      <span v-if="isFacilitySpotId(poi.spot_id)" class="facility-chip" aria-hidden="true">🏢</span>
                      {{ poiListLabel(poi) }}
                    </span>
                    <span class="rt-badges" v-if="isRouteReady && latestBySpot(poi.spot_id)">
                      <span
                        v-if="!isFacilitySpotId(poi.spot_id)"
                        class="rt-badge weather"
                        :title="weatherTitle(latestBySpot(poi.spot_id))"
                      >{{ weatherEmoji(latestBySpot(poi.spot_id)?.w) }}</span>
                      <span
                        v-else
                        class="rt-badge facility"
                        title="施設"
                        aria-label="施設"
                      >🏢</span>
                      <span
                        class="rt-badge crowd"
                        :class="crowdBadge(latestBySpot(poi.spot_id)).className"
                        :title="crowdBadge(latestBySpot(poi.spot_id)).tooltip"
                      >
                        {{ crowdBadge(latestBySpot(poi.spot_id)).label }}
                      </span>
                    </span>
                  </button>
                </li>
              </ul>
              <div v-if="isNavigationReady && sortedAlongPois.length > 0" class="nearby-section">
                <h3 class="nearby-title">Nearby Picks</h3>
                <ul>
                  <li v-for="poi in sortedAlongPois" :key="poi.spot_id">
                    <button @click="focusOnSpot(poi)" class="nearby-button">
                      <span v-if="isFacilitySpotId(poi.spot_id)" class="facility-chip" aria-hidden="true">🏢</span>
                      {{ poiListLabel(poi) }}
                    </button>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="toast-stack">
        <div v-for="t in toasts" :key="t.id" class="toast" role="status" aria-live="polite">
          <strong>{{ t.title }}</strong>
          <div class="toast-body">{{ t.body }}</div>
        </div>
      </div>
    </div>

    <div v-else class="error-view">
      <p v-if="navError">エラーが発生しました: {{ navError }}</p>
      <p v-else>ナビゲーションプランが見つかりません。</p>
      <router-link to="/plan">プラン作成画面に戻る</router-link>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useNavStore } from '@/stores/nav'
import { useRouter } from 'vue-router'
import NavMap from '@/components/NavMap.vue'
import { useRtStore } from '@/stores/rt'
import {
  connect,
  join,
  send,
  disconnect,
  getIsJoined
} from '@/lib/loraBridge'

import { enqueueAudio, resetPlaybackState, useAudioPlaybackState, primeAudioPlayback } from '@/lib/audioManager.js'
import * as geo from '@/lib/geoutils.js'
import { advanceArrivalState, playbackVariants } from '@/lib/offlinePack'
import { tilesForRoute } from '@/lib/tiles'
import { sendSwMessage } from '@/lib/swClient'
import { fetchPoiCatalog } from '@/lib/poi'
import { usePosition } from '@/lib/usePosition.mock.js'
// import { usePosition } from '@/lib/usePosition.js';

const navStore = useNavStore()
const rtStore = useRtStore()
const router = useRouter()

const {
  plan,
  isRouteReady,
  isNavigating,
  isNavigationReady,
  error: navError,
  packJob,
  packMetadata,
  packStartedAt,
  packImport,
  installedPack,
  isTourMode,
  isPackImporting,
} = storeToRefs(navStore)

const navMap = ref(null)
const audioPlaybackState = useAudioPlaybackState()
const textOnlyCaption = ref(null)
const playbackState = computed(() => audioPlaybackState.value || textOnlyCaption.value)
const isSpotListVisible = ref(true)
const online = ref(navigator.onLine)
const isLoraConnecting = ref(false)
const isLoraConnected = ref(false)
let loraSendInterval = null
const facilityIds = ref(new Set())
const FOLLOW_MODE_ZOOM = 15
const {
  currentPos,
  debugLat,
  debugLng,
  setDebugPos,
  isMock,
  // New journey controls
  isJourneyInProgress,
  startJourney,
  stopJourney,
  resetJourney,
  setMockTrack,
} = usePosition()
const isDebug = computed(() => !!isMock)
const isDebugPanelVisible = ref(false)
const progressClock = ref(Date.now())
let progressClockId = null
let textCaptionTimer = null
const pendingTextCaptions = []
const arrivalArmed = new Map()
const arrivalCycles = new Map()
let nextPassByIndex = 0

const packProgress = computed(() => packJob.value?.progress || { done: 0, total: 0, failed: 0 })
const packFailures = computed(() => packMetadata.value?.missing || packJob.value?.failures || [])
const packProgressPercent = computed(() => {
  const total = Number(packProgress.value.total) || 0
  if (total <= 0) return 0
  return Math.min(100, Math.round((Number(packProgress.value.done) || 0) / total * 100))
})
const packStateLabel = computed(() => ({
  queued: '待機中',
  running: '生成中',
  ready: '準備完了',
  partial: '一部失敗',
  failed: '失敗',
}[packJob.value?.state] || '未開始'))
const packElapsedText = computed(() => {
  if (!packStartedAt.value) return '0:00'
  const seconds = Math.max(0, Math.floor((progressClock.value - packStartedAt.value) / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
})
const packImportStateLabel = computed(() => ({
  idle: '未取り込み',
  route: '経路を保存中',
  audio: '音声を保存中',
  tiles: 'タイルを保存中',
  verify: '自己検証中',
  ready: '取り込み済み',
  partial: '一部不足',
  failed: '取り込み失敗',
}[packImport.value.state] || packImport.value.state))
const canEnterTourMode = computed(() => (
  !!installedPack.value && !!packImport.value.verification?.routeCached
))
const realtimeStatusText = computed(() => {
  if (rtStore.stale) return 'パックが古い（要再取得）'
  if (!rtStore.lastReceivedAt) return 'リアルタイム情報なし'
  return `${new Date(rtStore.lastReceivedAt).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' })} 時点`
})

async function loadFacilityCatalog() {
  try {
    const catalog = fetchPoiCatalog({ includeFacilities: true })
    facilityIds.value = new Set(
      catalog
        .filter((item) => item.kind === 'facility')
        .map((item) => item.spot_id)
    )
  } catch (e) {
    console.error('[nav-view] failed to load facility catalog', e)
    facilityIds.value = new Set()
  }
}

function isFacilitySpotId(spotId) {
  if (!spotId) return false
  if (facilityIds.value && typeof facilityIds.value.has === 'function' && facilityIds.value.has(spotId)) {
    return true
  }
  const alongList = plan.value?.along_pois
  if (Array.isArray(alongList)) {
    return alongList.some((poi) => poi.spot_id === spotId && poi.kind === 'facility')
  }
  return false
}

const showDebugPanel = () => {
  isDebugPanelVisible.value = true
}

const hideDebugPanel = () => {
  isDebugPanelVisible.value = false
}
const isPollingEnabled = ref(false)
const didPrecacheTiles = ref(false)
const cachedPlanKey = ref(null)
const precacheInFlight = ref(false)
const TILE_PRECACHE_PROFILES = [
  { label: 'follow-full', zooms: [FOLLOW_MODE_ZOOM], tileBuffer: 1, maxTiles: 700, batchSize: 90 },
  { label: 'follow-thin', zooms: [FOLLOW_MODE_ZOOM], tileBuffer: 0, maxTiles: 520, batchSize: 80 },
  { label: 'fallback-zoom', zooms: [Math.max(FOLLOW_MODE_ZOOM - 1, 1)], tileBuffer: 0, maxTiles: 360, batchSize: 70 },
]
const tileProfileIndex = ref(0)

const primeOnFirstPointer = () => {
  primeAudioPlayback().catch(() => {})
}

function startTextCaptionTimer() {
  if (textCaptionTimer != null || !textOnlyCaption.value || audioPlaybackState.value) return
  textCaptionTimer = window.setTimeout(() => {
    textCaptionTimer = null
    textOnlyCaption.value = null
    showNextTextCaption()
  }, 8000)
}

function showNextTextCaption() {
  if (audioPlaybackState.value || textOnlyCaption.value || pendingTextCaptions.length === 0) return
  textOnlyCaption.value = pendingTextCaptions.shift()
  startTextCaptionTimer()
}

function enqueueTextCaption(id, name, text) {
  pendingTextCaptions.push({
    id,
    name,
    text: text || 'この案内の音声・字幕はパックに含まれていません。',
    isLoading: false,
    error: null,
  })
  showNextTextCaption()
}

function clearTextCaptions() {
  if (textCaptionTimer != null) {
    clearTimeout(textCaptionTimer)
    textCaptionTimer = null
  }
  pendingTextCaptions.length = 0
  textOnlyCaption.value = null
}

watch(audioPlaybackState, (state) => {
  if (state) {
    if (textCaptionTimer != null) {
      clearTimeout(textCaptionTimer)
      textCaptionTimer = null
    }
    return
  }
  if (textOnlyCaption.value) startTextCaptionTimer()
  else showNextTextCaption()
})

function resetArrivalTracking() {
  arrivalArmed.clear()
  arrivalCycles.clear()
  nextPassByIndex = 0
}

const planAssetsList = computed(() => {
  const assets = plan.value?.assets
  if (!assets) return []
  return Array.isArray(assets) ? assets : Object.values(assets)
})

const prefetchedUrls = new Set()
const pendingPrefetchUrls = new Set()
let assetPrefetchPromise = null

function resetAssetPrefetchState() {
  prefetchedUrls.clear()
  pendingPrefetchUrls.clear()
  assetPrefetchPromise = null
}

function collectAssetUrls(assets) {
  if (!Array.isArray(assets) || assets.length === 0) return []
  const urls = []
  for (const asset of assets) {
    if (!asset) continue
    const audioUrl = asset?.audio?.url || asset?.audio_url
    if (audioUrl && !prefetchedUrls.has(audioUrl) && !pendingPrefetchUrls.has(audioUrl)) {
      urls.push(audioUrl)
    }
    const textUrl = asset?.text_url
    if (textUrl && !prefetchedUrls.has(textUrl) && !pendingPrefetchUrls.has(textUrl)) {
      urls.push(textUrl)
    }
  }
  return urls
}

function queueAssetPrefetch(assets) {
  const urls = collectAssetUrls(assets)
  if (!urls.length) return assetPrefetchPromise ?? Promise.resolve()

  urls.forEach((url) => pendingPrefetchUrls.add(url))

  const promise = (async () => {
    const tasks = urls.map(async (url) => {
      try {
        const res = await fetch(url, { cache: 'no-cache' })
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`)
        }
        prefetchedUrls.add(url)
      } catch (err) {
        console.warn('[audio] Prefetch failed', url, err)
      } finally {
        pendingPrefetchUrls.delete(url)
      }
    })

    await Promise.allSettled(tasks)
  })()

  assetPrefetchPromise = promise

  promise.finally(() => {
    if (assetPrefetchPromise === promise) {
      assetPrefetchPromise = null
    }
  })

  return promise
}

const activeTileProfile = () => TILE_PRECACHE_PROFILES[Math.min(tileProfileIndex.value, TILE_PRECACHE_PROFILES.length - 1)]
const isFollowMode = ref(false)
let followIntervalId = null

const recenterOnCurrent = (options = {}) => {
  if (!navMap.value || !currentPos.value) return
  navMap.value.flyToSpot(currentPos.value.lat, currentPos.value.lng, options.zoom ?? null, options.flyOptions)
}

const startFollowTimer = () => {
  if (followIntervalId) {
    clearInterval(followIntervalId)
    followIntervalId = null
  }
  followIntervalId = window.setInterval(() => {
    recenterOnCurrent({ zoom: FOLLOW_MODE_ZOOM, flyOptions: { duration: 0.75 } })
  }, 2000)
}

const stopFollowTimer = () => {
  if (followIntervalId) {
    clearInterval(followIntervalId)
    followIntervalId = null
  }
}

const enableFollowMode = () => {
  if (!currentPos.value) return
  if (isFollowMode.value) return
  isFollowMode.value = true
  recenterOnCurrent({ zoom: FOLLOW_MODE_ZOOM, flyOptions: { duration: 0.75 } })
  startFollowTimer()
}

const disableFollowMode = () => {
  if (!isFollowMode.value) return
  isFollowMode.value = false
  stopFollowTimer()
}

const handleUserPan = () => {
  disableFollowMode()
}

// --- ★★★ 新しいアクションを呼び出すメソッド ★★★ ---
const startGuidance = async () => {
  primeAudioPlayback().catch(() => {})
  resetAssetPrefetchState()
  resetPlaybackState()
  await navStore.startGuidance()
}
// --- ★★★ ここまで ★★★ ---

const handleSwMessage = (event) => {
  if (isTourMode.value) return
  const data = event.data
  if (data?.type === 'PRECACHE_TILES_RESULT' && data.summary) {
    const { added, skipped, failed, quotaExceeded } = data.summary
    console.debug('[sw] precache tiles result', data.summary)
    if (quotaExceeded) {
      didPrecacheTiles.value = false
      if (tileProfileIndex.value < TILE_PRECACHE_PROFILES.length - 1) {
        tileProfileIndex.value += 1
        console.warn('[tiles] quota exceeded, switching profile', {
          profile: TILE_PRECACHE_PROFILES[tileProfileIndex.value]?.label,
        })
        if (plan.value?.polyline?.length) {
          window.setTimeout(() => {
            requestTilePrecache(plan.value.polyline, { force: true })
          }, 250)
        }
      } else {
        console.error('[tiles] cache quota exceeded at minimal profile')
      }
      return
    }
    if (failed > 0) {
      console.warn('[tiles] precache completed with failures', data.summary)
    } else if (added > 0) {
      console.info('[tiles] precache success', data.summary)
    }
  }
}

function buildTrackFromRoute(route) {
  if (!route || route.type !== 'FeatureCollection' || !Array.isArray(route.features)) {
    return null
  }
  const track = []
  for (const feature of route.features) {
    const coords = feature?.geometry?.type === 'LineString' ? feature.geometry.coordinates : null
    if (!Array.isArray(coords) || coords.length === 0) continue
    for (let i = 0; i < coords.length; i += 1) {
      const coord = coords[i]
      if (!Array.isArray(coord) || coord.length < 2) continue
      const [lng, lat] = coord
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) continue
      const last = track[track.length - 1]
      if (last && last[0] === lng && last[1] === lat) {
        continue
      }
      track.push([lng, lat])
    }
  }
  return track.length > 1 ? track : null
}

// GeoJSONのLineStringをモック現在地トラックに流し込む
watch(
  () => plan.value?.route,
  (route) => {
    if (!isMock || typeof setMockTrack !== 'function') return
    const track = buildTrackFromRoute(route)
    if (track) setMockTrack(track)
  },
  { deep: true, immediate: true }
)

onMounted(async () => {
  progressClockId = window.setInterval(() => { progressClock.value = Date.now() }, 1000)
  window.addEventListener('pointerdown', primeOnFirstPointer, { once: true })

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', handleSwMessage)
  }

  if (!isRouteReady.value || (isTourMode.value && !isNavigationReady.value)) {
    await navStore.restoreOfflinePack()
  }
  if (isTourMode.value && !isNavigationReady.value) navStore.exitTourMode()
  if (!isTourMode.value) {
    navStore.resumePackPolling()
  }

  if (!isRouteReady.value) {
    if (isTourMode.value) navStore.exitTourMode()
    router.push('/plan')
    return
  }

  await loadFacilityCatalog()
})

onUnmounted(() => {
  if (progressClockId != null) {
    clearInterval(progressClockId)
    progressClockId = null
  }
  window.removeEventListener('pointerdown', primeOnFirstPointer)
  rtStore.stopPolling()
  stopLoraPolling()
  resetPlaybackState()
  clearTextCaptions()
  resetArrivalTracking()
  resetAssetPrefetchState()
  if (isLoraConnected.value) {
    disconnectLoraDevice()
  }
  disableFollowMode()
  window.removeEventListener('online', _updateOnline)
  window.removeEventListener('offline', _updateOnline)
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.removeEventListener('message', handleSwMessage)
  }
})

const clearTileCache = async () => {
  const ok = await sendSwMessage({ type: 'RESET_TILES_CACHE' })
  if (ok) {
    didPrecacheTiles.value = false
    cachedPlanKey.value = null
    tileProfileIndex.value = 0
  }
}

watch(
  [() => plan.value?.cacheKey, isTourMode],
  async ([cacheKey, tourMode], [prevKey] = []) => {
    if (tourMode) return
    if (plan.value?.manifest) {
      cachedPlanKey.value = cacheKey
      return
    }
    if (!cacheKey || !plan.value?.polyline?.length) {
      if (prevKey) await clearTileCache()
      cachedPlanKey.value = null
      didPrecacheTiles.value = false
      return
    }

    if (cachedPlanKey.value && cacheKey !== cachedPlanKey.value) {
      didPrecacheTiles.value = false
      await clearTileCache()
    }

    if (!didPrecacheTiles.value && !precacheInFlight.value) {
      precacheInFlight.value = true
      try {
        await requestTilePrecache(plan.value.polyline)
      } finally {
        precacheInFlight.value = false
      }
    }

    cachedPlanKey.value = cacheKey
  },
  { immediate: true }
)

watch(
  () => isNavigating.value,
  async (navigating) => {
    if (isTourMode.value || !navigating || !plan.value?.polyline?.length) return
    await requestTilePrecache(plan.value.polyline, { force: true })
  }
)

async function requestTilePrecache(polyline, { force = false } = {}) {
  if (isTourMode.value) return
  if (!('serviceWorker' in navigator)) return
  if (!force && didPrecacheTiles.value) return
  const profile = activeTileProfile()
  const candidateTiles = tilesForRoute(polyline, profile)
  if (!candidateTiles.length) return

  const metaBase = {
    requestedAt: Date.now(),
    planCreatedAt: plan.value?.createdAt ?? null,
    profile: profile.label,
    totalTiles: candidateTiles.length,
    batchSize: profile.batchSize,
    batches: Math.ceil(candidateTiles.length / profile.batchSize),
  }

  let postedAny = false
  for (let i = 0; i < candidateTiles.length; i += profile.batchSize) {
    const batch = candidateTiles.slice(i, i + profile.batchSize)
    const payload = {
      type: 'PRECACHE_TILES',
      tiles: batch,
      meta: {
        ...metaBase,
        batchIndex: Math.floor(i / profile.batchSize),
        batchRequested: batch.length,
      }
    }
    const posted = await sendSwMessage(payload)
    postedAny = postedAny || posted
  }

  if (postedAny) {
    didPrecacheTiles.value = true
  }
}


// マップ上の現在位置マーカーを更新
watch(currentPos, (newPos) => {
  if (!newPos) {
    disableFollowMode()
    return
  }
  if (navMap.value) {
    navMap.value.updateCurrentPosition(newPos.lat, newPos.lng)
    if (isFollowMode.value) {
      recenterOnCurrent({ zoom: FOLLOW_MODE_ZOOM, flyOptions: { duration: 0.35 } })
    }
  }
})

watch(
  [isNavigationReady, () => planAssetsList.value],
  ([ready, assets]) => {
    if (
      !ready
      || isTourMode.value
      || installedPack.value
      || isPackImporting.value
      || ['ready', 'partial'].includes(packJob.value?.state)
    ) return
    queueAssetPrefetch(assets)
  },
  { immediate: true }
)

watch(
  () => plan.value?.pack_id ?? null,
  (packId) => {
    if (!packId) {
      resetAssetPrefetchState()
    }
  },
  { immediate: true }
)

watch(
  [() => plan.value?.pack_id ?? null, isTourMode],
  () => {
    resetArrivalTracking()
    clearTextCaptions()
  },
)

watch(isTourMode, (active) => {
  if (active) rtStore.stopPolling()
}, { immediate: true })

watch(
  () => plan.value?.waypoints_info,
  (waypoints) => {
    if (!Array.isArray(waypoints) || waypoints.length === 0) {
      rtStore.setSpotOrder([])
      if (isPollingEnabled.value) {
        stopAllRtPolling()
      }
      return
    }
    rtStore.setSpotOrder(waypoints)
    if (isPollingEnabled.value) {
      startRtPollingIfNeeded()
    }
  },
  { immediate: true }
)

watch(isRouteReady, (ready) => {
  if (!ready) {
    stopAllRtPolling()
    isPollingEnabled.value = false
    return
  }
  startRtPollingIfNeeded()
})

function enqueueAssetAudio(id, displayName, asset, { fallbackText = null } = {}) {
  if (!asset) return false
  const voiceUrl = asset?.audio?.url || asset?.audio_url
  if (!voiceUrl) return false

  const payload = {
    id,
    name: displayName,
    voice_path: voiceUrl,
    text: asset?.text_url ? null : (asset?.text || fallbackText || null),
    textUrl: asset?.text_url ?? null,
  }

  console.debug('[AudioQueue] Enqueueing asset', {
    triggerId: id,
    asset,
    fallbackText,
    finalPayload: payload,
  });

  enqueueAudio(payload)

  return true
}

function enqueueArrivalGuidance(spot, arrivalKey) {
  const manifest = plan.value?.manifest
  if (!manifest) return
  const cycle = (arrivalCycles.get(arrivalKey) || 0) + 1
  arrivalCycles.set(arrivalKey, cycle)
  const spotName = spot.name_ja || spot.name || spot.spot_id
  const variants = playbackVariants(manifest, rtStore.getLatest(spot.spot_id))

  for (const variant of variants) {
    const asset = spot.assets?.[variant]
    const label = variant === 'base'
      ? spotName
      : `${spotName} · ${variant}`
    const id = `${arrivalKey}:${cycle}:${variant}`
    if (asset?.file) {
      enqueueAssetAudio(id, label, {
        audio_url: new URL(asset.file, plan.value.manifest_url).toString(),
        text: asset.text || null,
      })
    } else {
      enqueueTextCaption(id, label, asset?.text || `${spotName}の${variant}案内音声がありません。`)
    }
  }
}

function checkArrival(spot, key, newPos) {
  const lat = Number(spot?.lat)
  const lon = Number(spot?.lon)
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false
  const distance = geo.calculateDistance(newPos, { lat, lng: lon })
  const currentArmed = arrivalArmed.has(key) ? arrivalArmed.get(key) : true
  const next = advanceArrivalState(currentArmed, distance, spot.trigger_radius_m)
  arrivalArmed.set(key, next.armed)
  if (next.triggered) enqueueArrivalGuidance(spot, key)
  return next.triggered
}

// 観光モードの到達判定。visit は全件、pass_by は route_position 順の次の 1 件だけを見る。
watch(currentPos, (newPos) => {
  if (!isTourMode.value || !isNavigationReady.value || !newPos) return
  const manifest = plan.value?.manifest
  if (!manifest) return

  for (const spot of manifest.spots || []) {
    checkArrival(spot, `visit:${spot.spot_id}`, newPos)
  }

  const passBy = [...(manifest.along || [])]
    .sort((a, b) => Number(a.route_position) - Number(b.route_position))
  const nextSpot = passBy[nextPassByIndex]
  if (nextSpot && checkArrival(nextSpot, `pass_by:${nextPassByIndex}:${nextSpot.spot_id}`, newPos)) {
    nextPassByIndex += 1
  }
})

// LoRa受信データをトリガーに状況別案内をキューに追加するロジック
watch(
  () => rtStore.notifyLog.length,
  (newLength, oldLength) => {
    // ★★★ isNavigationReadyをチェックする条件を追加 ★★★
    const lastEvent = newLength > 0 ? rtStore.notifyLog[newLength - 1] : null
    console.debug('[nav-view] notifyLog watcher', {
      newLength,
      oldLength,
      isNavigationReady: isNavigationReady.value,
      hasPlan: !!plan.value,
      event: lastEvent,
    })

    if (isTourMode.value || newLength <= oldLength || !isNavigationReady.value || !plan.value) return;

    const event = rtStore.notifyLog[newLength - 1];
    const spotId = event.spot_id;
    const prevWeather = Number(event.prev?.w)
    const weatherCode = Number(event.next?.w)
    const weatherChanged = !Number.isFinite(prevWeather)
      ? Number.isFinite(weatherCode)
      : prevWeather !== weatherCode
    const prevCongestion = Number(event.prev?.c)
    const congestionLevel = Number(event.next?.c)
    const congestionChanged = !Number.isFinite(prevCongestion)
      ? Number.isFinite(congestionLevel)
      : prevCongestion !== congestionLevel
    const rules = plan.value?.playback_rules || {}
    const changedVariants = {
      weather: weatherChanged && !isFacilitySpotId(spotId)
        ? rules.weather?.[String(weatherCode)]
        : null,
      congestion: congestionChanged
        ? rules.congestion?.[String(congestionLevel)]
        : null,
    }
    for (const layer of rules.order || []) {
      const situationType = changedVariants[layer]
      if (situationType) queueSituationAnnouncement(spotId, situationType)
    }
  }
);


// --- 以下、既存のロジック (UI、LoRa接続、ライフサイクルなど) ---
// (※ ユーザー提供のコードから変更なし)
const sortedWaypoints = computed(() => {
  if (plan.value && plan.value.waypoints_info) {
    return [...plan.value.waypoints_info].sort(
      (a, b) => (a.nearest_idx || 0) - (b.nearest_idx || 0)
    )
  }
  return []
})

const sortedAlongPois = computed(() => {
  if (plan.value && plan.value.along_pois) {
    return [...plan.value.along_pois].sort(
      (a, b) => (a.order_index ?? a.nearest_idx ?? 0) - (b.order_index ?? b.nearest_idx ?? 0)
    )
  }
  return []
})

function poiKindLabel(poi) {
  if (!poi || !poi.kind) return null
  return poi.kind === 'facility' ? '施設' : (poi.kind === 'spot' ? 'スポット' : null)
}

function poiListLabel(poi) {
  const base = poi?.name || poi?.spot_id || '(unknown)'
  const category = poi?.category ? `（${poi.category}）` : ''
  return `${base}${category}`
}

const spotNameMap = computed(() => {
  const m = new Map()
  if (plan.value?.waypoints_info) {
    for (const wp of plan.value.waypoints_info) {
      if (wp.spot_id) m.set(wp.spot_id, wp.name || wp.spot_id)
    }
  }
  if (plan.value?.along_pois) {
    for (const p of plan.value.along_pois) {
      if (p.spot_id && !m.has(p.spot_id)) m.set(p.spot_id, p.name || p.spot_id)
    }
  }
  return m
})

const toasts = ref([])
function pushToast(title, body, timeoutMs = 4000) {
  const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  toasts.value.push({ id, title, body })
  setTimeout(() => {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }, timeoutMs)
}

function startLoraPolling() {
  stopLoraPolling()
  const spots = sortedWaypoints.value
  if (spots.length === 0 || !isLoraConnected.value) return
  let currentIndex = 0
  const loraTask = async () => {
    if (!getIsJoined()) {
      console.warn('[LoRa Polling] Not joined. Re-joining...')
      isLoraConnected.value = false
      isLoraConnecting.value = true
      try {
        await join()
        isLoraConnected.value = true
      } catch (e) {
        pushToast('LoRa Error', 'Failed to re-join.', 6000)
        await disconnectLoraDevice()
        return
      } finally {
        isLoraConnecting.value = false
      }
    }
    const spotId = spots[currentIndex].spot_id
    await send(spotId)
    currentIndex = (currentIndex + 1) % spots.length
  }
  loraTask()
  loraSendInterval = setInterval(loraTask, 60000)
}

function stopLoraPolling() {
  if (loraSendInterval) {
    clearInterval(loraSendInterval)
    loraSendInterval = null
  }
}

async function connectLoraDevice() {
  primeAudioPlayback().catch(() => {})
  isLoraConnecting.value = true
  try {
    await connect(
      (receivedData) => rtStore.applyDownlink(receivedData, plan.value?.manifest),
      () => {
        pushToast('LoRa', 'Device disconnected.', 5000)
        disconnectLoraDevice()
      }
    )
    await join()
    isLoraConnected.value = true
    pushToast('LoRa', 'Connected to device and joined network.')
    await new Promise((resolve) => setTimeout(resolve, 3000))
    if (isPollingEnabled.value) {
      rtStore.stopPolling()
      startLoraPolling()
    }
  } catch (error) {
    alert(`LoRa connection error: ${error.message}`)
    await disconnect()
    isLoraConnected.value = false
  } finally {
    isLoraConnecting.value = false
  }
}

async function disconnectLoraDevice() {
  stopLoraPolling()
  await disconnect()
  isLoraConnected.value = false
  if (!isTourMode.value && online.value && isPollingEnabled.value) {
    rtStore.startPolling(plan.value?.waypoints_info || [])
  } else {
    rtStore.stopPolling()
  }
}

function _updateOnline() { online.value = navigator.onLine }
window.addEventListener('online', _updateOnline)
window.addEventListener('offline', _updateOnline)

watch(online, (isOnline) => {
  if (isTourMode.value) {
    rtStore.stopPolling()
    return
  }
  if (isOnline && isNavigationReady.value) {
    queueAssetPrefetch(planAssetsList.value)
  }
  if (!isPollingEnabled.value) {
    rtStore.stopPolling()
    return
  }
  if (isOnline && !isLoraConnected.value) {
    rtStore.startPolling(plan.value?.waypoints_info || [])
  } else if (!isOnline) {
    rtStore.stopPolling()
  }
})

function startRtPollingIfNeeded() {
  if (!isPollingEnabled.value) return;

  if (isTourMode.value) {
    rtStore.stopPolling()
    if (isLoraConnected.value) startLoraPolling()
    else stopLoraPolling()
    return
  }

  // ナビ開始前はHTTPポーリングのみ
  if (!isNavigationReady.value) {
    if (online.value) {
      stopLoraPolling();
      rtStore.startPolling(plan.value?.waypoints_info || []);
    }
    return;
  }

  // ナビ開始後はLoRaを優先
  if (isLoraConnected.value) {
    rtStore.stopPolling();
    startLoraPolling();
  } else if (online.value) {
    stopLoraPolling();
    rtStore.startPolling(plan.value?.waypoints_info || []);
  } else {
    pushToast('リアルタイム', 'オフラインのためHTTP取得不可。LoRa接続すると取得できます。', 5000);
  }
}

function stopAllRtPolling() {
  stopLoraPolling()
  rtStore.stopPolling()
}

function togglePolling() {
  primeAudioPlayback().catch(() => {})
  isPollingEnabled.value = !isPollingEnabled.value
  if (isPollingEnabled.value) startRtPollingIfNeeded()
  else stopAllRtPolling()
}

async function verifyOfflineAssets() {
  const verification = await navStore.verifyInstalledPack()
  if (!verification) {
    pushToast('オフライン資材', '取り込み済みのパックがありません。', 5000)
    return
  }
  const body = verification.missing.length
    ? `音声が ${verification.missing.length} 本足りません。利用できる案内はそのまま使えます。`
    : '音声と経路の自己検証が完了しました。'
  pushToast('オフライン資材', body, 5000)
}

async function beginTourMode() {
  stopAllRtPolling()
  const entered = await navStore.enterTourMode()
  if (!entered) {
    pushToast('観光モード', '先に案内パックを端末へ取り込んでください。', 5000)
    startRtPollingIfNeeded()
    return
  }
  resetArrivalTracking()
  pushToast('観光モード', '通信を使わない観光モードを開始しました。', 5000)
  if (isPollingEnabled.value && isLoraConnected.value) startLoraPolling()
}

function leaveTourMode() {
  navStore.exitTourMode()
  resetArrivalTracking()
  if (isPollingEnabled.value) startRtPollingIfNeeded()
  pushToast('観光モード', '計画モードへ戻りました。', 4000)
}


function toggleSpotList() { isSpotListVisible.value = !isSpotListVisible.value }
function focusOnSpot(poi) {
  disableFollowMode()
  if (navMap.value) {
    navMap.value.flyToSpot(poi.lat, poi.lon)
  }
}
function weatherEmoji(w) { return { 0: '☀', 1: '☁', 2: '☂' }[w] || '▫' }
function weatherTitle(doc) { if (!doc) return ''; const m = { 0: '晴れ', 1: '曇り', 2: '雨' }; return `現在: ${m[doc.w] ?? '-'}` }

const CROWD_STATES = [
  {
    level: 0,
    label: 'Clear Flow',
    tooltip: '全く混んでいません',
    toast: '空いています',
    className: 'is-low'
  },
  {
    level: 1,
    label: 'Moderate Crowd',
    tooltip: 'やや混雑しています',
    toast: 'やや混雑しています',
    className: 'is-mid'
  },
  {
    level: 2,
    label: 'Heavy Crowd',
    tooltip: 'かなり混雑しています',
    toast: '混雑しています',
    className: 'is-high'
  }
]
const UNKNOWN_CROWD_STATE = {
  label: 'Unknown',
  tooltip: '混雑情報なし',
  toast: '混雑情報なし',
  className: 'is-unknown',
}

const SITUATION_META = {
  weather_cloudy: {
    title: 'Weather · Cloudy',
    fallback: (spotName) => `${spotName}は現在、雲が広がっています。空模様の変化にご注意ください。`
  },
  weather_rain: {
    title: 'Weather · Rain',
    fallback: (spotName) => `${spotName}では雨が降っています。足元が滑りやすいのでお気をつけください。`
  },
  congestion_mid: {
    title: 'Crowd · Moderate',
    fallback: (spotName) => `${spotName}は現在やや混雑しています。移動には少し時間に余裕を持ってください。`
  },
  congestion_high: {
    title: 'Crowd · Heavy',
    fallback: (spotName) => `${spotName}は現在かなり混雑しています。ルートの変更もご検討ください。`
  },
}

function normalizeCrowd(docOrLevel) {
  const raw = (docOrLevel && typeof docOrLevel === 'object') ? docOrLevel.c : docOrLevel
  const value = Number(raw)
  if (!Number.isFinite(value) || value === 0x0f) return null
  return Math.max(0, Math.min(2, value))
}

function crowdBadge(doc) {
  const level = normalizeCrowd(doc)
  return level == null ? UNKNOWN_CROWD_STATE : CROWD_STATES[level]
}

function queueSituationAnnouncement(spotId, situationType) {
  const meta = SITUATION_META[situationType]
  if (!meta) return

  const assets = planAssetsList.value
  if (!assets.length) {
    console.warn('[audio] No plan assets available for situation announcements.')
    return
  }

  const asset = assets.find((a) => a.spot_id === spotId && a.situation === situationType)
  if (!asset) {
    console.warn(`[audio] Missing asset for spot ${spotId} (${situationType}).`)
    return
  }

  const spotName = spotNameMap.value.get(spotId) || spotId
  const displayName = `${spotName} · ${meta.title}`
  const fallbackText = typeof meta.fallback === 'function' ? meta.fallback(spotName) : meta.fallback ?? null

  console.debug('[nav-view] queueSituationAnnouncement', {
    spotId,
    situationType,
    hasAsset: !!asset,
    asset,
  })

  enqueueAssetAudio(`${spotId}_${situationType}`, displayName, asset, { fallbackText })
}

function latestBySpot(spotId) { return rtStore.getLatest?.(spotId) ?? null }

</script>

<style scoped>
/* 基本レイアウト */
.nav-view {
  position: relative;
  width: 100%;
  height: 100vh;
  overflow: hidden;
}
.debug-panel {
  position: fixed;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  z-index: 960;
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: clamp(260px, 36vw, 360px);
  max-width: calc(100% - 48px);
  max-height: calc(100vh - 160px);
  padding: 16px;
  border-radius: 8px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.15);
  overflow: hidden;
}
.debug-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.debug-panel__header h4 {
  margin: 0;
  font-size: 0.86rem;
  font-weight: 600;
  color: #0f172a;
}
.debug-panel__close {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: none;
  background: transparent;
  color: #475569;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
}
.debug-panel__row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.debug-panel__input {
  flex: 1 1 120px;
  min-width: 120px;
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid #cbd5e1;
  font-size: 0.85rem;
  color: #0f172a;
}
.debug-panel__input:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
}
.debug-panel__action {
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid #3b82f6;
  background: #3b82f6;
  color: #ffffff;
  font-size: 0.8rem;
  font-weight: 600;
  cursor: pointer;
}
.debug-panel__follow-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78rem;
  color: #1e293b;
}
.debug-panel__status {
  margin: 0;
  font-size: 0.78rem;
  color: #334155;
}
.debug-toggle {
  position: fixed;
  bottom: 20px;
  left: 20px;
  z-index: 960;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: none;
  background: rgba(15, 23, 42, 0.92);
  color: #f8fafc;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.45);
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.debug-toggle:hover {
  opacity: 0.85;
  transform: translateY(-1px);
}
.debug-toggle:focus-visible {
  outline: 2px solid rgba(148, 163, 184, 0.7);
  outline-offset: 3px;
}
.debug-toggle__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 -8px 0 currentColor, 0 8px 0 currentColor;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
@media (max-width: 640px) {
  .debug-panel {
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: calc(100% - 32px);
    max-height: calc(100vh - 120px);
  }
  .debug-toggle {
    left: 16px;
    bottom: 16px;
  }
}
.nav-container {
  position: relative;
  width: 100%;
  height: 100%;
}
.map-wrapper {
  position: relative;
  width: 100%;
  height: 100%;
}

/* 音声ガイドキャプション */
.audio-caption {
  position: absolute;
  left: 50%;
  bottom: 28px;
  transform: translateX(-50%);
  width: min(640px, calc(100% - 48px));
  padding: 20px 24px;
  border-radius: 18px;
  background: linear-gradient(135deg, rgba(15, 23, 42, 0.95), rgba(30, 41, 59, 0.9));
  border: 1px solid rgba(148, 163, 184, 0.28);
  color: #f8fafc;
  box-shadow: 0 26px 48px rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(14px);
  pointer-events: none;
  z-index: 930;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.audio-caption::before {
  content: '';
  position: absolute;
  inset: -6px;
  border-radius: 22px;
  background: radial-gradient(circle at 30% 20%, rgba(59, 130, 246, 0.28), transparent 60%),
    radial-gradient(circle at 80% 0%, rgba(217, 70, 239, 0.22), transparent 55%);
  filter: blur(18px);
  opacity: 0.85;
  z-index: -2;
}
.audio-caption::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 18px;
  background: linear-gradient(135deg, rgba(148, 163, 184, 0.2), rgba(37, 99, 235, 0.12));
  mix-blend-mode: screen;
  opacity: 0.35;
  pointer-events: none;
  z-index: -1;
}
.audio-caption.is-loading {
  background: linear-gradient(135deg, rgba(8, 47, 73, 0.92), rgba(15, 118, 110, 0.88));
  border-color: rgba(45, 212, 191, 0.4);
}
.audio-caption.has-error {
  background: linear-gradient(135deg, rgba(127, 29, 29, 0.92), rgba(185, 28, 28, 0.88));
  border-color: rgba(248, 113, 113, 0.55);
}
.audio-caption__header {
  display: flex;
  align-items: center;
  gap: 16px;
}
.audio-caption__badge {
  width: 44px;
  height: 44px;
  border-radius: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, rgba(37, 99, 235, 0.95), rgba(59, 130, 246, 0.95));
  color: #fff;
  box-shadow: 0 14px 32px rgba(59, 130, 246, 0.35);
  transition: background 0.3s ease, box-shadow 0.3s ease, color 0.3s ease;
}
.audio-caption__badge--loading {
  background: linear-gradient(135deg, rgba(6, 182, 212, 0.95), rgba(45, 212, 191, 0.95));
  box-shadow: 0 14px 32px rgba(45, 212, 191, 0.35);
}
.audio-caption__badge--error {
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.95), rgba(220, 38, 38, 0.95));
  box-shadow: 0 14px 32px rgba(248, 113, 113, 0.38);
}
.audio-caption__meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.audio-caption__label {
  font-size: 0.75rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: rgba(148, 197, 255, 0.8);
}
.audio-caption.has-error .audio-caption__label {
  color: rgba(255, 205, 205, 0.85);
}
.audio-caption.is-loading .audio-caption__label {
  color: rgba(165, 243, 252, 0.85);
}
.audio-caption__title {
  font-size: 1.12rem;
  font-weight: 600;
  line-height: 1.3;
  color: inherit;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.audio-caption__alert {
  margin-left: auto;
  width: 38px;
  height: 38px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(248, 113, 113, 0.16);
  color: rgba(254, 202, 202, 0.9);
  box-shadow: inset 0 0 0 1px rgba(248, 113, 113, 0.4);
}
.audio-caption__body {
  font-size: 0.98rem;
  line-height: 1.7;
  color: #e2e8f0;
  white-space: pre-wrap;
}
.audio-caption__body--error {
  color: #fee2e2;
}
.audio-caption__wave {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 14px;
  color: rgba(148, 163, 184, 0.75);
  opacity: 0.4;
  transition: opacity 0.3s ease, color 0.3s ease;
}
.audio-caption__wave.is-active {
  color: rgba(96, 165, 250, 0.9);
  opacity: 0.9;
}
.audio-caption__wave span {
  display: block;
  width: 6px;
  height: 8px;
  border-radius: 999px;
  background: currentColor;
  transform-origin: center bottom;
  animation: captionWave 1.2s ease-in-out infinite;
  opacity: 0.7;
}
.audio-caption__wave span:nth-child(2) { animation-delay: 0.15s; }
.audio-caption__wave span:nth-child(3) { animation-delay: 0.3s; }
.audio-caption__wave span:nth-child(4) { animation-delay: 0.45s; }

@keyframes captionWave {
  0%, 100% {
    transform: scaleY(0.35);
    opacity: 0.5;
  }
  50% {
    transform: scaleY(1.1);
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .audio-caption__wave span {
    animation: none;
    transform: scaleY(1);
  }
}

@media (max-width: 768px) {
  .audio-caption {
    width: calc(100% - 32px);
    padding: 18px 20px;
    bottom: 22px;
  }
  .audio-caption__header {
    gap: 12px;
  }
  .audio-caption__badge {
    width: 40px;
    height: 40px;
  }
  .audio-caption__title {
    font-size: 1.05rem;
  }
}

/* 地図操作ボタン (現在地追従) - 右下 */
.map-actions {
  position: absolute;
  top: 55%;
  right: 40px;
  transform: translateY(-50%);
  z-index: 900;
}

@media (max-width: 640px) {
  .map-actions {
    top: 70%;
    right: 24px;
  }
  .map-action-btn {
    width: 72px;
    padding: 20px 12px 16px;
  }
  .map-action-btn__label {
    font-size: 0.62rem;
  }
}
.map-action-btn {
  position: relative;
  width: 86px;
  padding: 24px 14px 18px;
  border-radius: 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border: none;
  background: linear-gradient(135deg, rgba(37, 99, 235, 0.95), rgba(59, 130, 246, 0.88));
  color: #0f172a;
  cursor: pointer;
  box-shadow: 0 22px 38px rgba(37, 99, 235, 0.35);
  transition: all 0.2s ease-in-out;
  overflow: hidden;
}
.map-action-btn svg {
  position: relative;
  z-index: 1;
  color: currentColor;
}
.map-action-btn__halo {
  position: absolute;
  inset: -40%;
  background: radial-gradient(circle at 50% 40%, rgba(255, 255, 255, 0.45), transparent 65%);
  opacity: 0;
  transition: opacity 0.25s ease;
}
.map-action-btn:hover {
  transform: translateY(-3px);
  box-shadow: 0 28px 46px rgba(59, 130, 246, 0.45);
}
.map-action-btn:hover .map-action-btn__halo {
  opacity: 0.6;
}
.map-action-btn.is-following {
  background: linear-gradient(135deg, rgba(14, 165, 233, 0.95), rgba(16, 185, 129, 0.9));
  color: #0f172a;
  box-shadow: 0 26px 44px rgba(16, 185, 129, 0.38);
}
.map-action-btn.is-following .map-action-btn__label {
  color: #0f172a;
}
.map-action-btn:disabled {
  cursor: not-allowed;
  opacity: 0.65;
  box-shadow: none;
  transform: none;
}
.icon-location { transition: transform 0.4s cubic-bezier(0.68, -0.55, 0.27, 1.55); }
.icon-location-dot { transform: scale(0); transition: transform 0.3s ease-in-out; transform-origin: center; }
.map-action-btn.is-following .icon-location { transform: rotate(135deg); }
.map-action-btn.is-following .icon-location-dot { transform: scale(1); }
.map-action-btn__label {
  margin-top: 10px;
  font-size: 0.7rem;
  letter-spacing: 0.24em;
  text-transform: uppercase;
  color: #e2e8f0;
}

/* 左上UIコンテナ */
.top-left-ui-area {
  position: absolute;
  top: 16px;
  left: 16px;
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: flex-start;
}
.controls {
  display: grid;
  gap: 8px;
  width: min(320px, calc(100vw - 32px));
}
.tour-mode-banner,
.offline-pack-panel {
  border-radius: 12px;
  border: 1px solid rgba(45, 212, 191, 0.4);
  background: rgba(15, 23, 42, 0.9);
  color: #e2e8f0;
  box-shadow: 0 12px 24px rgba(15, 23, 42, 0.32);
  backdrop-filter: blur(10px);
}
.tour-mode-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 12px;
}
.tour-mode-banner div {
  display: grid;
  gap: 2px;
}
.tour-mode-banner strong { color: #5eead4; }
.tour-mode-banner span { font-size: 0.72rem; color: #cbd5e1; }
.tour-mode-banner button,
.offline-pack-panel button {
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 8px;
  background: rgba(30, 41, 59, 0.8);
  color: #e2e8f0;
  padding: 6px 9px;
  cursor: pointer;
}
.offline-pack-panel {
  display: grid;
  gap: 8px;
  padding: 10px 12px;
  font-size: 0.76rem;
}
.offline-pack-panel p { margin: 0; }
.offline-pack-panel__header,
.offline-pack-panel__progress,
.offline-pack-panel__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.offline-pack-panel__warning { color: #fcd34d; }
.offline-pack-panel .offline-pack-panel__primary {
  border-color: rgba(45, 212, 191, 0.55);
  background: rgba(13, 148, 136, 0.85);
  color: white;
}
.offline-pack-panel button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

/* 横並びコントロールバー */
.controls .control-buttons {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.75);
  box-shadow: 0 12px 24px rgba(15, 23, 42, 0.3);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(148, 163, 184, 0.25);
  max-width: min(320px, 100%);
}
.control-btn {
  position: relative;
  border: none;
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(30, 64, 175, 0.9), rgba(59, 130, 246, 0.85));
  color: #e2e8f0;
  cursor: pointer;
  padding: 8px 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78rem;
  letter-spacing: 0.02em;
  transition: transform 0.2s ease, box-shadow 0.2s ease, background 0.3s ease;
  box-shadow: 0 8px 18px rgba(37, 99, 235, 0.26);
  flex: 1 1 110px;
}
.control-btn svg { flex-shrink: 0; }
.control-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 20px rgba(59, 130, 246, 0.34);
}
.control-btn.is-active {
  background: linear-gradient(135deg, rgba(22, 163, 74, 0.92), rgba(34, 197, 94, 0.92));
  box-shadow: 0 10px 22px rgba(34, 197, 94, 0.32);
  color: #f0fdf4;
}
.control-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  box-shadow: none;
}

.data-sync-btn {
  min-width: 120px;
  justify-content: center;
  background: linear-gradient(135deg, rgba(29, 78, 216, 0.88), rgba(79, 70, 229, 0.88));
  flex: 1 1 140px;
}
.control-btn.is-active.data-sync-btn {
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.95), rgba(168, 85, 247, 0.95));
}
.data-sync-btn__label {
  font-weight: 600;
  text-transform: uppercase;
  font-size: 0.6rem;
  letter-spacing: 0.1em;
}

.lora-panel {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(17, 94, 89, 0.9), rgba(15, 23, 42, 0.92));
  border: 1px solid rgba(45, 212, 191, 0.3);
  color: #ccfbf1;
  min-width: 150px;
  box-shadow: 0 10px 20px rgba(14, 116, 144, 0.28);
  transition: border-color 0.3s ease, box-shadow 0.3s ease;
  flex: 1 1 160px;
}
.lora-panel.is-connecting {
  border-color: rgba(56, 189, 248, 0.5);
  box-shadow: 0 10px 20px rgba(56, 189, 248, 0.24);
}
.lora-panel.is-connected {
  border-color: rgba(34, 197, 94, 0.55);
  box-shadow: 0 10px 20px rgba(34, 197, 94, 0.26);
}
.lora-panel__icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 118, 110, 0.3);
  color: inherit;
  box-shadow: inset 0 0 0 1px rgba(45, 212, 191, 0.28);
}
.lora-panel__body {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
  gap: 2px;
}
.lora-panel__label {
  font-size: 0.6rem;
  text-transform: uppercase;
  letter-spacing: 0.16em;
  opacity: 0.7;
}
.lora-panel__status {
  font-size: 0.74rem;
  font-weight: 600;
}
.lora-toggle-btn {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: none;
  border-radius: 999px;
  padding: 5px 10px;
  background: rgba(15, 23, 42, 0.55);
  color: #f8fafc;
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: background 0.2s ease, transform 0.2s ease;
}
.lora-toggle-btn:hover {
  transform: translateY(-2px);
  background: rgba(15, 23, 42, 0.7);
}
.lora-toggle-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
}
.lora-toggle-btn__dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.5);
  position: relative;
}
.lora-toggle-btn__dot.is-active {
  background: rgba(34, 197, 94, 0.95);
  box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.2);
}
.lora-toggle-btn__dot.is-busy {
  background: rgba(56, 189, 248, 0.95);
  box-shadow: 0 0 0 4px rgba(56, 189, 248, 0.18);
  animation: loraBlink 1.2s ease-in-out infinite;
}
.lora-toggle-btn__text {
  text-transform: uppercase;
  letter-spacing: 0.18em;
}

@keyframes loraBlink {
  0%, 100% { opacity: 0.6; }
  50% { opacity: 1; }
}

@media (max-width: 540px) {
  .controls .control-buttons {
    width: calc(100vw - 48px);
  }
  .control-btn,
  .data-sync-btn,
  .lora-panel {
    flex: 1 1 100%;
  }
  .data-sync-btn,
  .lora-panel {
    min-width: auto;
  }
}

.spot-list-panel {
  width: 210px;
  border-radius: 12px;
  overflow: hidden;
  background: rgba(15, 23, 42, 0.8);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(148, 163, 184, 0.25);
  box-shadow: 0 14px 24px rgba(15, 23, 42, 0.32);
}
.spot-list-toggle {
  width: 100%;
  padding: 10px 12px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: rgba(15, 23, 42, 0.55);
  border: none;
  cursor: pointer;
  color: #e2e8f0;
  transition: background 0.2s ease;
}
.spot-list-toggle:hover {
  background: rgba(30, 41, 59, 0.65);
}
.spot-list-toggle__left {
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
}
.spot-list-toggle__eyebrow {
  font-size: 0.56rem;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  opacity: 0.6;
}
.spot-list-toggle__title {
  font-size: 0.86rem;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.chevron-icon {
  transition: transform 0.3s ease;
}
.chevron-icon.is-open {
  transform: rotate(180deg);
}

.spot-list-content {
  max-height: 0;
  overflow: hidden;
  transition: max-height 0.35s ease;
}
.spot-list-content.is-open {
  max-height: 60vh;
  overflow-y: auto;
}
.spot-list-content-inner {
  padding: 10px 12px 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.spot-list-content-inner ul {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.spot-list-content-inner li button {
  width: 100%;
  background: rgba(15, 23, 42, 0.55);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 10px;
  padding: 8px 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #f8fafc;
  font-size: 0.8rem;
  cursor: pointer;
  transition: transform 0.2s ease, border-color 0.2s ease, background 0.2s ease;
}
.spot-list-content-inner li button:hover {
  transform: translateY(-2px);
  border-color: rgba(94, 234, 212, 0.6);
  background: rgba(15, 118, 110, 0.55);
}
.order-index {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  border-radius: 5px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  background: linear-gradient(135deg, rgba(56, 189, 248, 0.9), rgba(59, 130, 246, 0.9));
  color: #0f172a;
  box-shadow: 0 6px 12px rgba(56, 189, 248, 0.26);
}
.rt-badges {
  margin-left: auto;
  display: inline-flex;
  gap: 3px;
}
.rt-badges .rt-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  min-height: 18px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.25);
  padding: 4px 8px;
  font-size: 0.75rem;
}
.rt-badge.crowd.is-low {
  background: rgba(34, 197, 94, 0.18);
  color: #4ade80;
  box-shadow: 0 0 0 1px rgba(34, 197, 94, 0.3);
}
.rt-badge.crowd.is-mid {
  background: rgba(234, 179, 8, 0.18);
  color: #facc15;
  box-shadow: 0 0 0 1px rgba(234, 179, 8, 0.28);
}
.rt-badge.crowd.is-high {
  background: rgba(248, 113, 113, 0.2);
  color: #f87171;
  box-shadow: 0 0 0 1px rgba(248, 113, 113, 0.32);
}
.rt-badge.crowd.is-unknown {
  background: rgba(148, 163, 184, 0.2);
  color: #cbd5e1;
}
.nearby-section {
  border-top: 1px solid rgba(148, 163, 184, 0.2);
  padding-top: 12px;
}
.nearby-title {
  font-size: 0.78rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: rgba(148, 197, 255, 0.8);
  margin-bottom: 8px;
}
.nearby-button {
  width: 100%;
  border: 1px dashed rgba(148, 163, 184, 0.25);
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.4);
  padding: 8px 10px;
  color: #e2e8f0;
  font-size: 0.82rem;
  cursor: pointer;
  transition: border-color 0.2s ease, transform 0.2s ease;
}
.nearby-button:hover {
  transform: translateY(-2px);
  border-color: rgba(249, 115, 22, 0.65);
}

.start-nav-panel {
  width: min(290px, calc(100vw - 32px));
  padding: 12px 14px 14px;
  border-radius: 14px;
  background: linear-gradient(135deg, rgba(15, 23, 42, 0.85), rgba(30, 64, 175, 0.92));
  box-shadow: 0 16px 30px rgba(15, 23, 42, 0.42);
  border: 1px solid rgba(99, 102, 241, 0.34);
  color: #e2e8f0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.start-nav-button {
  position: relative;
  overflow: hidden;
  width: 100%;
  border: none;
  border-radius: 12px;
  padding: 12px 14px;
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.95), rgba(14, 165, 233, 0.9));
  color: #0f172a;
  font-weight: 700;
  font-size: 0.9rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  box-shadow: 0 12px 24px rgba(56, 189, 248, 0.34);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.start-nav-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 14px 28px rgba(56, 189, 248, 0.4);
}
.start-nav-button:disabled {
  cursor: not-allowed;
  opacity: 0.7;
  transform: none;
  box-shadow: none;
}
.start-nav-button__spark {
  position: absolute;
  inset: -40%;
  background: radial-gradient(circle at 50% 50%, rgba(255, 255, 255, 0.55), transparent 65%);
  opacity: 0;
  transition: opacity 0.3s ease;
}
.start-nav-button:hover .start-nav-button__spark {
  opacity: 0.6;
}
.start-nav-button__inner {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.start-nav-button__icon {
  filter: drop-shadow(0 4px 8px rgba(14, 165, 233, 0.3));
}
.start-nav-button__label {
  font-size: 0.72rem;
  letter-spacing: 0.16em;
}
.start-nav-button__progress {
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: 8px;
  height: 3px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.25);
  overflow: hidden;
  opacity: 0;
  transition: opacity 0.2s ease;
}
.start-nav-button__progress span {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 40%;
  min-width: 60px;
  background: linear-gradient(135deg, rgba(56, 189, 248, 0.9), rgba(14, 165, 233, 0.9));
  border-radius: inherit;
  transform: translateX(-100%);
  animation: startProgress 1.8s ease-in-out infinite;
}
.start-nav-button__progress span:nth-child(2) { animation-delay: 0.22s; }
.start-nav-button__progress span:nth-child(3) { animation-delay: 0.44s; }
.start-nav-button__progress span:nth-child(4) { animation-delay: 0.66s; }

.start-nav-button.is-loading .start-nav-button__progress {
  opacity: 1;
}
.start-nav-button.is-loading {
  background: linear-gradient(135deg, rgba(14, 165, 233, 0.92), rgba(125, 211, 252, 0.95));
  color: #0c4a6e;
}

@keyframes startProgress {
  0% { transform: translateX(-100%); }
  50% { transform: translateX(30%); }
  100% { transform: translateX(120%); }
}

.start-nav-status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(148, 163, 184, 0.3);
  font-size: 0.82rem;
  color: #e0f2fe;
}
.start-nav-status__pulse {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: rgba(56, 189, 248, 0.95);
  box-shadow: 0 0 0 0 rgba(56, 189, 248, 0.4);
  animation: statusPulse 1.6s ease-out infinite;
}
.start-nav-status__text {
  letter-spacing: 0.06em;
}

.pack-progress {
  display: grid;
  gap: 9px;
  padding: 10px;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.68);
  border: 1px solid rgba(148, 163, 184, 0.3);
  color: #e2e8f0;
  font-size: 0.78rem;
}
.pack-progress__header,
.pack-progress__counts {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.pack-progress__elapsed {
  color: #bae6fd;
  font-variant-numeric: tabular-nums;
}
.pack-state-badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(56, 189, 248, 0.18);
  color: #7dd3fc;
  font-weight: 700;
}
.pack-state-badge.is-partial {
  background: rgba(245, 158, 11, 0.2);
  color: #fcd34d;
}
.pack-state-badge.is-failed {
  background: rgba(248, 113, 113, 0.2);
  color: #fca5a5;
}
.pack-state-badge.is-ready {
  background: rgba(52, 211, 153, 0.2);
  color: #6ee7b7;
}
.pack-progress__bar {
  height: 5px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.24);
}
.pack-progress__bar span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #38bdf8, #818cf8);
  transition: width 0.25s ease;
}
.pack-progress__message {
  margin: 0;
  line-height: 1.45;
}
.pack-progress__message.is-ready { color: #6ee7b7; }
.pack-progress__message.is-partial { color: #fcd34d; }
.pack-progress__message.is-failed { color: #fca5a5; }
.pack-progress__missing summary {
  cursor: pointer;
  color: #bae6fd;
}
.pack-progress__missing ul {
  max-height: 110px;
  margin: 7px 0 0;
  padding-left: 18px;
  overflow-y: auto;
  color: #cbd5e1;
}

@keyframes statusPulse {
  0% {
    transform: scale(0.9);
    box-shadow: 0 0 0 0 rgba(56, 189, 248, 0.5);
  }
  70% {
    transform: scale(1.05);
    box-shadow: 0 0 0 10px rgba(56, 189, 248, 0);
  }
  100% {
    transform: scale(0.9);
    box-shadow: 0 0 0 0 rgba(56, 189, 248, 0);
  }
}

/* その他 */
.error-box { background: #fef2f2; color: #b91c1c; padding: 8px; border-radius: 4px; margin-top: 8px; font-size: 0.9rem; }
.toast-stack { position: absolute; right: 12px; z-index: 1100; display: flex; flex-direction: column; gap: 8px; bottom: 90px; }
.toast { background: rgba(15, 23, 42, 0.9); color: #f8fafc; padding: 12px 16px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); width: 280px; }
.toast-body { font-size: 0.9rem; margin-top: 4px; opacity: 0.9; }
.error-view { padding: 20px; text-align: center; }
</style>
