<template>
  <section
    class="tw-border-y tw-border-slate-600 tw-bg-slate-900/95 tw-backdrop-blur-lg"
    aria-labelledby="ask-user-heading"
    :aria-busy="isSending"
  >
    <div class="tw-max-w-4xl tw-mx-auto tw-px-4 tw-py-4">
      <div class="tw-flex tw-items-start tw-justify-between tw-gap-4">
        <div class="tw-flex tw-items-start tw-gap-3">
          <span
            class="tw-flex tw-h-7 tw-w-7 tw-shrink-0 tw-items-center tw-justify-center tw-rounded-full tw-bg-blue-500/20 tw-font-bold tw-text-blue-200"
            aria-hidden="true"
          >?</span>
          <div>
            <h2 id="ask-user-heading" class="tw-font-semibold tw-text-white">
              {{ heading }}
            </h2>
            <p v-if="prompt.kind === 'clarify'" class="tw-mt-1 tw-text-sm tw-text-slate-200">
              「<strong class="tw-font-semibold tw-text-white">{{ prompt.surface }}</strong>」はどちらですか
            </p>
            <p class="tw-mt-1 tw-text-sm tw-text-slate-200">
              {{ promptReason }}
            </p>
          </div>
        </div>
        <button
          type="button"
          class="tw-shrink-0 tw-rounded-lg tw-px-3 tw-py-1.5 tw-text-sm tw-text-slate-300 tw-transition-colors hover:tw-bg-slate-800 hover:tw-text-white focus:tw-outline-none focus:tw-ring-2 focus:tw-ring-blue-400"
          @click="emit('dismiss')"
        >
          あとで
        </button>
      </div>

      <div class="tw-mt-4 tw-grid tw-gap-2 sm:tw-grid-cols-2">
        <button
          v-for="(option, index) in prompt.options || []"
          :key="`${optionValue(option)}-${index}`"
          type="button"
          :disabled="isSending"
          class="tw-flex tw-items-center tw-gap-2 tw-rounded-xl tw-border tw-border-blue-400/70 tw-bg-blue-500/10 tw-px-3 tw-py-2.5 tw-text-left tw-text-sm tw-text-blue-100 tw-transition-colors hover:tw-bg-blue-500/25 focus:tw-outline-none focus:tw-ring-2 focus:tw-ring-blue-400 disabled:tw-cursor-not-allowed disabled:tw-opacity-50"
          @click="emit('select-option', option)"
        >
          <span class="tw-h-3.5 tw-w-3.5 tw-shrink-0 tw-rounded-full tw-border tw-border-blue-300" aria-hidden="true"></span>
          <span>{{ optionLabel(option) }}</span>
        </button>
      </div>

      <form class="tw-mt-4 tw-flex tw-flex-col tw-gap-2 sm:tw-flex-row" @submit.prevent="submitFreeText">
        <input
          v-model="freeText"
          type="text"
          class="tw-min-w-0 tw-flex-1 tw-rounded-xl tw-border-0 tw-bg-slate-100 tw-px-4 tw-py-2.5 tw-text-sm tw-text-gray-800 placeholder:tw-text-gray-500 focus:tw-ring-2 focus:tw-ring-blue-400"
          placeholder="自由に書いても答えられます"
          aria-label="自由入力で回答"
        >
        <button
          type="submit"
          :disabled="isSending || !freeText.trim()"
          class="tw-rounded-xl tw-bg-blue-700 tw-px-5 tw-py-2.5 tw-text-sm tw-font-semibold tw-text-white tw-transition-colors hover:tw-bg-blue-600 focus:tw-outline-none focus:tw-ring-2 focus:tw-ring-blue-400 disabled:tw-cursor-not-allowed disabled:tw-opacity-50"
        >
          回答する
        </button>
      </form>
    </div>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  prompt: {
    type: Object,
    required: true,
  },
  isSending: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['select-option', 'answer', 'dismiss'])
const freeText = ref('')

const heading = computed(() => (
  props.prompt.kind === 'clarify' ? '確認させてください' : 'AIからの質問'
))

const promptReason = computed(() => {
  const reason = typeof props.prompt.reason === 'string' ? props.prompt.reason : ''
  return reason.trim() ? reason : 'ご希望に近いものを選んでください。'
})

const optionLabel = (option) => typeof option === 'string' ? option : option?.label
const optionValue = (option) => typeof option === 'string' ? option : option?.value || option?.label

const submitFreeText = () => {
  const answer = freeText.value.trim()
  if (!answer || props.isSending) return
  emit('answer', answer)
  freeText.value = ''
}

watch(
  () => props.prompt,
  () => {
    freeText.value = ''
  }
)
</script>
