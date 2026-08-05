<script setup>
import { ref, watch, nextTick, onMounted, onBeforeUnmount, computed, inject } from 'vue'
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
const { messages, isLoading, isUndoing, currentPrompt, isAnswering } = storeToRefs(chatStore)

const scrollEl = ref(null);

const chatInputText = ref('');
const isPromptDismissed = ref(false);
const isAtBottom = ref(true);

// AppShell(共通祖先)から中継される、サイドバーの例文カードの下書き
// (§5: 押すと入力欄に入る)。
const composerDraft = inject('composerDraft', ref(''));
watch(composerDraft, (text) => {
  if (!text) return;
  chatInputText.value = text;
  composerDraft.value = '';
});

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

// --- 空状態の見出し・提案ピル(§7.1) ---
const emptyHeadingLines = computed(() => {
  const lang = userStore.user?.language || 'ja';
  const name = userStore.userName;
  switch (lang) {
    case 'en':
      return { name: `Hi ${name},`, rest: 'where in Mt. Chokai shall we go?' };
    case 'zh':
      return { name: `${name}，`, rest: '鸟海山，我们去哪里？' };
    default: // 'ja'
      return { name: `${name}さん、`, rest: '鳥海山のどこへ行きましょうか' };
  }
});

const suggestionTemplates = computed(() => {
  const lang = userStore.user?.language || 'ja';
  switch (lang) {
    case 'en':
      return [
        'Show recommended spots',
        'I want to visit waterfalls and springs',
        'Plan a half-day trip',
      ];
    case 'zh':
      return [
        '推荐一些景点',
        '想去瀑布和涌泉',
        '制定半天的行程',
      ];
    default: // 'ja'
      return [
        'おすすめのスポットを教えて',
        '滝や湧水を巡りたい',
        '半日で回れるプランを作って',
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

// 会話が下端付近にあるときだけ自動追従する。ユーザーが読み返すために上へ
// スクロールしている間は追従しない(§7.2 最新へ戻るボタン)。
const AT_BOTTOM_THRESHOLD = 64;

function updateIsAtBottom() {
  const el = scrollEl.value;
  if (!el) return;
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
  isAtBottom.value = distance < AT_BOTTOM_THRESHOLD;
}

function scrollToBottom(smooth = false) {
  nextTick(() => {
    const el = scrollEl.value;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    isAtBottom.value = true;
  });
}

function handleScrollToLatestClick() {
  scrollToBottom(true);
}

watch(() => messages.value.length, () => {
  if (isAtBottom.value) scrollToBottom();
});

watch(() => messages.value.at(-1)?.content, () => {
  if (isAtBottom.value) scrollToBottom();
});

watch(currentPrompt, (prompt) => {
  isPromptDismissed.value = false;
  if (prompt) scrollToBottom();
});

onMounted(() => {
  scrollToBottom();
  scrollEl.value?.addEventListener('scroll', updateIsAtBottom, { passive: true });
});

onBeforeUnmount(() => {
  scrollEl.value?.removeEventListener('scroll', updateIsAtBottom);
});

</script>

<template>
  <!-- The NavWindow component is preserved here, outside the new chat UI div -->
  <NavWindow />

  <div class="relative flex h-full w-full flex-col overflow-hidden">
    <div class="ambient-glow" :class="{ 'is-receded': messages.length > 0 }"></div>

    <!-- 会話領域(スクロールするのはここだけ) -->
    <div ref="scrollEl" class="relative z-[1] flex-1 overflow-y-auto">
      <!-- 空状態(§7.1): 提案はここにしか出ない。会話に重ならない。
           M-1 是正: コンポーザが absolute のため、空状態にもその高さぶんの
           下端余白が要る(§7.3)。min-h-full + pb で確保し、収まらない
           場合は親(scrollEl)がスクロールできるようにする。 -->
      <div v-if="messages.length === 0" class="mx-auto flex min-h-full max-w-3xl flex-col justify-center px-5 pb-[150px]">
        <div class="empty-item-enter">
          <!-- N-1: アイコンの背後に色を敷かない。中立な暗い面(--color-raised)
               に置き、細い --color-edge の枠で浮かせる(§2.1)。 -->
          <div class="flex h-14 w-14 items-center justify-center rounded-ui border border-edge bg-ink-raised shadow-soft">
            <img src="/app-icon.png" alt="" class="h-8 w-8 rounded-full">
          </div>
        </div>
        <!-- N-2: 名前の行と定型文の行を別ブロックにして text-wrap: balance を
             それぞれ独立に効かせる。1 つの balance を両行にまたがせると、
             名前が長いときに定型文側で 1 文字だけの孤立行が生まれる
             (例: 「行きましょう / か」)。 -->
        <h2 class="empty-item-enter mt-6 text-[clamp(2rem,5vw,3.6rem)] font-semibold leading-[1.12] tracking-[-0.05em] text-text/90">
          <span class="aurora-copy" style="display: block; text-wrap: balance">{{ emptyHeadingLines.name }}</span>
          <span style="display: block; text-wrap: balance">{{ emptyHeadingLines.rest }}</span>
        </h2>
        <div class="empty-item-enter mt-7 flex flex-wrap gap-2.5">
          <button
            v-for="suggestion in suggestionTemplates"
            :key="suggestion"
            type="button"
            class="group inline-flex min-h-11 max-w-full items-center gap-3 rounded-full border border-edge-strong bg-ink-surface/65 px-4 py-2 text-[13.5px] text-text/80 shadow-hairline transition-all duration-base ease-expressive hover:-translate-y-0.5 hover:bg-ink-raised hover:text-text"
            @click="applySuggestion(suggestion)"
          >
            {{ suggestion }}
            <svg class="h-4 w-4 shrink-0 text-brand-soft opacity-60 transition-all duration-fast group-hover:translate-x-[-2px] group-hover:translate-y-[2px] group-hover:opacity-100" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M20 5v6a5 5 0 0 1-5 5H5M9 12l-4 4 4 4" />
            </svg>
          </button>
        </div>
      </div>

      <template v-else>
        <OC_ChatMessages
          :messages="messages"
          :is-undoing="isUndoing"
          @undo="handleUndo"
        />
        <!-- §7.4: 質問は会話の流れの中のカード。コンポーザの上に貼り付く帯ではない。 -->
        <div v-if="isAskUserFormVisible" class="mx-auto max-w-3xl px-4 pb-8">
          <OC_AskUserForm
            :prompt="currentPrompt"
            :is-sending="isAnswering"
            @select-option="handlePromptOption"
            @answer="handlePromptAnswer"
            @dismiss="handlePromptDismiss"
          />
        </div>
        <div class="h-[150px]" aria-hidden="true"></div>
      </template>
    </div>

    <!-- コンポーザ(§7.3) -->
    <div class="composer-dock pointer-events-none absolute inset-x-0 bottom-0 z-[6] pb-4 pt-11" style="background: linear-gradient(to bottom, transparent, rgba(var(--color-canvas-rgb), .92) 34%, var(--color-canvas) 68%)">
      <div class="relative mx-auto max-w-3xl px-4">
        <Transition name="pop-fade">
          <button
            v-if="!isAtBottom && messages.length > 0"
            type="button"
            class="pointer-events-auto absolute left-1/2 top-[-3.5rem] flex h-11 w-11 -translate-x-1/2 items-center justify-center rounded-full border border-edge-strong bg-ink-raised/70 text-text shadow-glass backdrop-blur-md transition-transform duration-base ease-expressive hover:-translate-y-0.5"
            aria-label="最新のメッセージへ戻る"
            @click="handleScrollToLatestClick"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 5v14M5 12l7 7 7-7" />
            </svg>
          </button>
        </Transition>
        <div class="pointer-events-auto">
          <OC_AskUserForm
            v-if="isAskUserFormVisible && messages.length === 0"
            :prompt="currentPrompt"
            :is-sending="isAnswering"
            class="mb-2"
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
    </div>
  </div>
</template>

<style scoped>
.aurora-copy {
  display: inline-block;
  background: var(--aurora-copy);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.pop-fade-enter-active,
.pop-fade-leave-active {
  transition: opacity var(--motion-base) var(--ease-standard), transform var(--motion-base) var(--ease-expressive);
}
.pop-fade-enter-from,
.pop-fade-leave-to {
  opacity: 0;
  transform: translate(-50%, 4px);
}
</style>
