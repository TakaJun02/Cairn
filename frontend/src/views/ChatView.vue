<script setup>
import { ref, watch, nextTick, onMounted, computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useUserStore } from '@/stores/user'
import { useChatStore } from '@/stores/chat'

// Preserved from original component
import NavWindow from '@/components/NavWindow.vue'

// New UI components
import OC_ChatMessages from '@/components/OC_ChatMessages.vue';
import OC_AskUserForm from '@/components/OC_AskUserForm.vue';
import OC_ChatInput from '@/components/OC_ChatInput.vue';

const userStore = useUserStore()
const chatStore = useChatStore()
const { messages, isLoading, isSessionLoaded, isUndoing, currentPrompt, isAnswering } = storeToRefs(chatStore)

const messagesContainer = ref(null);

const chatInputText = ref('');
const isPromptDismissed = ref(false);

const isAskUserFormVisible = computed(() => Boolean(currentPrompt.value) && !isPromptDismissed.value);

// 質問フォーム表示中は下の通常入力欄を塞がない(frontend_nav.md §2.3.1 の
// 5)。isLoading はターン全体(質問待ちの間も含む)を表すため、これで
// そのまま「送信不可」にすると回答できなくなる。
const isChatInputBlocked = computed(() => isLoading.value && !currentPrompt.value);

// --- Dynamic placeholder for input ---
const placeholderText = computed(() => {
  const lang = userStore.user?.language || 'ja';
  switch (lang) {
    case 'en':
      return 'Send a message...';
    case 'zh':
      return '发送消息...';
    default: // 'ja'
      return '質問してみましょう';
  }
});

// --- Suggestion prompts ---
const suggestionTemplates = computed(() => {
  const lang = userStore.user?.language || 'ja';
  switch (lang) {
    case 'en':
      return [
        { title: 'Recommend spots', description: 'Show me some interesting places.', fullText: 'Show recommended spots' },
        { title: 'Add to plan', description: 'Add a specific spot to my itinerary.', fullText: 'Add (Spot Name) to the plan' },
        { title: 'Find restaurants', description: 'Help me find a place to eat.', fullText: 'Where can I eat?' },
      ];
    case 'zh':
      return [
        { title: '推荐景点', description: '告诉我一些有趣的地方。', fullText: '推荐一些景点' },
        { title: '添加到计划', description: '将特定景点添加到我的行程中。', fullText: '将（景点名称）添加到计划中' },
        { title: '寻找餐厅', description: '帮我找个吃饭的地方。', fullText: '告诉我可以在哪里吃饭' },
      ];
    default: // 'ja'
      return [
        { title: 'おすすめを教えて', description: '周辺の面白い場所を教えて。', fullText: 'おすすめのスポットを教えて' },
        { title: 'プランに追加', description: '特定のスポットを計画に加えて。', fullText: '（スポット名）をプランに追加して' },
        { title: '食事場所を探す', description: '近くで食事ができる場所は？', fullText: '食事ができるところを教えて' },
      ];
  }
});

function applySuggestion(fullText) {
  chatInputText.value = fullText;
}
// -------------------------------------

async function handleSendMessage(message = chatInputText.value) {
  if (!message.trim()) return;
  await chatStore.sendMessage(message);
  // The input will be cleared by the child component via v-model update
}

function handlePromptOption(option) {
  void chatStore.selectPromptOption(option)
}

function handlePromptAnswer(answer) {
  void chatStore.sendAnswer(answer)
}

function handlePromptDismiss() {
  isPromptDismissed.value = true
}

function handleUndo(messageId) {
  void chatStore.undoItinerary(messageId)
}

// Function to scroll to the bottom of the messages container
const scrollToBottom = () => {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight;
    }
  });
};

// Watch for changes in the number of messages and scroll to bottom
watch(() => messages.value.length, () => {
  scrollToBottom();
});

watch(() => messages.value.at(-1)?.content, () => {
  scrollToBottom();
});

watch(currentPrompt, () => {
  isPromptDismissed.value = false;
});

watch(isSessionLoaded, (isLoaded) => {
  if (isLoaded && messages.value.length === 0) {
    console.log('[ChatView] Session loaded and chat is empty. Adding welcome message.');
    const lang = userStore.user?.language || 'ja';
    let welcomeMessage = '';

    switch (lang) {
      case 'en':
        welcomeMessage = `Hello, ${userStore.userName}. This app helps you create sightseeing plans for Mt. Chokai and find information about nearby spots through a conversation with an AI. To get started, try asking, "Show me some recommended spots"!`;
        break;
      case 'zh':
        welcomeMessage = `你好, ${userStore.userName}。通过与AI对话，您可以使用此应用程序创建鸟海山的观光计划，并查找附近景点的信息。首先，让我们试着问“请告诉我推荐的景点”！`;
        break;
      default: // 'ja'
        welcomeMessage = `こんにちは、${userStore.userName}さん。このアプリは、AIとの対話を通じて鳥海山の観光プランを作成したり、周辺のスポット情報を調べたりすることができます。まずは「おすすめのスポットを教えて」と聞いてみましょう！`;
        break;
    }

    messages.value.push({
      id: 'initial-welcome',
      content: welcomeMessage,
      sender: 'ai',
      timestamp: new Date(),
    });
  }
});

// When the component is first mounted
onMounted(() => {
  scrollToBottom(); // Also scroll to bottom on initial load
});

</script>

<template>
  <!-- The NavWindow component is preserved here, outside the new chat UI div -->
  <NavWindow />

  <div class="tw-relative tw-w-full tw-h-full tw-flex tw-flex-col bg-noise">
    <!-- Message List Area -->
    <div class="tw-flex-1 tw-overflow-y-auto tw-pb-28 tw-overscroll-y-contain tw-touch-action-pan-y" ref="messagesContainer">
      <OC_ChatMessages
        :messages="messages"
        :is-undoing="isUndoing"
        @undo="handleUndo"
      />
    </div>

    <!-- Floating Suggestion Cards -->
    <div
      v-if="!isAskUserFormVisible"
      class="tw-absolute tw-bottom-[100px] sm:tw-bottom-[88px] tw-left-0 tw-w-full tw-z-10 tw-transition-all tw-touch-action-none"
    >
      <div class="no-scrollbar tw-flex tw-gap-3 tw-overflow-x-auto tw-px-4">
        <div
          v-for="suggestion in suggestionTemplates"
          :key="suggestion.title"
          @click="applySuggestion(suggestion.fullText)"
          role="button"
          tabindex="0"
          class="tw-flex-shrink-0 tw-w-48 tw-p-3 tw-border tw-border-gray-300/80 tw-rounded-xl tw-bg-white/80 tw-backdrop-blur-md tw-shadow-sm hover:tw-shadow-lg hover:tw-border-gray-400/90 hover:-tw-translate-y-0.5 tw-transition-all tw-cursor-pointer focus:tw-outline-none focus:tw-ring-2 focus:tw-ring-blue-500"
        >
          <p class="tw-font-semibold tw-text-sm tw-text-gray-800">{{ suggestion.title }}</p>
          <p class="tw-text-xs tw-text-gray-600 tw-mt-1">{{ suggestion.description }}</p>
        </div>
      </div>
    </div>

    <!-- Input Area -->
    <div class="tw-shrink-0 tw-touch-action-none">
      <OC_AskUserForm
        v-if="isAskUserFormVisible"
        :prompt="currentPrompt"
        :is-sending="isAnswering"
        @select-option="handlePromptOption"
        @answer="handlePromptAnswer"
        @dismiss="handlePromptDismiss"
      />
      <OC_ChatInput
        v-model="chatInputText"
        @sendMessage="handleSendMessage"
        @stop="chatStore.stopStreaming"
        :is-sending="isChatInputBlocked"
        :placeholder="placeholderText"
      />
    </div>
  </div>
</template>

<style>
/* Utility to hide the scrollbar */
.no-scrollbar::-webkit-scrollbar {
    display: none;
}
.no-scrollbar {
    -ms-overflow-style: none;  /* IE and Edge */
    scrollbar-width: none;  /* Firefox */
}

.bg-noise {
  background-color: #1f2937; /* gray-800 */
}
</style>
