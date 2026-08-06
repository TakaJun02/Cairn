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

        <!-- §7.6.4/§7.6.5(2026-08-06 読みやすさのブラッシュアップ): 日見出しに
             DAY キッカー + 2 日目以降は罫線区切り。仮の前提は warn の帯に収め
             前提ごとに行を分ける。停留は 3 カラムグリッド(時刻/番号ピン/本文)
             で番号ピン付きのタイムラインにし、滞在は名前直下のメタ行へ。
             60 分以上は「n時間m分」表記。フッタの差分行はラベルと内容を
             分けて表示。差し色(--color-signal)は先頭ピンの塗りと DAY
             キッカーだけに使う(日付本文には使わない)。 -->
        <div v-if="itinerary" class="rounded-ui-lg border border-edge-strong bg-ink-raised p-5 shadow-soft">
          <!-- §7.6.5 の「日が 1 件以上あるとき」の形。見た目は変えない。 -->
          <template v-if="itineraryDays.length">
            <div
              v-for="(day, dayIndex) in itineraryDays"
              :key="`${day.date}-${dayIndex}`"
              :class="dayIndex > 0 ? 'mt-5 border-t border-edge pt-5' : ''"
            >
              <div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <div class="flex flex-wrap items-baseline gap-2.5">
                  <!-- §7.6.4 の 14: 日付が実際に取れたときだけ DAY キッカーを
                       出す。見出しが「n日目」フォールバックのときは重複する
                       ため出さない(dayHeading 自体は変えない)。 -->
                  <span
                    v-if="dayKicker(day, dayIndex)"
                    class="font-display text-[11px] font-medium tracking-[0.14em] text-brand-signal"
                  >{{ dayKicker(day, dayIndex) }}</span>
                  <b class="font-display text-[16px] font-semibold text-text">{{ dayHeading(day, dayIndex) }}</b>
                  <span
                    v-if="dayIndex === 0 && itinerary.phase === 'provisional'"
                    class="rounded-full border border-edge-strong bg-fill-hover px-2.5 py-0.5 text-[10.5px] text-text-muted"
                  >調整中</span>
                </div>
                <p v-if="daySummary(day)" class="font-display text-xs tabular-nums text-text-muted">{{ daySummary(day) }}</p>
              </div>

              <!-- §7.6.4 の 13: 仮の前提は warn の帯に収め、前提ごとに行を
                   分ける(「、」連結をやめる)。畳まない原則(§7.6.2 の 2)は
                   継続。カード全体で 1 度、最初の日の見出し直下に表示。 -->
              <div
                v-if="dayIndex === 0 && itineraryAssumptions.length"
                class="mt-2.5 flex items-start gap-2 rounded-ui-sm border border-warn/[0.14] bg-warn/[0.07] px-3 py-2 text-xs text-warn"
              >
                <span aria-hidden="true">⚠</span>
                <div class="flex flex-col gap-1">
                  <p v-for="(assumption, index) in itineraryAssumptions" :key="index" class="m-0">{{ assumption }}</p>
                </div>
              </div>

              <!-- §7.6.5 空の状態(2026-08-06 レビュー是正): 停留が 0 件の日は
                   順路の位置に 1 行だけ出す。 -->
              <ol v-if="dayRows(day).length" class="stop-list mt-3.5">
                <template v-for="row in dayRows(day)" :key="row.key">
                  <li
                    v-if="row.type === 'stop'"
                    class="stop-item"
                    :class="{ 'stop-item--first': row.index === 0, 'stop-item--last': isLastStopOfDay(day, row) }"
                  >
                    <span class="stop-item__time">{{ formatMinute(row.item.arrive_min) }}</span>
                    <span class="stop-item__pin" :class="{ 'stop-item__pin--first': row.index === 0 }">{{ row.index + 1 }}</span>
                    <div class="stop-item__body">
                      <div class="stop-item__name">{{ spotName(row.item.spot_id) }}</div>
                      <div v-if="formatDurationJa(row.item.stay_min)" class="stop-item__stay">滞在 {{ formatDurationJa(row.item.stay_min) }}</div>
                    </div>
                  </li>
                  <li v-else class="move-item">
                    <span class="move-item__label">↓ 移動 {{ row.text }}</span>
                  </li>
                </template>
              </ol>
              <p v-else class="mt-3.5 text-xs text-text-dim">この日はまだ予定がありません</p>
            </div>
          </template>

          <!-- §7.6.5 空の状態(2026-08-06 レビュー是正): 日が 0 件の旅程
               (ストアの防御的フォールバック `{ days: [] }` 経路)。日見出しは
               出せないが、「調整中」バッジと仮の前提の帯は失わない
               (未確認の前提が黙って消えるのは畳むより悪い。§7.6.2 の 2)。 -->
          <div v-else class="flex flex-col gap-2.5">
            <span
              v-if="itinerary.phase === 'provisional'"
              class="self-start rounded-full border border-edge-strong bg-fill-hover px-2.5 py-0.5 text-[10.5px] text-text-muted"
            >調整中</span>
            <div
              v-if="itineraryAssumptions.length"
              class="flex items-start gap-2 rounded-ui-sm border border-warn/[0.14] bg-warn/[0.07] px-3 py-2 text-xs text-warn"
            >
              <span aria-hidden="true">⚠</span>
              <div class="flex flex-col gap-1">
                <p v-for="(assumption, index) in itineraryAssumptions" :key="index" class="m-0">{{ assumption }}</p>
              </div>
            </div>
            <p class="text-xs text-text-dim">この旅程にはまだ予定がありません</p>
          </div>

          <!-- フッタ: 差分・譲歩は左、v{n} と「元に戻す」は右(§7.6.3 のまま)。
               §7.6.5: 差分行はラベル(text-text-dim)と内容(text-text-muted)を
               分け、text-[12.5px] に上げる。ラベルと内容の間は mr-1 で明示的に
               空ける(要素間の改行だけに頼ると Vue の whitespace condense で
               空白ノードが消え、間隔なしで連結して見える。2026-08-06 レビュー
               是正)。版はもう見出しではないので、常にここに小さく置く。 -->
          <div class="mt-5 flex flex-wrap items-start justify-between gap-3 border-t border-edge pt-3.5">
            <div class="flex-1 space-y-1 text-[12.5px]">
              <p v-for="(line, index) in diffLines" :key="index">
                <span class="mr-1 text-text-dim">{{ line.label }}</span>
                <span class="text-text-muted">{{ line.body }}</span>
              </p>
              <p v-for="(item, index) in (itinerary.concessions || [])" :key="`concession-${index}`" class="text-warn">
                {{ concessionText(item) }}
              </p>
              <p v-if="undoError" class="text-danger">{{ undoError }}</p>
            </div>
            <div class="flex shrink-0 items-center gap-2">
              <button
                v-if="itinerary.phase === 'final' && itinerary.version > 1"
                type="button"
                :disabled="isUndoing"
                class="flex min-h-11 items-center rounded-ui-sm border border-edge-strong px-3.5 text-[12.5px] text-text-muted transition-colors duration-fast hover:bg-fill-hover hover:text-text disabled:cursor-not-allowed disabled:opacity-50"
                @click="emit('undo', messageId)"
              >{{ isUndoing ? '戻しています…' : '元に戻す' }}</button>
              <span class="font-display text-[11px] text-text-dim">v{{ itinerary.version }}</span>
            </div>
          </div>
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
import { humanizeIsoDates, formatDurationJa } from '@/lib/dateDisplay.js'
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
// 使わない。final はバッジ自体を出さない。provisional のときだけ「調整中」
// バッジを出す(frontend_nav.md §2.4)。2026-08-06(§7.6.3): バッジの位置は
// 見出しの隣から、最初の日の見出し行へ移した(見出し自体が「旅程 v{n}」
// ではなく日付になったため)。

// 未確認の前提(日付・起点等)。空なら何も表示しない。
// §7.7.1: 文中に混ざる ISO 日付(例:「日付は明日(2026-08-05)と仮定」)も
// 表示直前に人の書式へ整形する(旧形式で永続化済みの文が再浮上する経路への
// 防御。spot_id を maskSpotIds で防いでいるのと同じ理由)。
const itineraryAssumptions = computed(() => {
  const values = props.itinerary?.assumptions ?? props.itinerary?.itinerary?.assumptions
  return Array.isArray(values) ? values.map((value) => humanizeIsoDates(value)) : []
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

// §7.6.2 の 3 / §7.6.4 の 16: 5 分丸め。formatMinute(時刻表示)と
// displayedTravelGapMinutes(移動時間の表示値の導出)の両方から参照する
// 共有の丸め関数(二重実装しない)。非数は NaN を返す。
function round5(value) {
  const minute = Number(value)
  if (!Number.isFinite(minute)) return NaN
  return Math.round(minute / 5) * 5
}

// §7.6.2 の 3: 時刻は 5 分に丸めて出す(元の値は変えない。表示だけ)。
// 計画に分の精度は無く、丸めるほうが誠実(§7.6.1 原因 d)。時は 0 埋めしない
// (§7.6.3 のモック「9:30」に合わせる)。
const formatMinute = (value) => {
  const rounded = round5(value)
  if (!Number.isFinite(rounded)) return '--:--'
  const normalized = ((rounded % 1440) + 1440) % 1440
  const hour = Math.floor(normalized / 60)
  const min = normalized % 60
  return `${hour}:${String(min).padStart(2, '0')}`
}

const WEEKDAY_JA = ['日', '月', '火', '水', '木', '金', '土']

// §7.6.2 の 1: 見出しは ISO 日付ではなく「8月5日(火)」形式。`compact` は
// 「6日(水)」のように月を省く(日跨ぎレンジの後半・同月のとき)。
function formatDateHeading(dateStr, { compact = false } = {}) {
  const match = typeof dateStr === 'string' && dateStr.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!match) return null
  const [, y, m, d] = match
  const date = new Date(Number(y), Number(m) - 1, Number(d))
  if (Number.isNaN(date.getTime())) return null
  const weekday = WEEKDAY_JA[date.getDay()]
  return compact ? `${Number(d)}日(${weekday})` : `${Number(m)}月${Number(d)}日(${weekday})`
}

function addDaysToIsoDate(dateStr, days) {
  const match = typeof dateStr === 'string' && dateStr.match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!match || days <= 0) return null
  const [, y, m, d] = match
  const date = new Date(Number(y), Number(m) - 1, Number(d) + days)
  const yyyy = date.getFullYear()
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  return `${yyyy}-${mm}-${dd}`
}

// data_model.md §7.2: `arrive_min`/`depart_min` は日跨ぎ(24時超え)を表現
// できる整数分。その日の項目が 1440 分を跨ぐときだけ、見出しを
// 「8月5日(火) 〜 6日(水)」のレンジにする(§7.6.2 の 1 の「複数日なら」)。
// 跨がない通常時は単一の日付見出しのまま。
function dayHeading(day, index) {
  const startHeading = formatDateHeading(day?.date)
  if (!startHeading) return `${index + 1}日目`
  const items = Array.isArray(day?.items) ? day.items : []
  const candidates = [Number(day?.end_min)]
  for (const item of items) {
    candidates.push(Number(item?.depart_min), Number(item?.arrive_min))
  }
  const maxMinute = Math.max(0, ...candidates.filter(Number.isFinite))
  const spilloverDays = Math.floor(maxMinute / 1440)
  if (spilloverDays > 0) {
    const nextDate = addDaysToIsoDate(day.date, spilloverDays)
    if (nextDate) {
      const sameMonth = day.date.slice(0, 7) === nextDate.slice(0, 7)
      const endHeading = formatDateHeading(nextDate, { compact: sameMonth })
      if (endHeading) return `${startHeading} 〜 ${endHeading}`
    }
  }
  return startHeading
}

// §7.6.4 の 14: 日付が実際に取れたときだけ DAY キッカーを出す(dayHeading が
// 「n日目」フォールバックを返すときは、キッカーと見出しが同じ意味を二重に
// 言うことになるため出さない)。dayHeading 自体は変えず、内部で使っている
// formatDateHeading をそのまま流用する。
function dayKicker(day, dayIndex) {
  if (!formatDateHeading(day?.date)) return null
  return `DAY ${dayIndex + 1}`
}

// §7.6.2 の 5: 見出しの右に「n か所 · 開始 → 終了」。終了 = 最後の到着 + 滞在。
function daySummary(day) {
  const items = Array.isArray(day?.items) ? day.items : []
  if (!items.length) return ''
  const first = items[0]
  const last = items[items.length - 1]
  const start = formatMinute(first?.arrive_min)
  const lastArrive = Number(last?.arrive_min)
  const lastStay = Number(last?.stay_min) || 0
  const end = formatMinute(Number.isFinite(lastArrive) ? lastArrive + lastStay : NaN)
  return `${items.length}か所 · ${start} → ${end}`
}

// §7.6.2 の 4: 連続する 2 つの滞在から「次の到着 −(今の到着 + 滞在)」で
// 移動分を導く(サーバーの契約は変えない)。移動行を出すか自体は、この
// 生の間隙(round 前の arrive_min/stay_min)が正のときだけ検討する
// (丸めが作る見かけの移動まで拾わないための入口のゲート)。実際に表示する
// 分数は displayedTravelGapMinutes(§7.6.4 の 16)が別に導く。
function travelGapMinutes(current, next) {
  const arriveCur = Number(current?.arrive_min)
  const arriveNext = Number(next?.arrive_min)
  const stayCur = Number(current?.stay_min) || 0
  if (!Number.isFinite(arriveCur) || !Number.isFinite(arriveNext)) return 0
  return arriveNext - (arriveCur + stayCur)
}

// §7.6.4 の 16(2026-08-06 レビュー是正): 時刻だけ 5 分丸めで滞在・移動が
// 生値のままだと、行を縦に足したとき表示時刻と合わない
// (例: 9:30 + 40分 + 27分 = 10:37 なのに次行が 10:40)。§7.6.2 の 4 の式
// (次の到着 −(今の到着 + 滞在))自体は変えず、入力を表示済みの丸めた
// 時刻に揃える: round5(次の到着) −(round5(今の到着)+ 滞在)。滞在自体は
// ここでは丸めない(滞在の表示丸めは formatDurationJa が別に行う)。
function displayedTravelGapMinutes(current, next) {
  const arriveCur = round5(current?.arrive_min)
  const arriveNext = round5(next?.arrive_min)
  const stayCur = Number(current?.stay_min) || 0
  if (!Number.isFinite(arriveCur) || !Number.isFinite(arriveNext)) return 0
  return arriveNext - (arriveCur + stayCur)
}

// 各日を「停留」と「移動」の行に組む(表ではなく順路に見せる。§7.6.2 の 4)。
// §7.6.4 の 16 / レビュー指摘 6(生成条件と表示条件の二重化の是正):
// 移動行を出すかの判定(生の間隙が正か)と表示テキストの生成(丸めた時刻
// からの導出 → formatDurationJa。導出値が 0 以下なら省く)をここで一括
// して決める。テンプレート側は row.text を出すだけにする。
function dayRows(day) {
  const items = Array.isArray(day?.items) ? day.items : []
  const rows = []
  items.forEach((item, index) => {
    rows.push({ type: 'stop', key: `stop-${index}-${item.spot_id}`, item, index })
    const next = items[index + 1]
    if (next && travelGapMinutes(item, next) > 0) {
      const text = formatDurationJa(displayedTravelGapMinutes(item, next))
      if (text) rows.push({ type: 'move', key: `move-${index}`, text })
    }
  })
  return rows
}

// §7.6.4 の 10 / §7.6.5: 縦線は番号ピンの中心を通し、最初のピンの上・最後の
// ピンの下では描かない(停留が 1 つだけの日は、先頭かつ末尾になるので線
// 自体を描かない)。dayRows の計算自体は変えず、日内の位置判定だけを足す。
function isLastStopOfDay(day, row) {
  const items = Array.isArray(day?.items) ? day.items : []
  return row.index === items.length - 1
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

// §7.6.5: フッタの差分行はラベル(text-text-dim)と内容(text-text-muted)を
// 分けて表示するため、文字列ではなく {label, body} で組む(旧: 1 本の文字列)。
const diffLines = computed(() => {
  const diff = props.itinerary?.diff || {}
  const lines = []
  if (diff.added?.length) lines.push({ label: '追加:', body: diff.added.map(spotName).join('、') })
  if (diff.removed?.length) lines.push({ label: '削除:', body: diff.removed.map(spotName).join('、') })
  for (const moved of diff.moved || []) {
    lines.push({
      label: '移動:',
      body: `${spotName(moved.spot_id)} (${positionText(moved.from)} → ${positionText(moved.to)})`,
    })
  }
  if (diff.retimed?.length) lines.push({ label: '時刻変更:', body: diff.retimed.map(spotName).join('、') })
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
/* 旅程の訪問順(§7.6.4/§7.6.5)。停留は 3 カラムグリッド(時刻 46px / 番号
   ピン 22px / 本文 1fr、gap 12px)。縦線は番号ピンの中心(左端から 69px)を
   通す 1px だが、left は 68px の整数に置く — 68.5px だと DPR=1 の画面で
   2 デバイスピクセルへ半分ずつ滲み、実効 8.5% × 2 本になって原因 i(線が
   見えない)が再発する(2026-08-06 レビュー指摘)。中心との 0.5px 差は
   22px の不透明なピンの陰に隠れて見えない。最初のピンの上・最後のピンの
   下では描かない(停留が 1 つだけの日は先頭かつ末尾になるので線自体を
   描かない)。差し色(--color-signal)は先頭ピンの塗りと DAY キッカーだけに
   使う(§7.6.4 の 10)。 */
.stop-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.stop-item,
.move-item {
  position: relative;
  display: grid;
  grid-template-columns: 46px 22px 1fr;
  column-gap: 12px;
}
.stop-item {
  padding: 4px 0;
}
.stop-item::before,
.move-item::before {
  content: "";
  position: absolute;
  left: 68px;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--color-edge-strong);
}
.stop-item--first::before {
  top: 14px;
}
.stop-item--last::before {
  bottom: auto;
  height: 14px;
}
.stop-item--first.stop-item--last::before {
  display: none;
}
.stop-item__time {
  padding-top: 3px;
  text-align: right;
  font-family: var(--font-display);
  font-variant-numeric: tabular-nums;
  font-size: 13px;
  color: var(--color-text-muted);
}
/* §7.6.4 の 9: 22px の番号ピン。日の先頭だけ差し色で塗り、以降は中空の
   グレー地にする。 */
.stop-item__pin {
  position: relative;
  z-index: 1;
  margin-top: 3px;
  width: 22px;
  height: 22px;
  border-radius: 9999px;
  display: grid;
  place-items: center;
  font-family: var(--font-display);
  font-size: 11px;
  font-weight: 600;
  background: var(--color-high);
  border: 1px solid var(--color-edge-strong);
  color: var(--color-text-muted);
}
.stop-item__pin--first {
  background: var(--color-signal);
  border-color: var(--color-signal);
  color: var(--color-paper-ink);
}
.stop-item__name {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.5;
  padding-top: 2px;
  color: var(--color-text);
}
.stop-item__stay {
  font-size: 12px;
  color: var(--color-text-dim);
}

/* §7.6.2 の 4 / §7.6.4 の 12: 行間の移動時間。本文カラム(3 列目)に置き、
   縦線は移動行の背後も通る。 */
.move-item__label {
  grid-column: 3;
  padding: 3px 0;
  font-size: 11.5px;
  color: var(--color-text-dim);
}
</style>
