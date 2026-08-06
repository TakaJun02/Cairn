<template>
  <div class="plan-form">
    <div class="toolbar">
      <label>言語</label>
      <select v-model="langLocal">
        <option value="ja">日本語</option>
        <option value="en">English</option>
        <option value="zh">中文</option>
      </select>
    </div>

    <div class="toolbar">
      <input v-model="q" placeholder="スポット検索（名前で絞り込み）" />
    </div>

    <div class="columns">
      <!-- 候補リスト -->
      <div class="col">
        <h3>自然スポット（{{ filteredSpots.length }}）</h3>
        <ul class="list" v-if="filteredSpots.length">
          <li v-for="poi in filteredSpots" :key="poi.spot_id">
            <button class="add" @click="add(poi.spot_id)" :disabled="selectedIds.includes(poi.spot_id)">＋</button>
            <span class="name">
              <span v-if="poi.kind === 'facility'" class="facility-chip" aria-hidden="true">🏢</span>
              {{ displayName(poi) }}
            </span>
          </li>
        </ul>
        <p v-else class="empty">該当する自然スポットはありません。</p>

        <h3>施設（{{ filteredFacilities.length }}）</h3>
        <ul class="list" v-if="filteredFacilities.length">
          <li v-for="poi in filteredFacilities" :key="poi.spot_id">
            <button class="add" @click="add(poi.spot_id)" :disabled="selectedIds.includes(poi.spot_id)">＋</button>
            <span class="name">
              <span class="facility-chip" aria-hidden="true">🏢</span>
              {{ displayName(poi) }}
            </span>
          </li>
        </ul>
        <p v-else class="empty">該当する施設はありません。</p>
      </div>

      <!-- 選択順序 -->
      <div class="col">
        <h3>巡回順（{{ selectedIds.length }}）</h3>
        <ol class="list selected">
          <li v-for="(id, idx) in selectedIds" :key="id">
            <span class="order">{{ idx + 1 }}</span>
            <span class="name">{{ nameById(id) }}</span>
            <span class="actions">
              <button @click="move(idx, -1)" :disabled="idx === 0">↑</button>
              <button @click="move(idx, 1)" :disabled="idx === selectedIds.length - 1">↓</button>
              <button @click="remove(idx)">✕</button>
            </span>
          </li>
        </ol>

        <div class="origin">
          <label>出発地（緯度・経度）</label>
          <div class="row">
            <input type="number" step="0.000001" v-model.number="origin.lat" placeholder="lat">
            <input type="number" step="0.000001" v-model.number="origin.lon" placeholder="lon">
          </div>
        </div>

        <button class="primary" :disabled="!selectedIds.length" @click="start">
          この順でナビ開始
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useNavStore } from '@/stores/nav'
import { fetchPoiCatalog } from '@/lib/poi'

const store = useNavStore()

// ---- ローカル状態（送信時にstoreへ反映） ----
const langLocal = ref(store.lang || 'ja')
const origin = ref({
  lat: store.origin?.lat ?? 39.2201,
  lon: store.origin?.lon ?? 139.9006,
})
const pois = ref([])
const q = ref('')

// storeのフィールド名が「waypoints」「waypointIds」どちらでも拾えるように
const selectedIds = ref(
  Array.isArray(store.waypointIds) ? [...store.waypointIds]
  : Array.isArray(store.waypoints) ? [...store.waypoints]
  : []
)

// ---- POI & 施設の読み込み ----
onMounted(async () => {
  try {
    const catalog = fetchPoiCatalog({ includeFacilities: true })
    pois.value = catalog.sort((a, b) => baseDisplayName(a).localeCompare(baseDisplayName(b), 'ja'))
  } catch (e) {
    console.error('[plan] failed to load poi catalog', e)
    pois.value = []
  }
})

// ---- 表示名ユーティリティ ----
function baseDisplayName(poi) {
  const names = poi?.names || poi?.official_name
  const localized = (names && names[langLocal.value]) || null
  if (localized) return String(localized)
  if (poi?.name) return String(poi.name)
  return poi?.spot_id ? String(poi.spot_id) : '(no name)'
}

function displayName(poi) {
  return baseDisplayName(poi)
}
function nameById(id) {
  const p = pois.value.find(x => x.spot_id === id)
  return p ? displayName(p) : id
}

const filtered = computed(() => {
  const needle = q.value.trim().toLowerCase()
  if (!needle) return pois.value
  return pois.value.filter((p) => {
    const base = baseDisplayName(p)
    const category = p?.category ? String(p.category) : ''
    const spotId = p?.spot_id ? String(p.spot_id) : ''
    const names = p?.names && typeof p.names === 'object'
      ? Object.values(p.names).filter(Boolean).join(' ') : ''
    return [displayName(p), base, category, spotId, names]
      .some((str) => str && String(str).toLowerCase().includes(needle))
  })
})

const filteredSpots = computed(() => filtered.value.filter(p => p.kind !== 'facility'))
const filteredFacilities = computed(() => filtered.value.filter(p => p.kind === 'facility'))

// ---- 選択操作 ----
function add(id) {
  if (!selectedIds.value.includes(id)) selectedIds.value.push(id)
}
function remove(idx) {
  selectedIds.value.splice(idx, 1)
}
function move(idx, delta) {
  const ni = idx + delta
  if (ni < 0 || ni >= selectedIds.value.length) return
  const arr = selectedIds.value
  const [v] = arr.splice(idx, 1)
  arr.splice(ni, 0, v)
}

// 親へ送信
const emit = defineEmits(['start'])
function start() {
  emit('start', {
    lang: langLocal.value,
    origin: { lat: Number(origin.value.lat), lon: Number(origin.value.lon) },
    waypoints: [...selectedIds.value],
  })
}
</script>

<style scoped>
.plan-form{ padding:16px; }
.toolbar{ margin:8px 0; display:flex; gap:8px; align-items:center; }
.columns{ display:grid; grid-template-columns: 1fr 1fr; gap:12px; }
@media (max-width: 720px){ .columns{ grid-template-columns: 1fr; } }
.list{ list-style:none; padding:0; margin:8px 0; max-height:60vh; overflow:auto; }
.list li{ display:flex; align-items:center; gap:8px; padding:6px 4px; border-bottom:1px solid #eee; }
.list .add{ width:2.2rem; height:2rem; }
.selected li .order{ width:1.6rem; text-align:right; opacity:.6; }
.actions button{ margin-left:4px; }
.origin{ margin-top:8px; }
.origin .row{ display:flex; gap:8px; }
.primary{ margin-top:12px; width:100%; height:44px; font-size:16px; }
.empty{ margin:4px 0 12px; color:#777; font-size:13px; }
.facility-chip{ display:inline-block; margin-right:4px; font-size:16px; vertical-align:middle; }
</style>
