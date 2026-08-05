<script setup>
import { ref, reactive, onMounted, onBeforeUnmount, computed, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { useNavStore } from '@/stores/nav'
import { usePosition } from '@/lib/usePosition.js'
// import { usePosition } from '@/lib/usePosition.mock.js'
import { sendSwMessage } from '@/lib/swClient'
import { fetchPoiCatalog } from '@/lib/poi'
// import NavView from '@/views/NavView.vue' // 削除

// 新しく追加
import NavWindow from '@/components/NavWindow.vue'

const DEBUG_ORIGIN = Object.freeze({ lat: 39.393477, lon: 140.073769 })

const nav = useNavStore()
const router = useRouter()
const { lang, isRouteLoading, error, plan } = storeToRefs(nav)
const position = usePosition()

const effectiveOrigin = computed(() => DEBUG_ORIGIN)

const isMockPositionMode = computed(() => true)

const pois = ref([])
const spotPois = computed(() => pois.value.filter(p => p.kind !== 'facility'))
const facilityPois = computed(() => pois.value.filter(p => p.kind === 'facility'))
const selectedIds = ref([])

const hasError = computed(() => !!error.value)
const hasNoPosition = computed(() => !effectiveOrigin.value)
const hasRoute = computed(() => !!plan.value?.route)

// 削除: isNavWindowVisible, isNavWindowFullScreen, navWindow, navWindowStyle, previousUserSelect, previousWindowState

// 削除: clamp 関数

// --- POIデータ読み込み ---
onMounted(async () => {
  try {
    const catalog = fetchPoiCatalog({ includeFacilities: true })
    pois.value = catalog.sort((a, b) => baseDisplayName(a).localeCompare(baseDisplayName(b), 'ja'))
  } catch (e) {
    console.error('[plan] failed to load poi catalog', e)
    pois.value = []
  }

  // 削除: ensureNavWindowBounds()
  // 削除: if (typeof window !== 'undefined') { window.addEventListener('resize', handleWindowResize) }
})

onBeforeUnmount(() => {
  // 削除: endPointerInteraction()
  // 削除: if (typeof window !== 'undefined') { window.removeEventListener('resize', handleWindowResize) }
})

// --- UI用ヘルパー関数 ---
function baseDisplayName(poi) {
  const names = poi?.official_name || poi?.names || {}
  // F9([25 §1-7] レビュー是正): 名前解決に失敗しても spot_id へは
  // フォールバックしない。中立表記「不明な地点」を使う
  // (frontend_nav.md §2.4)。
  return names[lang.value] || names.ja || poi?.name || '不明な地点'
}

function displayName(p) {
  return baseDisplayName(p)
}

function nameById(id) {
  const p = pois.value.find((x) => x.spot_id === id)
  return p ? displayName(p) : '不明な地点'
}

// --- 選択・並べ替えロジック ---
function toggleSpot(id) {
  const i = selectedIds.value.indexOf(id)
  if (i >= 0) {
    selectedIds.value.splice(i, 1)
  } else {
    selectedIds.value.push(id)
  }
}

function move(i, d) {
  const j = i + d
  if (j < 0 || j >= selectedIds.value.length) return
  const temp = selectedIds.value[i]
  selectedIds.value[i] = selectedIds.value[j]
  selectedIds.value[j] = temp
}

function remove(i) {
  selectedIds.value.splice(i, 1)
}

/**
 * ルート計画の作成をストアのアクションに依頼する
 */
async function submitPlan() {
  if (hasNoPosition.value) {
    alert('現在地が取得できていません。ブラウザのGPS利用を許可してください。')
    return
  }
  const origin = effectiveOrigin.value
  if (!origin) {
    // ガード: 念のため null チェック（上の hasNoPosition で基本的に弾かれる）
    alert('現在地の解決に失敗しました。')
    return
  }
  const planOptions = {
    language: lang.value,
    origin,
    waypoints: selectedIds.value.map((id) => ({ spot_id: id }))
  }

  await sendSwMessage({ type: 'RESET_TILES_CACHE' })
  await nav.fetchRoute(planOptions, { navigate: false })
  // 削除: isNavWindowVisible.value = true
  // 削除: isNavWindowFullScreen.value = false
  // 削除: ensureNavWindowBounds()
}

// 削除: toggleNavWindow, openNavFullScreen, startDrag, attachGlobalPointerListeners, onPointerMove, endPointerInteraction, lockUserSelect, unlockUserSelect, handleWindowResize, ensureNavWindowBounds 関数

// 削除: watch(hasRoute, ...), watch(plan, ...), watch(isNavWindowVisible, ...)

watch(() => nav.plan?.route, (newRoute) => {
  if (
    position.isMock &&
    newRoute?.features?.[0]?.geometry?.type === 'LineString' &&
    newRoute.features[0].geometry.coordinates &&
    position.setMockTrack
  ) {
    const track = newRoute.features[0].geometry.coordinates;
    position.setMockTrack(track);
  }
}, { deep: true });
</script>

<template>
  <div class="page">
    <header class="bar">
      <h1>ナビ計画</h1>
      <div v-if="isMockPositionMode" class="mock-indicator" role="status">
        モック現在地モード
      </div>
    </header>

    <section class="form">
      <div class="row">
        <label for="lang">表示言語</label>
        <select id="lang" v-model="lang">
          <option value="ja">日本語</option>
          <option value="en">English</option>
          <option value="zh">中文</option>
        </select>
      </div>

      <div class="row">
        <label>スポット選択（複数可・順番に周遊）</label>
        <div class="chips-groups">
          <section>
            <p class="chips-heading">自然スポット</p>
            <div class="chips">
              <button
                v-for="p in spotPois"
                :key="`spot-${p.spot_id}`"
                @click="toggleSpot(p.spot_id)"
                :class="{ chip: true, on: selectedIds.includes(p.spot_id) }"
              >
                <span v-if="p.kind === 'facility'" class="facility-chip" aria-hidden="true">🏢</span>
                {{ displayName(p) }}
              </button>
              <p v-if="!spotPois.length" class="chips-empty">自然スポットが読み込めませんでした。</p>
            </div>
          </section>
          <section>
            <p class="chips-heading">施設</p>
            <div class="chips">
              <button
                v-for="p in facilityPois"
                :key="`facility-${p.spot_id}`"
                @click="toggleSpot(p.spot_id)"
                :class="{ chip: true, on: selectedIds.includes(p.spot_id) }"
              >
                <span class="facility-chip" aria-hidden="true">🏢</span>
                {{ displayName(p) }}
              </button>
              <p v-if="!facilityPois.length" class="chips-empty">施設が読み込めませんでした。</p>
            </div>
          </section>
        </div>
      </div>

      <div class="row" v-if="selectedIds.length > 0">
        <label>訪問順</label>
        <div class="reorder">
          <ul>
            <li v-for="(id, i) in selectedIds" :key="id">
              <span class="name">{{ i + 1 }}. {{ nameById(id) }}</span>
              <span class="ctrl">
                <button class="up" :disabled="i === 0" @click="move(i, -1)">↑</button>
                <button class="down" :disabled="i === selectedIds.length - 1" @click="move(i, 1)">
                  ↓
                </button>
                <button class="del" @click="remove(i)">×</button>
              </span>
            </li>
          </ul>
        </div>
      </div>

      <div v-if="hasError" class="error-box">
        <p>エラーが発生しました:</p>
        <pre>{{ nav.error }}</pre>
      </div>
      <div v-if="hasNoPosition" class="error-box">
        <p>現在地を取得中です...</p>
      </div>

      <div class="row">
        <button
          class="primary"
          :disabled="isRouteLoading || selectedIds.length === 0 || hasNoPosition"
          @click="submitPlan"
        >
          {{ isRouteLoading ? 'ルート作成中...' : 'ルートを作成して地図で確認' }}
        </button>
      </div>
    </section>
  </div>
  <!-- 削除: フローティングボタン -->
  <!-- 削除: Teleportとnav-window -->
  <NavWindow /> <!-- 追加 -->
</template>

<style scoped>
.page {
  max-width: 720px;
  margin: 0 auto;
  padding: 8px 12px 24px;
}
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.mock-indicator {
  margin-left: 16px;
  padding: 4px 12px;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.16);
  border: 1px solid rgba(37, 99, 235, 0.45);
  color: #1d4ed8;
  font-size: 0.85rem;
  letter-spacing: 0.05em;
  font-weight: 600;
  white-space: nowrap;
}
.form {
  margin-top: 8px;
}
.row {
  margin: 16px 0;
}
label {
  display: block;
  margin-bottom: 8px;
  font-weight: bold;
}
select {
  padding: 8px;
  font-size: 16px;
  width: 100%;
}
.chips-groups {
  display: grid;
  gap: 12px;
}

.chips-heading {
  margin: 0 0 6px;
  font-size: 14px;
  font-weight: 600;
  color: #374151;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chips-empty {
  color: #777;
  font-size: 13px;
  margin: 0;
}
.facility-chip {
  display: inline-block;
  margin-right: 4px;
  font-size: 16px;
  vertical-align: middle;
}
.chip {
  padding: 6px 12px;
  border: 1px solid #ccc;
  border-radius: 16px;
  background: #fff;
  cursor: pointer;
  transition: all 0.2s;
}
.chip.on {
  background: #e8f0fe;
  border-color: #2563eb;
  color: #2563eb;
  font-weight: bold;
}
.reorder {
  margin-top: 16px;
}
.reorder ul {
  list-style: none;
  padding: 0;
  margin: 0;
  border: 1px solid #ccc;
  border-radius: 8px;
  overflow: hidden;
}
.reorder li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border-bottom: 1px solid #eee;
}
.reorder li:last-child {
  border-bottom: none;
}
.reorder .name {
  font-size: 16px;
}
.reorder .ctrl {
  display: flex;
  gap: 4px;
}
.reorder .ctrl button {
  width: 28px;
  height: 28px;
  border-radius: 4px;
  border: 1px solid #ccc;
  background: #f8f8f8;
  cursor: pointer;
}
.reorder .ctrl button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.reorder .ctrl button.del {
  border-color: #e11d48;
  color: #e11d48;
}

button.primary {
  width: 100%;
  padding: 12px;
  font-size: 16px;
  background: #1d4ed8;
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
}
button.primary:disabled {
  background: #9ca3af;
  cursor: not-allowed;
}

.error-box {
  border: 1px solid #f87171;
  background-color: #fef2f2;
  color: #b91c1c;
  padding: 10px;
  border-radius: 6px;
  margin-top: 16px;
}

/* 削除: nav-window関連のスタイル */
</style>
