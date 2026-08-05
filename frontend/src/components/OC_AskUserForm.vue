<template>
  <section
    class="rounded-ui-lg border border-edge-strong bg-ink-raised p-4 shadow-soft"
    aria-labelledby="ask-user-heading"
    :aria-busy="isSending"
  >
    <div class="flex items-start justify-between gap-4">
      <div class="flex items-start gap-3">
        <span
          class="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-signal/15 font-bold text-brand-signal"
          aria-hidden="true"
        >?</span>
        <div>
          <h2 id="ask-user-heading" class="font-display font-semibold text-text">
            {{ heading }}
          </h2>
          <p v-if="prompt.kind === 'clarify'" class="mt-1 text-sm text-text-muted">
            「<strong class="font-semibold text-text">{{ prompt.surface }}</strong>」はどちらですか
          </p>
          <p class="mt-1 text-sm text-text-muted">
            {{ promptReason }}
          </p>
        </div>
      </div>
      <button
        type="button"
        class="flex min-h-11 shrink-0 items-center rounded-ui-sm px-3 text-sm text-text-dim transition-colors duration-fast hover:bg-fill-hover hover:text-text"
        @click="emit('dismiss')"
      >あとで</button>
    </div>

    <div class="mt-4 grid gap-2 sm:grid-cols-2">
      <button
        v-for="(option, index) in prompt.options || []"
        :key="`${optionValue(option)}-${index}`"
        type="button"
        :disabled="isSending"
        class="flex min-h-11 items-center gap-2.5 rounded-ui-sm border border-edge-strong bg-fill-hover px-3.5 py-2 text-left text-[13px] text-text transition-colors duration-base hover:border-brand-signal/50 hover:bg-brand-signal/[0.08] disabled:cursor-not-allowed disabled:opacity-50"
        @click="emit('select-option', option)"
      >
        <span class="h-3.5 w-3.5 shrink-0 rounded-full border border-edge-strong" aria-hidden="true"></span>
        <span>{{ optionLabel(option) }}</span>
      </button>
    </div>

    <form class="mt-4 flex flex-col gap-2 sm:flex-row" @submit.prevent="submitFreeText">
      <input
        v-model="freeText"
        type="text"
        class="min-h-11 min-w-0 flex-1 rounded-ui-sm border border-edge bg-ink-base px-4 text-sm text-text placeholder:text-text-dim transition-colors focus:border-brand-signal focus:outline-none focus:ring-[3px] focus:ring-brand-signal/40"
        placeholder="自由に書いても答えられます"
        aria-label="自由入力で回答"
      >
      <button
        type="submit"
        :disabled="isSending || !freeText.trim()"
        class="flex min-h-11 items-center justify-center rounded-ui-sm bg-ink-paper px-5 text-sm font-semibold text-paper-ink transition-transform duration-base ease-expressive hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
      >回答する</button>
    </form>
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
