<template>
  <div
    class="composer-shell flex items-end gap-2 rounded-[1.6rem] border border-edge-strong bg-ink-raised p-2 shadow-soft transition-colors duration-base focus-within:border-white/[0.32] focus-within:bg-ink-high"
    :class="{ 'is-streaming': isSending }"
  >
    <textarea
      ref="textarea"
      v-model="value"
      @input="adjustTextareaHeight"
      @keydown="handleKeydown"
      @compositionstart="isComposing = true"
      @compositionend="isComposing = false"
      :placeholder="placeholder"
      class="min-h-11 max-h-[164px] flex-1 resize-none overflow-y-auto bg-transparent px-3 py-2 text-[15px] leading-6 text-text outline-none placeholder:text-white/45"
      rows="1"
    ></textarea>
    <button
      @click="handlePrimaryAction"
      :disabled="!props.isSending && value.trim() === ''"
      class="flex h-11 w-11 shrink-0 items-center justify-center rounded-full transition-all duration-base ease-expressive"
      :class="props.isSending
        ? 'bg-ink-high text-text hover:-translate-y-0.5'
        : value.trim() === ''
          ? 'cursor-not-allowed bg-white/[0.07] text-white/25'
          : 'bg-ink-paper text-paper-ink hover:-translate-y-0.5'"
      :aria-label="props.isSending ? '応答を停止' : 'メッセージを送信'"
    >
      <svg v-if="props.isSending" class="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <rect x="6" y="6" width="12" height="12" rx="1"></rect>
      </svg>
      <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 19V5M5 12l7-7 7 7" />
      </svg>
    </button>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, computed } from 'vue'

const props = defineProps({
  isSending: {
    type: Boolean,
    default: false,
  },
  modelValue: {
    type: String,
    default: '',
  },
  placeholder: {
    type: String,
    default: 'Send a message...',
  },
})

const emit = defineEmits(['sendMessage', 'stop', 'update:modelValue'])
const textarea = ref(null)
// IME(日本語・中国語等)の変換確定 Enter が送信に食われないようにする
// (frontend_design_system.md §7.3。現行の `@keydown.enter.prevent` はバグ)。
const isComposing = ref(false)

const value = computed({
  get: () => props.modelValue,
  set: (newValue) => emit('update:modelValue', newValue),
})

const adjustTextareaHeight = () => {
  const element = textarea.value
  if (!element) return
  element.style.height = 'auto'
  element.style.height = `${element.scrollHeight}px`
}

const handleKeydown = (event) => {
  if (event.key !== 'Enter') return
  // ブラウザによっては isComposing が正しく立たない古い実装があるため、
  // keyCode 229(IME 変換中の共通コード)も合わせて見る。
  if (event.isComposing || isComposing.value || event.keyCode === 229) return
  if (event.shiftKey || props.isSending) return
  event.preventDefault()
  handleSendMessage()
}

const handlePrimaryAction = () => {
  if (props.isSending) {
    emit('stop')
    return
  }
  handleSendMessage()
}

const handleSendMessage = () => {
  if (props.isSending || value.value.trim() === '') return
  emit('sendMessage', value.value.trim())
  emit('update:modelValue', '')
  nextTick(adjustTextareaHeight)
}

watch(
  () => props.modelValue,
  () => nextTick(adjustTextareaHeight),
  { immediate: true }
)
</script>
