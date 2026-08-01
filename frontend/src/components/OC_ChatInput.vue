<template>
  <div
    class="tw-bg-slate-50/90 tw-backdrop-blur-lg"
    style="box-shadow: 0 -8px 32px -10px rgba(0, 0, 0, 0.08);"
  >
    <div class="tw-max-w-4xl tw-mx-auto tw-px-4 tw-py-3">
      <div class="tw-relative tw-flex tw-items-end tw-gap-2">
        <textarea
          ref="textarea"
          v-model="value"
          @input="adjustTextareaHeight"
          @keydown.enter.prevent="handleEnter"
          :placeholder="placeholder"
          class="tw-flex-1 tw-bg-slate-100 focus:tw-bg-slate-200/60 tw-rounded-2xl tw-border-none focus:tw-ring-0 tw-resize-none tw-py-2.5 tw-px-4 tw-text-base tw-text-gray-800 placeholder:tw-text-gray-500 tw-transition-colors tw-duration-200"
          rows="1"
          style="max-height: 200px;"
        ></textarea>
        <button
          @click="handlePrimaryAction"
          :disabled="!props.isSending && value.trim() === ''"
          class="tw-w-9 tw-h-9 tw-rounded-full tw-flex-shrink-0 tw-flex tw-items-center tw-justify-center tw-transition-all tw-duration-200 tw-mb-0.5"
          :class="props.isSending
            ? 'tw-bg-red-600 tw-text-white hover:tw-bg-red-500 active:tw-scale-90'
            : value.trim() === ''
              ? 'tw-text-slate-400 tw-cursor-not-allowed'
              : 'tw-bg-slate-800 tw-text-white hover:tw-bg-slate-700 active:tw-scale-90'"
          :aria-label="props.isSending ? '応答を停止' : 'メッセージを送信'"
        >
          <svg v-if="props.isSending" class="tw-h-4 tw-w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <rect x="6" y="6" width="12" height="12" rx="1"></rect>
          </svg>
          <svg v-else xmlns="http://www.w3.org/2000/svg" class="tw-h-5 tw-w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <polyline points="19 12 12 19 5 12"></polyline>
          </svg>
        </button>
      </div>
    </div>
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

const handleEnter = (event) => {
  if (event.shiftKey || props.isSending) return
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
