<script setup>
import { ref, provide, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { RouterView } from 'vue-router'
import OC_Sidebar from './OC_Sidebar.vue'
import { useChatStore } from '@/stores/chat'
import { useUserStore } from '@/stores/user'
import { useNavStore } from '@/stores/nav'
import { useNavWindow } from '@/lib/useNavWindow'

const isSidebarOpen = ref(false)
const isInfoOpen = ref(false)
// M-3 是正: ドロワーの開閉に伴うフォーカス管理(§11)。
// - ドロワーを開いたら、フォーカスをドロワー内へ移す
// - ドロワーを閉じたら、開いたボタン(ハンバーガー)へフォーカスを戻す
const menuButtonRef = ref(null)
const drawerRef = ref(null)

const chatStore = useChatStore()
const userStore = useUserStore()
const navStore = useNavStore()

// ガイダンスマップの開閉状態は、ここ(AppShell = 全 /app/* 画面の共通祖先)で
// 1 度だけ生成し、下の階層(NavWindow.vue)へ provide で共有する。
// `useNavWindow` はコンポーザブル(呼び出しごとに独立した state を作る)なので、
// 呼び出し箇所が 2 つに分かれると header のトグルと実ウィンドウが同期しない。
// 呼び出す関数・座標計算ロジック自体は lib/useNavWindow.js のまま変えていない。
const navWindow = useNavWindow()
provide('navWindow', navWindow)

// サイドバーの例文カード(OC_Sidebar)と会話画面のコンポーザ(ChatView)は
// 兄弟関係にあり(どちらも AppShell の子孫)、状態を持つ store を新設せずに
// 「押すと入力欄に入る」を実現するため、この共通祖先で下書きを中継する。
const composerDraft = ref('')
provide('composerDraft', composerDraft)

// モバイルのアドレスバー伸縮に追従する `--app-height`(100vh を使わない)。
function setAppHeight() {
  document.documentElement.style.setProperty('--app-height', `${window.innerHeight}px`)
}

onMounted(() => {
  setAppHeight()
  window.addEventListener('resize', setAppHeight)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', setAppHeight)
})

// M-3 是正: Escape でドロワーを閉じる。開いたら内部の最初の操作要素へ、
// 閉じたら開いたボタン(ハンバーガー)へフォーカスを移す。
function handleShellKeydown(event) {
  if (event.key === 'Escape' && isSidebarOpen.value) {
    isSidebarOpen.value = false
  }
}
onMounted(() => {
  window.addEventListener('keydown', handleShellKeydown)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleShellKeydown)
})
watch(isSidebarOpen, (open) => {
  if (open) {
    nextTick(() => {
      const focusable = drawerRef.value?.querySelector(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )
      focusable?.focus()
    })
  } else {
    nextTick(() => {
      menuButtonRef.value?.focus()
    })
  }
})

// 旅程が更新されたときだけ、トグルの碧のドットを 1 回だけ脈打たせる(§8.4)。
// :key を変えて DOM を作り直すことで、同じアニメーションを毎回最初から再生する。
const dotPulseKey = ref(0)
watch(
  () => navStore.currentItinerary?.version,
  (version, previous) => {
    if (version == null || previous === undefined) return
    dotPulseKey.value += 1
  }
)

// Rehydrate session on component mount
onMounted(async () => {
  await chatStore.rehydrateSession()
})

function closeSidebar() {
  isSidebarOpen.value = false
}

function toggleInfo() {
  isInfoOpen.value = !isInfoOpen.value
}
</script>

<template>
  <div class="app-shell relative flex h-full w-full overflow-hidden bg-ink-base text-text">
    <!-- サイドバー(lg: 常設) -->
    <aside class="hidden lg:flex lg:w-[17.5rem] lg:shrink-0 lg:border-r lg:border-edge">
      <OC_Sidebar />
    </aside>

    <!-- サイドバー(<lg: ドロワー) -->
    <Transition name="scrim-fade">
      <div
        v-if="isSidebarOpen"
        class="fixed inset-0 z-40 bg-black/70 backdrop-blur-[2px] lg:hidden"
        @click="closeSidebar"
      ></div>
    </Transition>
    <Transition name="sidebar-slide">
      <div
        v-if="isSidebarOpen"
        ref="drawerRef"
        class="fixed inset-y-0 left-0 z-50 w-[85%] max-w-[17.5rem] lg:hidden"
      >
        <OC_Sidebar @close="closeSidebar" />
      </div>
    </Transition>

    <!-- M-3 是正: ドロワーが開いている間は、背景側(このサイドバー以外)を
         inert にする(ドロワー自身に inert を付けるのではない。ドロワーは
         v-if で出し入れするため、そちらに付けても常に false になり無意味)。 -->
    <div class="relative flex h-full min-w-0 flex-1 flex-col" :inert="isSidebarOpen">
      <!-- ヘッダー(sticky) -->
      <header
        class="sticky top-0 z-20 border-b border-edge bg-ink-base/[0.88] pt-[env(safe-area-inset-top)] backdrop-blur-xl"
      >
        <div class="mx-auto flex min-h-[68px] max-w-3xl items-center gap-3 px-4">
          <button
            ref="menuButtonRef"
            type="button"
            class="flex h-11 w-11 shrink-0 items-center justify-center rounded-ui-sm text-text-dim transition-colors duration-fast hover:bg-fill-hover hover:text-text lg:hidden"
            aria-label="サイドバーを開く"
            @click="isSidebarOpen = true"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>

          <h1 class="truncate font-display text-sm font-semibold tracking-[-0.02em] text-text/90">
            Chokai Guide
          </h1>

          <div class="hidden items-baseline gap-1.5 whitespace-nowrap lg:flex">
            <small class="text-[9px] font-medium uppercase tracking-[0.16em] text-text-dim">Powered by</small>
            <b
              class="bg-clip-text text-[10px] font-bold tracking-[0.16em] text-transparent"
              style="background-image: var(--aurora-copy)"
            >Gemma 4</b>
          </div>

          <div class="ml-auto flex items-center gap-2">
            <span class="hidden font-display text-[10px] font-medium uppercase tracking-[0.18em] text-text-dim lg:inline">
              CHOKAI / GUIDANCE
            </span>

            <button
              v-if="navWindow.hasRoute.value"
              type="button"
              class="flex h-11 w-11 items-center justify-center gap-1.5 rounded-full border border-edge-strong bg-fill-hover text-[12.5px] text-text transition-all duration-base ease-expressive hover:-translate-y-px hover:border-brand-signal/45 hover:bg-fill-active lg:w-auto lg:gap-2 lg:px-3.5"
              :aria-expanded="navWindow.isNavWindowVisible.value"
              :aria-label="navWindow.isNavWindowVisible.value ? 'ガイダンスマップを閉じる' : 'ガイダンスマップを開く'"
              @click="navWindow.toggleNavWindow()"
            >
              <span
                :key="dotPulseKey"
                class="h-[7px] w-[7px] shrink-0 rounded-full bg-brand-signal toggle-dot-pulse"
                aria-hidden="true"
              ></span>
              <span class="hidden lg:inline">ガイダンスマップ</span>
              <svg class="hidden shrink-0 text-text-dim lg:inline" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
              <svg class="shrink-0 text-text-dim lg:hidden" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 21s7-6.1 7-11.5A7 7 0 0 0 5 9.5C5 14.9 12 21 12 21z" />
                <circle cx="12" cy="9.5" r="2.4" />
              </svg>
            </button>

            <div class="relative">
              <button
                type="button"
                class="flex h-11 w-11 items-center justify-center rounded-ui-sm text-text-dim transition-colors duration-fast hover:bg-fill-hover hover:text-text"
                aria-label="このアプリについて"
                :aria-expanded="isInfoOpen"
                @click="toggleInfo"
              >
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4">
                  <circle cx="12" cy="12" r="8.25" />
                  <circle cx="12" cy="8.5" r="0.8" fill="currentColor" stroke="none" />
                  <path d="M12 11.75v4.75" stroke-linecap="round" />
                </svg>
              </button>
              <Transition name="scrim-fade">
                <div
                  v-if="isInfoOpen"
                  class="fixed inset-0 z-30"
                  @click="isInfoOpen = false"
                ></div>
              </Transition>
              <Transition name="info-pop">
                <div
                  v-if="isInfoOpen"
                  class="absolute right-0 top-[calc(100%+8px)] z-40 w-72 rounded-ui border border-edge-strong bg-ink-raised p-4 shadow-glass"
                >
                  <p class="font-display text-sm font-semibold text-text">Chokai Guide について</p>
                  <p class="mt-2 text-xs leading-relaxed text-text-muted">
                    鳥海山エリアの観光ガイダンスを AI との対話で組み立てるアプリです。おすすめスポットの提案から周遊プランの作成まで、会話だけで進められます。
                  </p>
                  <p class="mt-3 text-[11px] text-text-dim">生成モデル: Gemma 4</p>
                </div>
              </Transition>
            </div>
          </div>
        </div>
      </header>

      <!-- メインコンテンツ -->
      <main class="relative min-h-0 flex-1 overflow-hidden">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.scrim-fade-enter-active,
.scrim-fade-leave-active {
  transition: opacity var(--motion-base) var(--ease-standard);
}
.scrim-fade-enter-from,
.scrim-fade-leave-to {
  opacity: 0;
}

.sidebar-slide-enter-active,
.sidebar-slide-leave-active {
  transition: transform var(--motion-base) var(--ease-expressive);
}
.sidebar-slide-enter-from,
.sidebar-slide-leave-to {
  transform: translateX(-100%);
}

.info-pop-enter-active,
.info-pop-leave-active {
  transition: transform var(--motion-base) var(--ease-expressive), opacity var(--motion-base) var(--ease-standard);
}
.info-pop-enter-from,
.info-pop-leave-to {
  transform: translateY(-4px);
  opacity: 0;
}
</style>
