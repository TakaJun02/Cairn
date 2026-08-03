<template>
  <div class="tw-p-4 tw-space-y-6">
    <OC_ChatMessage
      v-for="message in messages"
      :key="message.id"
      :message-id="message.id"
      :sender="message.sender"
      :content="message.text || message.content"
      :is-pending="message.isPending"
      :status-text="message.statusText"
      :candidates="message.candidates"
      :itinerary="message.itinerary"
      :profile="message.profile"
      :notices="message.notices"
      :error="message.error"
      :undo-error="message.undoError"
      :is-undoing="isUndoing"
      @undo="$emit('undo', $event)"
    />
  </div>
</template>

<script setup>
import OC_ChatMessage from './OC_ChatMessage.vue';

defineProps({
  messages: {
    type: Array,
    required: true,
  },
  isUndoing: {
    type: Boolean,
    default: false,
  },
});

defineEmits(['undo']);
</script>
