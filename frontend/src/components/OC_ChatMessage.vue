<template>
  <div class="tw-py-4 tw-px-4 md:tw-px-8">
    <div v-if="sender === 'user'" class="tw-flex tw-justify-end">
      <div class="tw-max-w-xl">
        <div class="tw-px-4 tw-py-3 tw-rounded-2xl tw-bg-blue-800 tw-text-white tw-rounded-br-none tw-shadow-sm">
          <p class="tw-text-base tw-leading-relaxed tw-whitespace-pre-wrap">{{ content }}</p>
        </div>
      </div>
    </div>

    <div v-else class="tw-max-w-4xl tw-mx-auto tw-space-y-3">
      <div v-if="isPending" class="tw-flex tw-items-center tw-gap-4">
        <div class="tw-relative tw-w-8 tw-h-8">
          <svg class="tw-absolute tw-top-0 tw-left-0 tw-w-full tw-h-full tw-overflow-visible animate-gemini-spinner-container" viewBox="0 0 24 24">
            <defs>
              <linearGradient :id="gradientId" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stop-color="#FF8A65" />
                <stop offset="50%" stop-color="#FFEB3B" />
                <stop offset="100%" stop-color="#69F0AE" />
              </linearGradient>
            </defs>
            <circle cx="12" cy="12" r="11" fill="none" :stroke="`url(#${gradientId})`" stroke-width="2.5" class="animate-gemini-spinner-arc" stroke-linecap="round" stroke-dasharray="69.115"></circle>
          </svg>
          <div class="tw-absolute tw-inset-0 tw-flex tw-items-center tw-justify-center">
            <img src="/app-icon.png" alt="App Icon" class="tw-w-5 tw-h-5 tw-rounded-full animate-icon-rotate">
          </div>
        </div>
        <p class="tw-text-sm tw-text-gray-300">{{ statusText || pendingText }}</p>
      </div>

      <div v-if="content || candidates || itinerary || prompt || profile">
        <div class="tw-w-8 tw-h-8 tw-flex tw-items-center tw-justify-start tw-shrink-0">
          <img src="/app-icon.png" alt="App Icon" class="tw-w-6 tw-h-6 tw-rounded-full">
        </div>

        <div
          v-if="content"
          class="tw-prose tw-prose-invert tw-prose-zinc lg:tw-prose-lg tw-max-w-none tw-pt-2 prose-p:tw-text-gray-50 prose-li:tw-text-gray-50 prose-headings:tw-text-white"
          v-html="formattedContent"
        ></div>

        <div v-if="candidates" class="tw-mt-4 tw-space-y-2">
          <div class="tw-flex tw-items-center tw-gap-2">
            <p class="tw-text-sm tw-font-semibold tw-text-white">おすすめ候補</p>
            <span class="tw-rounded-full tw-bg-slate-700 tw-px-2 tw-py-0.5 tw-text-xs tw-text-slate-200">
              {{ candidates.phase === 'provisional' ? '候補' : '確定' }}
            </span>
          </div>
          <div class="tw-grid tw-gap-2 sm:tw-grid-cols-2">
            <div
              v-for="item in candidates.items || []"
              :key="item.spot_id"
              class="tw-rounded-xl tw-border tw-border-slate-600 tw-bg-slate-800/70 tw-p-3"
            >
              <p class="tw-font-semibold tw-text-white">{{ item.name_ja || spotName(item.spot_id) }}</p>
              <p v-if="candidateReason(item)" class="tw-mt-1 tw-text-xs tw-leading-relaxed tw-text-slate-300">
                {{ candidateReason(item) }}
              </p>
            </div>
          </div>
        </div>

        <div v-if="itinerary" class="tw-mt-4 tw-rounded-xl tw-border tw-border-slate-600 tw-bg-slate-800/70 tw-p-4">
          <div class="tw-flex tw-flex-wrap tw-items-center tw-justify-between tw-gap-2">
            <div class="tw-flex tw-items-center tw-gap-2">
              <p class="tw-font-semibold tw-text-white">旅程 v{{ itinerary.version }}</p>
              <span class="tw-rounded-full tw-bg-slate-700 tw-px-2 tw-py-0.5 tw-text-xs tw-text-slate-200">
                {{ itinerary.phase === 'provisional' ? '調整中' : '確定' }}
              </span>
            </div>
            <button
              v-if="itinerary.phase === 'final' && itinerary.version > 1"
              type="button"
              :disabled="isUndoing"
              class="tw-rounded-lg tw-border tw-border-slate-500 tw-px-3 tw-py-1.5 tw-text-sm tw-text-slate-100 tw-transition-colors hover:tw-bg-slate-700 disabled:tw-cursor-not-allowed disabled:tw-opacity-50"
              @click="emit('undo', messageId)"
            >
              {{ isUndoing ? '戻しています…' : '元に戻す' }}
            </button>
          </div>

          <div class="tw-mt-3 tw-space-y-3">
            <div v-for="(day, dayIndex) in itineraryDays" :key="`${day.date}-${dayIndex}`">
              <p class="tw-text-sm tw-font-semibold tw-text-blue-200">
                {{ day.date || `${dayIndex + 1}日目` }}
              </p>
              <ol class="tw-mt-1 tw-space-y-1">
                <li
                  v-for="item in day.items || []"
                  :key="`${dayIndex}-${item.seq}-${item.spot_id}`"
                  class="tw-flex tw-items-baseline tw-gap-2 tw-text-sm tw-text-slate-200"
                >
                  <span class="tw-w-12 tw-shrink-0 tw-text-xs tw-text-slate-400">{{ formatMinute(item.arrive_min) }}</span>
                  <span>{{ spotName(item.spot_id) }}</span>
                  <span v-if="item.stay_min" class="tw-text-xs tw-text-slate-400">{{ item.stay_min }}分</span>
                </li>
              </ol>
            </div>
          </div>

          <ul v-if="diffLines.length" class="tw-mt-3 tw-space-y-1 tw-border-t tw-border-slate-700 tw-pt-3">
            <li v-for="line in diffLines" :key="line" class="tw-text-xs tw-text-slate-300">{{ line }}</li>
          </ul>
          <ul v-if="itinerary.concessions?.length" class="tw-mt-3 tw-space-y-1">
            <li v-for="(item, index) in itinerary.concessions" :key="index" class="tw-text-xs tw-text-amber-200">
              {{ item.message_ja || '一部の希望条件を調整しました。' }}
            </li>
          </ul>
          <p v-if="undoError" class="tw-mt-2 tw-text-xs tw-text-red-300">{{ undoError }}</p>
        </div>

        <div v-if="profile" class="tw-mt-3 tw-rounded-lg tw-border tw-border-slate-700 tw-bg-slate-800/50 tw-px-3 tw-py-2 tw-text-xs tw-text-slate-300">
          希望条件を更新しました<span v-if="profileSummary">: {{ profileSummary }}</span>
        </div>

        <div v-if="prompt" class="tw-mt-4 tw-space-y-2">
          <p class="tw-text-sm tw-text-slate-300">選択するか、下の入力欄から自由に回答できます。</p>
          <div class="tw-flex tw-flex-wrap tw-gap-2">
            <button
              v-for="(option, index) in prompt.options || []"
              :key="`${optionValue(option)}-${index}`"
              type="button"
              class="tw-rounded-full tw-border tw-border-blue-400/70 tw-bg-blue-500/10 tw-px-3 tw-py-1.5 tw-text-sm tw-text-blue-100 tw-transition-colors hover:tw-bg-blue-500/25 focus:tw-outline-none focus:tw-ring-2 focus:tw-ring-blue-400"
              @click="emit('select-option', { messageId, option })"
            >
              {{ optionLabel(option) }}
            </button>
          </div>
        </div>
      </div>

      <div v-if="notices?.length" class="tw-space-y-1">
        <p
          v-for="(notice, index) in notices"
          :key="`${notice.code || 'notice'}-${index}`"
          class="tw-text-xs"
          :class="notice.degraded ? 'tw-text-amber-200' : 'tw-text-red-300'"
        >
          {{ notice.message }}
        </p>
      </div>
      <p v-if="error" class="tw-text-sm tw-text-red-300">{{ error }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useUserStore } from '@/stores/user'
import { useNavStore } from '@/stores/nav'

const props = defineProps({
  messageId: { type: [String, Number], required: true },
  sender: { type: String, required: true },
  content: { type: String, default: '' },
  isPending: { type: Boolean, default: false },
  statusText: { type: String, default: '' },
  candidates: { type: Object, default: null },
  itinerary: { type: Object, default: null },
  prompt: { type: Object, default: null },
  profile: { type: Object, default: null },
  notices: { type: Array, default: () => [] },
  error: { type: String, default: '' },
  undoError: { type: String, default: '' },
  isUndoing: { type: Boolean, default: false },
})

const emit = defineEmits(['select-option', 'undo'])
const userStore = useUserStore()
const navStore = useNavStore()
const gradientId = `spinner-gradient-${Math.random().toString(36).substring(2, 9)}`

const formattedContent = computed(() => DOMPurify.sanitize(marked.parse(props.content || '')))
const itineraryDays = computed(() => props.itinerary?.itinerary?.days || [])

const spotName = (spotId) => {
  const spot = navStore.spots.find((value) => value.spot_id === spotId)
  return spot?.name_ja || spot?.name || spotId
}

const formatMinute = (value) => {
  const minute = Number(value)
  if (!Number.isFinite(minute)) return '--:--'
  const hour = Math.floor(minute / 60)
  return `${String(hour).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`
}

const candidateReason = (item) => {
  const reason = item?.reason_materials || {}
  const parts = []
  if (Array.isArray(reason.matched_tags) && reason.matched_tags.length) {
    parts.push(reason.matched_tags.join('・'))
  }
  if (reason.travel_time_text) parts.push(reason.travel_time_text)
  if (reason.stay_min) parts.push(`滞在目安 ${reason.stay_min}分`)
  return parts.join(' / ')
}

const positionText = (position) => {
  if (!position) return ''
  return `${position.day}日目 ${position.position}番目`
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

const profileSummary = computed(() => {
  const values = [props.profile?.party, props.profile?.mobility, props.profile?.pace].filter(Boolean)
  return values.join(' / ')
})

const optionLabel = (option) => typeof option === 'string' ? option : option?.label
const optionValue = (option) => typeof option === 'string' ? option : option?.value || option?.label

const pendingText = computed(() => {
  const lang = userStore.user?.language || 'ja'
  if (lang === 'en') return 'Please wait...'
  if (lang === 'zh') return '请稍候...'
  return 'お待ちください...'
})
</script>
