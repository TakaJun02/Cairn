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
      <!-- 観光モード中のバナー。地図には重ねず、地図の上の帯として表示する
           (地図に重なる常設要素は現在地追従ボタン 1 つだけ、という条件を
           満たすため)。 -->
      <div v-if="isTourMode" class="tour-mode-bar" role="status">
        <strong>観光モード</strong>
        <span class="tour-mode-bar__status">{{ realtimeStatusText }}</span>
        <button type="button" class="tour-mode-bar__exit" @click="leaveTourMode">観光モードを終了</button>
      </div>

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
            class="follow-btn"
            :class="{ 'is-following': isFollowMode }"
            :disabled="!currentPos"
            @click="isFollowMode ? disableFollowMode() : enableFollowMode()"
            :title="isFollowMode ? '追従を停止' : '現在地に追従'"
          >
            <svg class="icon-location" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 19c-3.87 0-7-3.13-7-7s3.13-7 7-7 7 3.13 7 7-3.13 7-7 7z"/>
              <path d="M12 8v8M8 12h8"/>
              <circle class="icon-location-dot" cx="12" cy="12" r="2.5" fill="currentColor" stroke="none" />
            </svg>
            <span class="sr-only">現在地に追従</span>
          </button>
        </div>

        <!-- L-1 是正: legacy ルート `/nav` は NavWindow(⋯ トリガーの持ち主)
             でラップされないため、単体表示のときだけここに同じトリガーを
             出す。呼び出す先は既存の isControlsMenuOpen のまま。 -->
        <button
          v-if="isStandaloneNavView"
          type="button"
          class="relative flex h-11 w-11 items-center justify-center rounded-ui-sm border border-edge-strong bg-ink-raised/80 text-text-dim shadow-soft backdrop-blur transition-colors duration-fast hover:bg-fill-hover hover:text-text"
          style="position: absolute; top: 12px; right: 12px; z-index: 950"
          :aria-expanded="isControlsMenuOpen"
          aria-label="メニュー"
          @click.stop="isControlsMenuOpen = !isControlsMenuOpen"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="19" cy="12" r="1.7" /></svg>
          <span v-if="isMapStatusActive" class="menu-trigger-dot" aria-hidden="true"></span>
        </button>

        <!-- ⋯ メニュー(NavWindow.vue のヘッダーから開閉)。「端末に取り込む」
             「オフライン資材」「ライブ同期」「LoRa リンク」を 1 枚に畳む。
             機能・発火条件は現行のまま。変えたのは配置と見せ方だけ。 -->
        <div
          v-if="isControlsMenuOpen"
          class="controls-menu-scrim"
          @click="isControlsMenuOpen = false"
        ></div>
        <div v-if="isControlsMenuOpen" class="controls-menu" role="group" aria-label="地図の設定">
          <div v-if="!isNavigationReady || packJob" class="menu-section">
            <button
              v-if="!packJob || ['partial', 'failed'].includes(packJob.state)"
              type="button"
              class="menu-row"
              :disabled="isNavigating"
              @click="startGuidance"
            >
              <span class="menu-row__label">
                <template v-if="isNavigating">パックを生成中…</template>
                <template v-else-if="packJob?.state === 'partial'">不足分を再生成</template>
                <template v-else-if="packJob?.state === 'failed'">もう一度生成</template>
                <template v-else>端末に取り込む</template>
              </span>
              <!-- L-4 是正: 固定文字列だと partial/failed の状態でも
                   「未取得」のまま実態と食い違う。packStateLabel(既存の
                   computed。§8.3)を使う。 -->
              <span class="menu-row__status">{{ packStateLabel }}</span>
            </button>
            <div v-if="packJob" class="menu-detail" role="status" aria-live="polite">
              <div class="menu-detail__row">
                <span class="menu-detail__badge" :class="`is-${packJob.state}`">{{ packStateLabel }}</span>
                <span class="menu-detail__muted">{{ packElapsedText }}</span>
              </div>
              <div class="menu-detail__row menu-detail__muted">
                <strong class="menu-detail__count">{{ packProgress.done }} / {{ packProgress.total }}</strong>
                <span>失敗 {{ packProgress.failed }} 件</span>
              </div>
              <div class="menu-detail__bar" aria-hidden="true">
                <span :style="{ width: `${packProgressPercent}%` }"></span>
              </div>
              <p v-if="packJob.state === 'ready'" class="menu-detail__message">
                端末への取り込み準備ができました
              </p>
              <p v-else-if="packJob.state === 'partial'" class="menu-detail__message is-warn">
                一部の案内を作れませんでした（{{ packFailures.length }} 件）
              </p>
              <p v-else-if="packJob.state === 'failed'" class="menu-detail__message is-warn">
                案内パックを作成できませんでした
              </p>
              <details v-if="packFailures.length" class="menu-detail__missing">
                <summary>作れなかった案内の内訳</summary>
                <ul>
                  <li v-for="(failure, index) in packFailures" :key="`${failure.spot_id}-${failure.variant}-${index}`">
                    {{ packFailureLabel(failure) }}
                    <template v-if="failure.variant"> / {{ failure.variant }}</template>
                    — {{ failure.reason }}
                  </li>
                </ul>
              </details>
            </div>
            <p v-if="navError" class="menu-detail__message is-warn">エラー: {{ navError }}</p>
          </div>

          <div v-if="isNavigationReady" class="menu-section" role="status" aria-live="polite">
            <div class="menu-row menu-row--static">
              <span class="menu-row__label">オフライン資材</span>
              <span class="menu-row__status">{{ packImportStateLabel }}</span>
            </div>
            <div class="menu-detail">
              <div class="menu-detail__row menu-detail__muted">
                <span>音声 {{ packImport.audio.done }} / {{ packImport.audio.total }}</span>
                <span>タイル {{ packImport.tiles.done }} / {{ packImport.tiles.total }}</span>
              </div>
              <p v-if="packImport.verification" class="menu-detail__muted">
                自己検証: {{ packImport.verification.cached }} / {{ packImport.verification.expected }} 本
                <strong v-if="packImport.verification.missing.length" class="menu-detail__warn-text">
                  （{{ packImport.verification.missing.length }} 本足りません）
                </strong>
                <span v-else>（完了）</span>
              </p>
              <p v-if="packImport.tiles.quotaExceeded" class="menu-detail__message is-warn">
                保存容量の上限に達したため、表示件数の地点でタイル取得を停止しました。
              </p>
              <p v-if="packImport.verification && !packImport.verification.routeCached" class="menu-detail__message is-warn">
                経路データが足りません。観光モードは開始できません。
              </p>
              <p v-if="packImport.error" class="menu-detail__message is-warn">{{ packImport.error }}</p>
              <div class="menu-detail__actions">
                <button type="button" class="menu-detail__btn" :disabled="isPackImporting" @click="verifyOfflineAssets">
                  取り込みを再検証
                </button>
                <button
                  v-if="!isTourMode"
                  type="button"
                  class="menu-detail__btn menu-detail__btn--primary"
                  :disabled="!canEnterTourMode || isPackImporting"
                  @click="beginTourMode"
                >
                  観光モードを開始
                </button>
              </div>
            </div>
          </div>

          <button
            v-if="isRouteReady"
            type="button"
            class="menu-row"
            :class="{ 'is-active': isPollingEnabled }"
            @click="togglePolling"
          >
            <span class="menu-row__label">ライブ同期</span>
            <span class="menu-row__status">
              <b v-if="isPollingEnabled" class="menu-row__dot" aria-hidden="true"></b>
              {{ isPollingEnabled ? '接続中' : '停止中' }}
            </span>
          </button>

          <div v-if="isNavigationReady" class="menu-section">
            <div class="menu-row menu-row--static" :class="{ 'is-active': isLoraConnected }">
              <span class="menu-row__label">LoRa リンク</span>
              <span class="menu-row__status">
                <b v-if="isLoraConnected" class="menu-row__dot" aria-hidden="true"></b>
                {{ isLoraConnected ? '接続済み' : (isLoraConnecting ? '接続中…' : '待機中') }}
              </span>
            </div>
            <div class="menu-detail">
              <div class="menu-detail__actions">
                <button
                  type="button"
                  class="menu-detail__btn"
                  :disabled="isLoraConnecting"
                  @click="isLoraConnected ? disconnectLoraDevice() : connectLoraDevice()"
                >
                  {{ isLoraConnected ? '切断する' : '接続する' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 旅程ストリップ(frontend_design_system.md §8.3.1、2026-08-06 改訂):
           Spots List カードを廃止し、ここに統合する。訪問順に丸番号 +
           スポット名のチップを横スクロールで並べる。「訪問順」は行内の
           小さな前置きラベルにして 1 段に畳む(§8.3.1-6)。面は地図の
           明るさに合わせる(§8.3.1-1)。 -->
      <div class="itinerary-strip" aria-label="訪問順">
        <div v-if="sortedWaypoints.length" class="itinerary-strip__row">
          <span class="itinerary-strip__prefix">訪問順</span>
          <button
            v-for="(poi, index) in sortedWaypoints"
            :key="poi.spot_id"
            type="button"
            class="stop-chip"
            @click="focusOnSpot(poi)"
          >
            <span class="stop-chip__num">{{ index + 1 }}</span>
            <span v-if="isFacilitySpotId(poi.spot_id)" aria-hidden="true">🏢</span>
            {{ poiListLabel(poi) }}
            <span
              v-if="isRouteReady && latestBySpot(poi.spot_id)"
              class="stop-chip__rt"
              :title="isFacilitySpotId(poi.spot_id) ? '施設' : weatherTitle(latestBySpot(poi.spot_id))"
            >
              {{ isFacilitySpotId(poi.spot_id) ? '🏢' : weatherEmoji(latestBySpot(poi.spot_id)?.w) }}
              <span class="rt-badge crowd" :class="crowdBadge(latestBySpot(poi.spot_id)).className">{{ crowdBadge(latestBySpot(poi.spot_id)).label }}</span>
            </span>
          </button>
        </div>
        <p v-else class="itinerary-strip__empty">まだ旅程がありません</p>

        <div v-if="isNavigationReady && sortedAlongPois.length > 0" class="itinerary-strip__row itinerary-strip__row--secondary">
          <span class="itinerary-strip__prefix">近くのおすすめ</span>
          <button
            v-for="poi in sortedAlongPois"
            :key="poi.spot_id"
            type="button"
            class="stop-chip stop-chip--nearby"
            @click="focusOnSpot(poi)"
          >
            <span v-if="isFacilitySpotId(poi.spot_id)" aria-hidden="true">🏢</span>
            {{ poiListLabel(poi) }}
          </button>
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
import { ref, computed, inject, onMounted, onUnmounted, watch } from 'vue'
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

// 「⋯」メニューの開閉状態。トリガーは NavWindow.vue のヘッダーにあるため、
// provide/inject で共有する(frontend_design_system.md §8.3/§8.4)。
//
// L-1 是正: legacy ルート `/nav` は NavView.vue を NavWindow でラップせずに
// 直接マウントするため、provide が届かず、メニューを開くトリガー(⋯ ボタン)
// がヘッダーにもそもそも存在しない。provide の有無を判定し、無ければこの
// コンポーネント自身がトリガーを出す(呼び出す関数・状態は同じ
// isControlsMenuOpen のまま。挙動は変えていない)。
const NAV_CONTROLS_MENU_NOT_PROVIDED = Symbol('nav-controls-menu-not-provided')
const injectedControlsMenuOpen = inject('navControlsMenuOpen', NAV_CONTROLS_MENU_NOT_PROVIDED)
const isStandaloneNavView = injectedControlsMenuOpen === NAV_CONTROLS_MENU_NOT_PROVIDED
const isControlsMenuOpen = isStandaloneNavView ? ref(false) : injectedControlsMenuOpen

// frontend_design_system.md §8.3.1-7: ⋯ ボタンの状態ドット。中身(ライブ
// 同期・パック取得・LoRa)は本コンポーネントが知っているが、ボタン自体は
// NavWindow.vue のヘッダーにあるため、isControlsMenuOpen と同じ「親が ref を
// 作って渡し、子が書き込む」型で共有する(provide/inject は親→子方向にしか
// 流れないが、同一の ref オブジェクトを介せば子から親へも値を伝えられる)。
const NAV_MAP_STATUS_NOT_PROVIDED = Symbol('nav-map-status-not-provided')
const injectedMapStatusActive = inject('navMapStatusActive', NAV_MAP_STATUS_NOT_PROVIDED)
const mapStatusActiveRef = injectedMapStatusActive === NAV_MAP_STATUS_NOT_PROVIDED ? ref(false) : injectedMapStatusActive

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
// F9([25 §1-7] レビュー是正): spot_id 自体が無いときは「パック全体」の
// まま(変更しない)。spot_id はあるが nav ストアの spots で名前解決
// できないときは spot_id へフォールバックせず「不明な地点」にする
// (frontend_nav.md §2.4)。
function packFailureLabel(failure) {
  const spotId = failure?.spot_id
  if (!spotId) return 'パック全体'
  const spot = navStore.spots.find((value) => value.spot_id === spotId)
  return spot?.name_ja || spot?.name || '不明な地点'
}
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

// frontend_design_system.md §8.3.1-7: 「ライブ同期が接続 / パック取得中 /
// LoRa 接続」のいずれかが真のとき、⋯ ボタンにドットを出す。中身は開かないと
// 読めなくてよいが、「何か動いている」ことは閉じていても分かるようにする。
const isMapStatusActive = computed(() => (
  isPollingEnabled.value || isNavigating.value || isPackImporting.value || isLoraConnected.value
))
watch(isMapStatusActive, (value) => { mapStatusActiveRef.value = value }, { immediate: true })

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
  // F9([25 §1-7] レビュー是正): 名前解決に失敗しても spot_id へは
  // フォールバックしない(frontend_nav.md §2.4)。
  const spotName = spot.name_ja || spot.name || '不明な地点'
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
  // F9([25 §1-7] レビュー是正): 名前解決に失敗しても spot_id へは
  // フォールバックしない(frontend_nav.md §2.4)。
  // P7([frontend_design_system.md §7.7]): `poi.category` は `tourist_spot`
  // のような内部 enum であり、画面に出さない。名前だけを返す。
  return poi?.name || '不明な地点'
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
        pushToast('LoRa', '再接続に失敗しました。', 6000)
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
        pushToast('LoRa', 'デバイスが切断されました。', 5000)
        disconnectLoraDevice()
      }
    )
    await join()
    isLoraConnected.value = true
    pushToast('LoRa', 'デバイスに接続し、ネットワークに参加しました。')
    await new Promise((resolve) => setTimeout(resolve, 3000))
    if (isPollingEnabled.value) {
      rtStore.stopPolling()
      startLoraPolling()
    }
  } catch (error) {
    alert(`LoRa 接続エラー: ${error.message}`)
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

// P7/§6-2([frontend_design_system.md](../../Docs/30_design/frontend_design_system.md)):
// `label` は画面に常時出る短いラベルなので日本語にする(`tooltip` はもともと
// 日本語)。
const CROWD_STATES = [
  {
    level: 0,
    label: '空いてる',
    tooltip: '全く混んでいません',
    toast: '空いています',
    className: 'is-low'
  },
  {
    level: 1,
    label: 'やや混雑',
    tooltip: 'やや混雑しています',
    toast: 'やや混雑しています',
    className: 'is-mid'
  },
  {
    level: 2,
    label: '混雑',
    tooltip: 'かなり混雑しています',
    toast: '混雑しています',
    className: 'is-high'
  }
]
const UNKNOWN_CROWD_STATE = {
  label: '不明',
  tooltip: '混雑情報なし',
  toast: '混雑情報なし',
  className: 'is-unknown',
}

const SITUATION_META = {
  weather_cloudy: {
    title: '天気 · 曇り',
    fallback: (spotName) => `${spotName}は現在、雲が広がっています。空模様の変化にご注意ください。`
  },
  weather_rain: {
    title: '天気 · 雨',
    fallback: (spotName) => `${spotName}では雨が降っています。足元が滑りやすいのでお気をつけください。`
  },
  congestion_mid: {
    title: '混雑 · やや混雑',
    fallback: (spotName) => `${spotName}は現在やや混雑しています。移動には少し時間に余裕を持ってください。`
  },
  congestion_high: {
    title: '混雑 · かなり混雑',
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
  /* L-9 是正: `.nav-window`(親)の will-change: transform がこの要素の
     containing block になるため、position: fixed は「ウィンドウの右下」
     ではなく実質「ウィンドウ内」に閉じ込められる。bottom 起点だと旅程
     ストリップの 1 番目のチップに重なっていた。地図左上へ移す。 */
  position: absolute;
  top: 12px;
  left: 12px;
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
    left: 12px;
    top: 12px;
  }
}
.nav-container {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
}
.map-wrapper {
  position: relative;
  width: 100%;
  flex: 1 1 auto;
  min-height: 0;
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

/* 地図操作ボタン(現在地追従) - 右下。地図に重なる常設要素はこれ 1 つだけ
   (frontend_design_system.md §8.3)。 */
.map-actions {
  position: absolute;
  right: 14px;
  bottom: 14px;
  z-index: 900;
}
.follow-btn {
  width: 44px;
  height: 44px;
  border-radius: 9999px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--color-edge-strong);
  background: rgba(24, 29, 33, 0.8);
  backdrop-filter: blur(10px);
  color: var(--color-text);
  cursor: pointer;
  box-shadow: var(--shadow-raised);
  transition: background-color var(--motion-base) var(--ease-standard), transform var(--motion-base) var(--ease-expressive);
}
.follow-btn:hover {
  background: rgba(34, 42, 46, 0.9);
  transform: translateY(-1px);
}
.follow-btn.is-following {
  color: var(--color-signal);
  border-color: rgba(47, 201, 176, 0.45);
}
.follow-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
  transform: none;
}
.icon-location { transition: transform 0.4s cubic-bezier(0.68, -0.55, 0.27, 1.55); }
.icon-location-dot { transform: scale(0); transition: transform 0.3s ease-in-out; transform-origin: center; }
.follow-btn.is-following .icon-location { transform: rotate(135deg); }
.follow-btn.is-following .icon-location-dot { transform: scale(1); }

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

/* 観光モードの帯。地図の上部に定位置を持つ(地図には重ねない)。 */
.tour-mode-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  padding: 8px 16px;
  border-bottom: 1px solid var(--color-edge);
  background: var(--color-raised);
  color: var(--color-text);
  font-size: 13px;
}
.tour-mode-bar__status {
  color: var(--color-text-muted);
}
.tour-mode-bar__exit {
  margin-left: auto;
  min-height: 44px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-edge-strong);
  background: transparent;
  color: var(--color-text-muted);
  padding: 0 12px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: background-color var(--motion-fast), color var(--motion-fast);
}
.tour-mode-bar__exit:hover {
  background: var(--color-fill-hover);
  color: var(--color-text);
}

/* ⋯ メニュー(§8.3)。「端末に取り込む」「オフライン資材」「ライブ同期」
   「LoRa リンク」を 1 枚のポップオーバーに畳む。 */
.controls-menu-scrim {
  position: absolute;
  inset: 0;
  z-index: 940;
}
.controls-menu {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 950;
  width: min(300px, calc(100% - 24px));
  max-height: calc(100% - 24px);
  overflow-y: auto;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-edge-strong);
  background: var(--color-raised);
  box-shadow: var(--shadow-overlay);
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.menu-section + .menu-section,
.menu-section + button.menu-row,
button.menu-row + .menu-section {
  margin-top: 4px;
  padding-top: 4px;
  border-top: 1px solid var(--color-edge);
}
.menu-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 44px;
  border-radius: var(--radius-sm);
  border: 0;
  background: transparent;
  color: var(--color-text-muted);
  padding: 8px 11px;
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--motion-fast), color var(--motion-fast);
}
.menu-row:hover:not(:disabled) {
  background: var(--color-fill-hover);
  color: var(--color-text);
}
.menu-row:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
.menu-row--static {
  cursor: default;
}
.menu-row--static:hover {
  background: transparent;
  color: var(--color-text-muted);
}
.menu-row.is-active .menu-row__label {
  color: var(--color-text);
}
.menu-row__label {
  flex: 1;
}
.menu-row__status {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--color-text-dim);
  display: flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}
.menu-row__dot {
  width: 6px;
  height: 6px;
  border-radius: 9999px;
  background: var(--color-signal);
  display: block;
}
.menu-detail {
  padding: 2px 11px 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12px;
  color: var(--color-text-muted);
}
.menu-detail__row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.menu-detail__muted {
  color: var(--color-text-dim);
}
.menu-detail__count {
  color: var(--color-text);
  font-family: var(--font-display);
}
.menu-detail__badge {
  border-radius: 9999px;
  border: 1px solid var(--color-edge-strong);
  background: var(--color-fill-hover);
  padding: 2px 9px;
  font-size: 10.5px;
  color: var(--color-text-muted);
}
.menu-detail__bar {
  height: 4px;
  border-radius: 9999px;
  background: var(--color-fill-hover);
  overflow: hidden;
}
.menu-detail__bar span {
  display: block;
  height: 100%;
  background: var(--aurora-copy);
  transition: width var(--motion-base) var(--ease-standard);
}
.menu-detail__message {
  color: var(--color-text-muted);
}
.menu-detail__message.is-warn,
.menu-detail__warn-text {
  color: var(--color-warn);
}
.menu-detail__missing summary {
  cursor: pointer;
  color: var(--color-text-muted);
}
.menu-detail__missing ul {
  margin-top: 6px;
  padding-left: 16px;
  color: var(--color-text-dim);
}
.menu-detail__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.menu-detail__btn {
  min-height: 44px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-edge-strong);
  background: transparent;
  color: var(--color-text-muted);
  padding: 0 12px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: background-color var(--motion-fast), color var(--motion-fast);
}
.menu-detail__btn:hover:not(:disabled) {
  background: var(--color-fill-hover);
  color: var(--color-text);
}
.menu-detail__btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.menu-detail__btn--primary {
  border-color: rgba(47, 201, 176, 0.45);
  color: var(--color-signal-soft);
}

/* 旅程ストリップ(frontend_design_system.md §8.3.1、2026-08-06 改訂)。
   地図に接する面は「地図の明るさ」に合わせる(P1 の例外。§2/§8.3.1)。
   旧実装(git show HEAD~2:frontend/src/views/NavView.vue)の Spots List
   パネルが使っていた白地・濃紺文字の考え方を、こちらのトークンで引き継ぐ。 */
.itinerary-strip {
  flex-shrink: 0;
  border-top: 1px solid var(--color-edge);
  background: var(--color-paper);
  padding: 10px 14px;
}
.itinerary-strip__row {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow-x: auto;
  padding-bottom: 2px;
}
/* §8.3.1-6: 「訪問順」は行内の小さな前置きラベルに畳む(1 件でも 2 段
   取らないようにする)。 */
.itinerary-strip__prefix {
  flex: 0 0 auto;
  font-size: 10px;
  font-family: var(--font-display);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  color: rgb(var(--color-paper-ink-rgb) / 0.55);
  white-space: nowrap;
}
.itinerary-strip__row--secondary {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgb(var(--color-paper-ink-rgb) / 0.08);
}
.itinerary-strip__empty {
  font-size: 12px;
  color: rgb(var(--color-paper-ink-rgb) / 0.6);
}
/* §8.3.1-3: チップ本体は明るい面に載る形へ(白に近い地 + paper-ink の文字 +
   薄い枠)。hover で枠を signal-deep に。 */
.stop-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
  min-height: 44px;
  border-radius: 9999px;
  border: 1px solid rgb(var(--color-paper-ink-rgb) / 0.14);
  background: #ffffff;
  padding: 5px 14px 5px 6px;
  font-size: 12.5px;
  color: var(--color-paper-ink);
  cursor: pointer;
  white-space: nowrap;
  transition: border-color var(--motion-base), color var(--motion-base), background-color var(--motion-base);
}
.stop-chip:hover {
  border-color: var(--color-signal-deep);
}
/* 明るい地の上では既定の --color-signal-soft の外周線が 1.6:1 前後しか
   出ない(§3.2)。ここだけ signal-deep に差し替える(F1 の「1 つの言語」は
   保つ。色調だけを地の明るさに合わせる)。 */
.stop-chip:focus-visible {
  outline-color: var(--color-signal-deep);
}
.stop-chip--nearby {
  padding-left: 12px;
}
/* §8.3.1-2: 番号バッジは signal-deep で塗り、数字は paper。地図側の
   divIcon(NavMap.vue の `.poi-marker-badge`)と同じ色・同じ数字にする。 */
.stop-chip__num {
  width: 22px;
  height: 22px;
  flex: 0 0 22px;
  border-radius: 9999px;
  background: var(--color-signal-deep);
  display: grid;
  place-items: center;
  font-family: var(--font-display);
  font-size: 11px;
  font-weight: 600;
  color: var(--color-paper);
}
.stop-chip__rt {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: 2px;
}

/* §8.3.1-7: ⋯ ボタンの状態ドット。 */
.menu-trigger-dot {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 7px;
  height: 7px;
  border-radius: 9999px;
  background: var(--color-signal);
  box-shadow: 0 0 0 2px var(--color-raised);
}

.toast-stack { position: absolute; right: 12px; z-index: 1100; display: flex; flex-direction: column; gap: 8px; bottom: 90px; }
.toast { background: rgba(15, 23, 42, 0.9); color: #f8fafc; padding: 12px 16px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); width: 280px; }
.toast-body { font-size: 0.9rem; margin-top: 4px; opacity: 0.9; }
.error-view { padding: 20px; text-align: center; }
</style>
