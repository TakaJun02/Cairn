<template>
  <aside class="flex h-full w-full flex-col bg-ink-surface">
    <!-- 上段: 銘 -->
    <div class="flex items-center gap-3 border-b border-edge p-4">
      <!-- N-1: アイコンの背後に色を敷かない。中立な暗い面(--color-raised)に
           置き、細い --color-edge の枠で浮かせる(§2.1)。 -->
      <img src="/app-icon.png" alt="" class="h-11 w-11 shrink-0 rounded-ui border border-edge bg-ink-raised object-contain p-1 shadow-soft" />
      <div class="min-w-0">
        <p class="truncate font-display text-base font-semibold tracking-[-0.025em] text-text">
          Chokai Guide
        </p>
        <p class="mt-0.5 text-[11px] text-text-dim">鳥海山エリア観光ガイド</p>
      </div>
      <button
        type="button"
        class="ml-auto flex h-11 w-11 shrink-0 items-center justify-center rounded-ui-sm text-text-dim transition-colors duration-fast hover:bg-fill-hover hover:text-text lg:hidden"
        aria-label="サイドバーを閉じる"
        @click="$emit('close')"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M15 19l-7-7 7-7" />
        </svg>
      </button>
    </div>

    <!-- 中段: 使い方 -->
    <div class="flex-1 overflow-y-auto px-3.5 py-[18px]">
      <p class="px-1.5 pb-2 text-xs font-medium text-text-dim">使い方</p>
      <p class="px-1.5 pb-[14px] text-[12.5px] leading-relaxed text-text-muted">
        {{ usageGuide.description }}
      </p>

      <p class="px-1.5 pb-2 text-xs font-medium text-text-dim">{{ usageGuide.prompt_intro }}</p>
      <button
        v-for="example in examples"
        :key="example"
        type="button"
        class="mb-2 block w-full min-h-11 rounded-ui-sm border border-edge bg-fill-hover px-3 py-2.5 text-left text-[12.5px] text-text transition-all duration-base ease-standard hover:-translate-y-px hover:border-edge-strong hover:bg-fill-active"
        @click="applyExample(example)"
      >
        {{ example }}
      </button>

      <p class="mt-[14px] px-1.5 text-[12.5px] leading-relaxed text-text-muted">
        {{ usageGuide.map_info_1 }}<span class="font-medium text-brand-soft">{{ usageGuide.map_info_highlight }}</span>{{ usageGuide.map_info_2 }}
      </p>
    </div>

    <!-- 下段: ユーザー -->
    <div class="border-t border-edge p-3">
      <div class="flex items-center gap-3 p-1.5">
        <div class="relative h-9 w-9 shrink-0 rounded-full border border-edge-strong bg-ink-high">
          <span class="flex h-full w-full items-center justify-center font-display text-sm font-semibold text-text">
            {{ userInitial }}
          </span>
          <span class="absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border-2 border-ink-surface bg-brand-signal"></span>
        </div>
        <span class="min-w-0 flex-1 truncate text-[13.5px] font-medium text-text">{{ userStore.userName }}</span>
        <button
          type="button"
          class="flex h-11 w-11 shrink-0 items-center justify-center rounded-ui-sm text-text-dim transition-colors duration-fast hover:bg-fill-hover hover:text-text"
          aria-label="ログアウト"
          @click="handleLogout"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 8l-4 4 4 4M6 12h10" />
          </svg>
        </button>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { computed, inject, ref } from 'vue';
import { useUserStore } from '@/stores/user';
import { useChatStore } from '@/stores/chat';
import { useRouter } from 'vue-router';

const emit = defineEmits(['close']);
const userStore = useUserStore();
const chatStore = useChatStore();
const router = useRouter();

// AppShell(共通祖先)が持つ、コンポーザへの下書き中継(§5: 例文は押すと入力欄に入る)。
const composerDraft = inject('composerDraft', ref(''));

const usageGuide = computed(() => {
  const lang = userStore.user?.language || 'ja';
  switch (lang) {
    case 'en':
      return {
        title: "How to Use",
        description: "Plan your tour around Mt. Chokai by talking with our AI agent that recommends spots.",
        prompt_intro: "Try asking things like:",
        prompt_1: "Show recommended spots",
        prompt_2: "Add (spot name) to the plan",
        map_info_1: "The plan built up through the conversation is reflected on the ",
        map_info_highlight: "Guidance Map",
        map_info_2: " at the top."
      };
    case 'zh':
      return {
        title: "使用方法",
        description: "与推荐鸟海山景点的人工智能代理交谈，制定您的景点游览计划。",
        prompt_intro: "请试着像这样提问：",
        prompt_1: "推荐一些景点",
        prompt_2: "将（景点名称）添加到计划中",
        map_info_1: "对话中逐步确定的计划会反映在上方的",
        map_info_highlight: "导航地图",
        map_info_2: "上。"
      };
    default: // 'ja'
      return {
        title: "使い方",
        description: "鳥海山のスポットをオススメするAIエージェントと会話しながら、スポットの周遊計画を立てましょう。",
        prompt_intro: "次のように話しかけてみてください。",
        prompt_1: "おすすめのスポットを教えて",
        prompt_2: "〇〇をプランに追加して",
        map_info_1: "会話ごとに決まっていく計画は、上部の",
        map_info_highlight: "ガイダンスマップ",
        map_info_2: "に反映されます。"
      };
  }
});

const examples = computed(() => [usageGuide.value.prompt_1, usageGuide.value.prompt_2]);

function applyExample(text) {
  composerDraft.value = text;
  emit('close');
}

const userInitial = computed(() => {
  return userStore.userName ? userStore.userName.charAt(0).toUpperCase() : '?';
});

const handleLogout = () => {
  emit('close');
  userStore.logout();
  chatStore.clearChat();
  router.push('/login');
};
</script>
