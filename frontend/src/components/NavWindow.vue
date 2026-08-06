<!-- frontend/src/components/NavWindow.vue -->
<script setup>
import { inject, provide, ref } from 'vue'
import NavView from '@/views/NavView.vue' // NavViewは常に表示されるので直接インポート
import { useNavWindow } from '@/lib/useNavWindow'

// ガイダンスマップの開閉状態は AppShell.vue が 1 度だけ生成し、ここへ
// provide で渡す(ヘッダーのトグルピルと実ウィンドウで同じ state を共有する
// ため。§8.4 / AppShell.vue のコメント参照)。`lib/useNavWindow.js` 自体は
// 変更していない。
//
// L-1 是正: `/plan`(legacy ルート。PlanView.vue が NavWindow を単体で
// マウントする)は AppShell の外(router の兄弟ルート)にあり、provide が
// 届かない。inject の既定値を空オブジェクトのままにすると `hasRoute` が
// undefined になり、ガイダンスマップが常に消える。provide が無いときは
// このコンポーネント自身で `useNavWindow()` を呼んでフォールバックする
// (座標計算ロジック自体は変更していない)。
const NAV_WINDOW_NOT_PROVIDED = Symbol('nav-window-not-provided')
const injectedNavWindow = inject('navWindow', NAV_WINDOW_NOT_PROVIDED)
const navWindow = injectedNavWindow === NAV_WINDOW_NOT_PROVIDED ? useNavWindow() : injectedNavWindow
const {
  hasRoute,
  isNavWindowVisible,
  isNavWindowFullScreen,
  navWindowStyle,
  toggleNavWindow,
  openNavFullScreen,
  startDrag,
} = navWindow

// 「⋯」メニュー(§8.3: 端末取り込み・オフライン資材・ライブ同期・LoRa を畳む)
// の開閉状態。中身は NavView.vue が持つ(state がそちらにあるため)ので、
// provide/inject で共有する。
const isControlsMenuOpen = ref(false)
provide('navControlsMenuOpen', isControlsMenuOpen)

// frontend_design_system.md §8.3.1-7: ⋯ ボタンの状態ドット(ライブ同期が
// 接続 / パック取得中 / LoRa 接続 のいずれかが真のとき)。判定は
// NavView.vue が持つ状態から行うため、同じ provide/inject の型で共有する
// (この ref は NavView.vue 側が書き込む)。
const isMapStatusActive = ref(false)
provide('navMapStatusActive', isMapStatusActive)

function handleClose() {
  isControlsMenuOpen.value = false
  toggleNavWindow?.()
}

function handleMenuToggle() {
  isControlsMenuOpen.value = !isControlsMenuOpen.value
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="hasRoute"
      :class="[
        'nav-window',
        'rounded-ui-lg border border-edge-strong bg-ink-surface shadow-glass backdrop-blur-xl overflow-hidden flex flex-col',
        {
          'nav-window--fullscreen rounded-none border-0 shadow-none': isNavWindowFullScreen,
          'nav-window--visible': isNavWindowVisible && !isNavWindowFullScreen,
          'nav-window--hidden': !isNavWindowVisible && !isNavWindowFullScreen
        }
      ]"
      role="dialog"
      :aria-modal="isNavWindowFullScreen ? 'true' : 'false'"
      :aria-hidden="(!isNavWindowVisible).toString()"
      :style="navWindowStyle"
    >
      <header
        :class="['flex h-12 shrink-0 items-center gap-2.5 border-b border-edge bg-ink-raised px-4', { 'cursor-default': isNavWindowFullScreen }]"
      >
        <!-- ドラッグの把手は把手 + 銘の範囲だけに限る(ボタン群は含めない)。
             ヘッダー全体に pointerdown を付けると、`startDrag` の
             `setPointerCapture` がその後の click をヘッダーへ奪ってしまい、
             内側のボタンが一切反応しなくなる(実機確認 2026-08-05)。
             `useNavWindow.startDrag` 自体は変更していない。 -->
        <div
          :class="['flex flex-1 cursor-grab items-center gap-2.5 active:cursor-grabbing', { 'cursor-default': isNavWindowFullScreen }]"
          style="touch-action: none"
          @pointerdown="startDrag"
        >
          <span class="grip-handle text-text-dim" aria-hidden="true">
            <i></i><i></i><i></i>
          </span>
          <span class="font-display text-sm font-semibold tracking-[-0.02em] text-text">ガイダンスマップ</span>
        </div>
        <div class="ml-auto flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            class="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-ui-sm text-text-dim transition-colors duration-fast hover:bg-fill-hover hover:text-text"
            :aria-expanded="isControlsMenuOpen"
            aria-label="メニュー"
            @click.stop="handleMenuToggle"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="19" cy="12" r="1.7" /></svg>
            <span v-if="isMapStatusActive" class="menu-trigger-dot" aria-hidden="true"></span>
          </button>
          <button
            type="button"
            class="flex h-11 w-11 shrink-0 items-center justify-center rounded-ui-sm text-text-dim transition-colors duration-fast hover:bg-fill-hover hover:text-text"
            aria-label="全画面"
            @click.stop="openNavFullScreen"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4H4v5M15 4h5v5M15 20h5v-5M9 20H4v-5" /></svg>
          </button>
          <button
            type="button"
            class="flex h-11 w-11 shrink-0 items-center justify-center rounded-ui-sm text-text-dim transition-colors duration-fast hover:bg-fill-hover hover:text-text"
            aria-label="閉じる"
            @click.stop="handleClose"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
      </header>
      <div class="nav-window__body min-h-0 flex-1">
        <NavView />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.grip-handle {
  display: flex;
  flex-direction: column;
  gap: 2.5px;
}
.grip-handle i {
  display: block;
  width: 11px;
  height: 1.5px;
  border-radius: 2px;
  background: currentColor;
}

/* frontend_design_system.md §8.3.1-7: ⋯ ボタンの状態ドット。中身は開かな
   くても「何か動いている」ことだけ分かればよい(無限アニメーションは
   持たせない。P6)。 */
.menu-trigger-dot {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 7px;
  height: 7px;
  border-radius: 9999px;
  background: var(--color-signal);
  box-shadow: 0 0 0 2px var(--color-raised);
}

.nav-window {
  position: fixed;
  z-index: 1200;
  transition:
    transform 0.32s ease,
    opacity 0.24s ease,
    width 0.38s cubic-bezier(0.22, 1, 0.36, 1),
    height 0.38s cubic-bezier(0.22, 1, 0.36, 1),
    top 0.38s cubic-bezier(0.22, 1, 0.36, 1),
    left 0.38s cubic-bezier(0.22, 1, 0.36, 1);
  will-change: width, height, top, left, transform;
}

.nav-window--visible {
  pointer-events: auto;
}

.nav-window--hidden {
  pointer-events: none;
}

.nav-window__body {
  background: var(--color-canvas);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.nav-window__body :deep(.nav-view) {
  height: 100% !important;
  width: 100%;
}

.nav-window__body :deep(.nav-container),
.nav-window__body :deep(.map-wrapper) {
  height: 100%;
}

.nav-window__body :deep(.toast-stack) {
  bottom: 16px;
  right: 16px;
}
</style>
