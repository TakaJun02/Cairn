<template>
  <div :class="sender === 'user' ? 'is-user flex justify-end' : 'is-ai flex flex-col gap-3.5'">
    <!-- ユーザー発話 -->
    <div
      v-if="sender === 'user'"
      class="max-w-[88%] whitespace-pre-wrap break-words rounded-[1.35rem] rounded-br-md border border-white/[0.075] bg-ink-high px-4 py-2.5 leading-7 shadow-soft sm:max-w-[78%]"
    >{{ content }}</div>

    <!-- アシスタント発話。吹き出しを持たない。 -->
    <template v-else>
      <div v-if="isPending" class="flex items-center gap-4">
        <div class="relative h-8 w-8 shrink-0">
          <svg class="animate-gemini-spinner-container absolute left-0 top-0 h-full w-full overflow-visible" viewBox="0 0 24 24">
            <defs>
              <linearGradient :id="gradientId" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stop-color="#4f7df3" />
                <stop offset="50%" stop-color="#2fc9b0" />
                <stop offset="100%" stop-color="#cfe6ee" />
              </linearGradient>
            </defs>
            <circle cx="12" cy="12" r="11" fill="none" :stroke="`url(#${gradientId})`" stroke-width="2.5" class="animate-gemini-spinner-arc" stroke-linecap="round" stroke-dasharray="69.115"></circle>
          </svg>
          <div class="absolute inset-0 flex items-center justify-center">
            <img src="/app-icon.png" alt="" class="animate-icon-rotate h-5 w-5 rounded-full">
          </div>
        </div>
        <p class="text-sm text-text-muted">{{ statusText || pendingText }}</p>
      </div>

      <template v-if="content || candidates || itinerary || profileLabelList.length">
        <div class="flex h-6 w-6 shrink-0 items-center justify-center">
          <img src="/app-icon.png" alt="" class="h-6 w-6 rounded-full">
        </div>

        <div
          v-if="content"
          class="markdown-body"
          v-html="formattedContent"
        ></div>

        <div v-if="candidates">
          <div class="mb-2.5 flex items-center gap-2">
            <b class="font-display text-[13.5px] font-semibold text-text">おすすめ候補</b>
            <span
              v-if="candidates.phase === 'provisional'"
              class="rounded-full border border-edge-strong bg-fill-hover px-2.5 py-0.5 text-[10.5px] text-text-muted"
            >候補</span>
          </div>
          <div class="grid gap-2 sm:grid-cols-2">
            <div
              v-for="item in candidates.items || []"
              :key="item.spot_id"
              class="rounded-ui border border-edge bg-ink-surface p-3 transition-all duration-base ease-expressive hover:-translate-y-px hover:border-edge-strong hover:bg-ink-raised"
            >
              <p class="font-medium text-text">{{ candidateName(item) }}</p>
              <div v-if="candidateChips(item).length" class="mt-2.5 flex flex-wrap gap-1.5">
                <span
                  v-for="(chip, index) in candidateChips(item)"
                  :key="index"
                  class="rounded-full border border-edge bg-fill-hover px-2 py-0.5 text-[10px] text-text-muted"
                >{{ chip }}</span>
              </div>
            </div>
          </div>
        </div>

        <div v-if="itinerary" class="rounded-ui-lg border border-edge-strong bg-ink-raised p-4 shadow-soft">
          <div class="flex flex-wrap items-center gap-2">
            <b class="font-display text-[15px] font-semibold tracking-[-0.02em] text-text">旅程 v{{ itinerary.version }}</b>
            <span
              v-if="itinerary.phase === 'provisional'"
              class="rounded-full border border-edge-strong bg-fill-hover px-2.5 py-0.5 text-[10.5px] text-text-muted"
            >調整中</span>
            <details v-if="itineraryAssumptions.length" class="relative" :title="itineraryAssumptions.join('、')">
              <!-- §11 是正: <summary> も押せる操作子であり 44px の対象。
                   見た目のチップは 23px のまま、当たり判定だけ min-h-11 で
                   広げる(背景は hover まで出さない = チップの背景をこの
                   要素に持たせない)。
                   2026-08-06(F4 是正): 当たり判定(summary, 角丸 0・44px)に
                   フォーカス枠を出すと、中の小さな rounded-full ピルの外へ
                   矩形の枠がはみ出す(コンポーザと同型の不具合)。枠は
                   「見えている」内側の span へ出す(下の <style> 参照)。 -->
              <summary
                class="assumption-toggle inline-flex min-h-11 cursor-pointer list-none items-center"
              ><span
                class="inline-flex items-center rounded-full border border-warn/35 bg-warn/10 px-2.5 py-0.5 text-[10.5px] text-warn"
              >仮の前提あり</span></summary>
              <ul class="mt-1 space-y-0.5 rounded-ui-sm border border-edge-strong bg-ink-base/90 p-2 text-xs text-warn">
                <li v-for="(assumption, index) in itineraryAssumptions" :key="index">{{ assumption }}</li>
              </ul>
            </details>
            <button
              v-if="itinerary.phase === 'final' && itinerary.version > 1"
              type="button"
              :disabled="isUndoing"
              class="ml-auto flex min-h-11 items-center rounded-ui-sm border border-edge-strong px-3.5 text-[12.5px] text-text-muted transition-colors duration-fast hover:bg-fill-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-50"
              @click="emit('undo', messageId)"
            >{{ isUndoing ? '戻しています…' : '元に戻す' }}</button>
          </div>

          <div class="mt-4">
            <div v-for="(day, dayIndex) in itineraryDays" :key="`${day.date}-${dayIndex}`" class="mt-4 first:mt-0">
              <p class="mb-2 font-display text-[12.5px] font-semibold tracking-[0.02em] text-brand-signal">
                {{ day.date || `${dayIndex + 1}日目` }}
              </p>
              <ol class="stop-list">
                <li
                  v-for="item in day.items || []"
                  :key="`${dayIndex}-${item.seq}-${item.spot_id}`"
                  class="stop-item"
                >
                  <span class="stop-item__time">{{ formatMinute(item.arrive_min) }}</span>
                  <span class="stop-item__name">{{ spotName(item.spot_id) }}</span>
                  <span v-if="item.stay_min" class="stop-item__stay">{{ item.stay_min }}分</span>
                </li>
              </ol>
            </div>
          </div>

          <ul v-if="diffLines.length" class="mt-4 space-y-1 border-t border-edge pt-3 text-xs text-text-muted">
            <li v-for="line in diffLines" :key="line">{{ line }}</li>
          </ul>
          <ul v-if="itinerary.concessions?.length" class="mt-3 space-y-1">
            <li v-for="(item, index) in itinerary.concessions" :key="index" class="text-xs text-warn">
              {{ concessionText(item) }}
            </li>
          </ul>
          <p v-if="undoError" class="mt-2 text-xs text-danger">{{ undoError }}</p>
        </div>

        <!-- P7: 希望条件は日本語ラベルのみ。全項目が空(対応表にない値含む)なら
             何も描画しない([25 §3-5] / frontend_design_system.md §7.7)。 -->
        <p v-if="profileLabelList.length" class="text-xs text-text-muted">
          希望条件を更新しました: {{ profileLabelList.join(' / ') }}
        </p>
      </template>

      <div v-if="notices?.length" class="space-y-1">
        <p
          v-for="(notice, index) in notices"
          :key="`${notice.code || 'notice'}-${index}`"
          class="text-xs"
          :class="notice.degraded ? 'text-warn' : 'text-danger'"
        >{{ notice.message }}</p>
      </div>
      <p v-if="error" class="text-sm text-danger">{{ error }}</p>
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useUserStore } from '@/stores/user'
import { useNavStore } from '@/stores/nav'
import { maskSpotIds, resolveCandidateName } from '@/lib/spotDisplay.js'
import { profileLabels } from '@/lib/profileLabels.js'

const props = defineProps({
  messageId: { type: [String, Number], required: true },
  sender: { type: String, required: true },
  content: { type: String, default: '' },
  isPending: { type: Boolean, default: false },
  statusText: { type: String, default: '' },
  candidates: { type: Object, default: null },
  itinerary: { type: Object, default: null },
  profile: { type: Object, default: null },
  notices: { type: Array, default: () => [] },
  error: { type: String, default: '' },
  undoError: { type: String, default: '' },
  isUndoing: { type: Boolean, default: false },
})

const emit = defineEmits(['undo'])
const userStore = useUserStore()
const navStore = useNavStore()
const gradientId = `spinner-gradient-${Math.random().toString(36).substring(2, 9)}`

const formattedContent = computed(() => DOMPurify.sanitize(marked.parse(props.content || '')))
const itineraryDays = computed(() => props.itinerary?.itinerary?.days || [])

// 2026-08-04([25 §1-3]の是正・レビュー是正 L-1): `phase` はソルバー処理の
// 段階であってユーザーが内容を確定したという意味ではない。「確定」の語は
// 使わない。final はバッジ自体を出さない(見出しの「旅程 v{n}」と重複する
// ため)。provisional のときだけ「調整中」バッジを出す
// (frontend_nav.md §2.4)。

// 未確認の前提(日付・起点等)。空なら何も表示しない。
const itineraryAssumptions = computed(() => {
  const values = props.itinerary?.assumptions ?? props.itinerary?.itinerary?.assumptions
  return Array.isArray(values) ? values : []
})

// [25 §1-7](frontend_nav.md §2.4): 名前解決に失敗しても spot_id へは
// フォールバックしない。中立表記「不明な地点」を使う。
const resolveSpotName = (spotId) => {
  const spot = navStore.spots.find((value) => value.spot_id === spotId)
  return spot?.name_ja || spot?.name || null
}

const spotName = (spotId) => resolveSpotName(spotId) || '不明な地点'

// F7([25 §1-7] レビュー是正): 旧形式の永続 meta では `presented[].name_ja`
// (candidateReference)に生の spot_id がそのまま入っていることがあり、
// truthy なので `item.name_ja || spotName(...)` を素通りしていた。
// 純関数 `resolveCandidateName`(spotDisplay.js。単体テスト済み)に
// 切り出し、live SSE 経路・復元経路の両方をこの 1 箇所でカバーする。
const candidateName = (item) => resolveCandidateName(item, resolveSpotName)

// 譲歩(concessions[].message_ja)はサーバー契約上 spot_id を含まないが、
// 旧形式で永続化済みの版への表示前フィルタとして通す(frontend_nav.md §2.4)。
const concessionText = (item) => {
  const message = item?.message_ja
  if (!message) return '一部の希望条件を調整しました。'
  return maskSpotIds(message, resolveSpotName)
}

const formatMinute = (value) => {
  const minute = Number(value)
  if (!Number.isFinite(minute)) return '--:--'
  const hour = Math.floor(minute / 60)
  return `${String(hour).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`
}

// §7.5: タグ・所要時間・滞在目安を丸チップに分ける(現行は "/" 連結の 1 行)。
const candidateChips = (item) => {
  const reason = item?.reason_materials || {}
  const chips = []
  if (Array.isArray(reason.matched_tags)) {
    for (const tag of reason.matched_tags) {
      if (tag) chips.push(tag)
    }
  }
  if (reason.travel_time_text) chips.push(reason.travel_time_text)
  if (reason.stay_min) chips.push(`滞在目安 ${reason.stay_min}分`)
  return chips
}

const diffLines = computed(() => {
  const diff = props.itinerary?.diff || {}
  const lines = []
  if (diff.added?.length) lines.push(`追加: ${diff.added.map(spotName).join('、')}`)
  if (diff.removed?.length) lines.push(`削除: ${diff.removed.map(spotName).join('、')}`)
  for (const moved of diff.moved || []) {
    lines.push(`移動: ${spotName(moved.spot_id)} (${positionText(moved.from)} → ${positionText(moved.to)})`)
  }
  if (diff.retimed?.length) lines.push(`時刻変更: ${diff.retimed.map(spotName).join('、')}`)
  return lines
})

const positionText = (position) => {
  if (!position) return ''
  return `${position.day}日目 ${position.position}番目`
}

// P7 / §7.7: `party`/`mobility`/`pace` の生 enum を出さない。対応表に無い値・
// 空値は落とし、全項目が落ちたら何も描画しない(`profile` は非表示)。
const profileLabelList = computed(() => profileLabels(props.profile))

const pendingText = computed(() => {
  const lang = userStore.user?.language || 'ja'
  if (lang === 'en') return 'Please wait...'
  if (lang === 'zh') return '请稍候...'
  return 'お待ちください...'
})
</script>

<style scoped>
/* F4(frontend_design_system.md §11.1): 当たり判定を見た目より大きくした
   要素は、フォーカス枠を「見えている部分」に出す。この <summary> は当たり
   判定が 44px・角丸 0 だが、見えているのは中の rounded-full ピル(span)だけ
   なので、既定の外周線(summary 自身)を消し、span 側へ移す。 */
.assumption-toggle:focus-visible {
  outline: none;
}
.assumption-toggle:focus-visible > span {
  outline: 2px solid var(--color-signal-soft);
  outline-offset: 3px;
}

/* 旅程の訪問順(§7.6)。左の縦線が行をつなぎ、「順路」であることを見た目で
   表す(現行はただの箇条書き)。 */
.stop-list {
  list-style: none;
  margin: 0 0 0 52px;
  padding: 0;
  border-left: 1px solid var(--color-edge);
}
.stop-item {
  position: relative;
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 7px 0 7px 18px;
  font-size: 13.5px;
  color: var(--color-text);
}
.stop-item::before {
  content: "";
  position: absolute;
  left: -4.5px;
  top: 14px;
  width: 8px;
  height: 8px;
  border-radius: 9999px;
  background: var(--color-raised);
  border: 1.5px solid var(--color-edge-strong);
}
.stop-item__time {
  position: absolute;
  left: -52px;
  width: 44px;
  text-align: right;
  font-family: var(--font-display);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  color: var(--color-text-dim);
}
.stop-item__name {
  flex: 1;
}
.stop-item__stay {
  font-size: 11.5px;
  color: var(--color-text-dim);
}
</style>
