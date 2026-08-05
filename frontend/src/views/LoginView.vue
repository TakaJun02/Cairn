<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'

const userName = ref('')
const language = ref('ja')
const isLoading = ref(false)
const errorMessage = ref('')
const router = useRouter()
const userStore = useUserStore()

function getErrorMessage(error) {
  if (!error || !error.status) return '不明なエラーが発生しました。'
  switch (error.status) {
    case 400: return '不正なリクエストです。入力内容を確認してください。';
    default: return `エラーが発生しました (コード: ${error.status})。`;
  }
}

// バックエンドの POST /login はユーザー名のみで、未登録なら作成・登録済みなら
// そのまま通す(登録/ログインの 2 モードを画面に持つ必然性がない。§10)。
async function handleSubmit() {
  if (!userName.value.trim()) {
    errorMessage.value = 'ユーザー名を入力してください。';
    return;
  }
  isLoading.value = true;
  errorMessage.value = '';
  const result = await userStore.login(userName.value.trim());
  isLoading.value = false;
  if (result.success) {
    // 言語選択はサーバーに送る項目ではなく(現行 API に該当フィールドがない)、
    // このセッションの表示言語としてクライアント側で持つ(旧 register フロー
    // と同じ扱い)。
    if (userStore.user) userStore.user.language = language.value;
    router.push('/app/chat');
  } else {
    errorMessage.value = getErrorMessage(result.error);
  }
}
</script>

<template>
  <div class="relative h-full w-full overflow-y-auto bg-ink-base">
    <!--
      層1: 鳥海山の写真(質感)。§10.1 / 入場は §10.4 行0。
      写真自体(.login-bg-photo)だけが scale アニメーションを持つので、
      overflow-hidden の外枠でそのはみ出しを吸収する
      (外枠自身は動かないので、スクロール親を汚さない)。
    -->
    <div class="absolute inset-0 z-0 overflow-hidden">
      <div
        class="login-bg-photo h-full w-full bg-cover bg-center"
        style="background-image: url('/chokai.jpg')"
      ></div>
    </div>
    <!--
      層2: 縦の暗幕(§10.2.1)。
      上端が最も濃く(90)、中盤で少し緩め(60)、下端は再び締める(75)。
      to を transparent で終わらせず floor を残すことで、画面が高いほど
      暗幕が早く尽きて中央のパネルが写真の明るい部分に浮く問題(1440×900 で
      発生)を防ぐ。色は純黒ではなく --color-canvas 基準にして地の色と馴染ませる。
    -->
    <div class="absolute inset-0 z-0 bg-gradient-to-b from-ink-base/90 via-ink-base/60 to-ink-base/75"></div>
    <!-- 層3: 周辺減光(§10.2.1、任意)。パネルの背後を中心にごく薄く鎮める。 -->
    <div class="absolute inset-0 z-0 login-vignette"></div>

    <!--
      タイトルとパネルは縦に積んで中央寄せする(§10.2-4)。
      旧実装の absolute + mt-32 の力技(780×493 で重なっていた)をやめ、
      通常のフローで積むことで重なりが原理的に起きない組み方にする。
      画面が低いときは外側の overflow-y-auto でスクロールして到達できる。
    -->
    <div
      class="relative z-10 flex min-h-full w-full flex-col items-center justify-center gap-10 px-6 py-14 pt-[calc(3.5rem+env(safe-area-inset-top))] pb-[calc(3rem+env(safe-area-inset-bottom))] sm:gap-12"
    >
      <!-- タイトル(§10.4 行1〜3) -->
      <div class="w-full max-w-md text-center">
        <h1
          class="enter enter--lift font-display text-4xl font-bold tracking-[-0.02em] text-text sm:text-5xl"
          style="
            text-shadow: 0 4px 28px rgb(var(--color-canvas-rgb) / 0.7);
            --enter-delay: 100ms;
            --enter-dur: var(--enter-slow);
          "
        >
          Chokai Guide
        </h1>
        <!-- オーロラの罫(§10.5-3)。名前のないログイン時点で「ブランドの気配」を担う。 -->
        <div
          class="login-aurora-rule mx-auto mt-4"
          style="--enter-delay: 190ms; --enter-dur: var(--enter-base)"
        ></div>
        <p
          class="enter enter--nudge mt-3 font-display text-lg font-light tracking-[0.02em] text-text-muted sm:text-xl"
          style="--enter-delay: 250ms; --enter-dur: var(--enter-base)"
        >
          鳥海山エリア観光ガイド
        </p>
      </div>

      <!-- グラス調パネル(§10.1 / §10.2-1 / §10.4 行4) -->
      <div
        class="enter enter--rise w-full max-w-md"
        style="--enter-delay: 340ms; --enter-dur: var(--enter-slow)"
      >
        <div class="login-panel rounded-ui-lg border border-edge-strong bg-ink-surface/90 p-8 backdrop-blur-xl md:p-10">
          <div class="text-center">
            <h2
              class="enter enter--nudge font-display text-2xl font-semibold text-text"
              style="--enter-delay: 520ms; --enter-dur: var(--enter-quick)"
            >
              おかえりなさい
            </h2>
            <p
              class="enter enter--nudge mt-1.5 text-sm text-text-muted"
              style="--enter-delay: 580ms; --enter-dur: var(--enter-quick)"
            >
              ユーザー名を入力してください
            </p>
          </div>

          <form @submit.prevent="handleSubmit" class="mt-7 space-y-4">
            <input
              id="username"
              v-model="userName"
              type="text"
              placeholder="ユーザー名"
              required
              :disabled="isLoading"
              class="enter enter--nudge min-h-11 w-full rounded-ui-sm border border-edge bg-ink-base/70 px-4 py-3 text-text placeholder:text-text-dim transition-colors focus:border-brand-signal focus:outline-none focus:ring-[3px] focus:ring-brand-signal/40"
              style="--enter-delay: 650ms; --enter-dur: var(--enter-quick)"
            />

            <!-- 言語選択(§10.5-1): <select> は残しつつ、appearance-none + 自前の chevron -->
            <div
              class="enter enter--nudge relative"
              style="--enter-delay: 710ms; --enter-dur: var(--enter-quick)"
            >
              <select
                id="language"
                v-model="language"
                :disabled="isLoading"
                class="min-h-11 w-full appearance-none rounded-ui-sm border border-edge bg-ink-base/70 py-3 pl-4 pr-10 text-text transition-colors focus:border-brand-signal focus:outline-none focus:ring-[3px] focus:ring-brand-signal/40"
              >
                <option value="ja">日本語</option>
                <option value="en">English</option>
                <option value="zh">中文</option>
              </select>
              <svg
                class="pointer-events-none absolute right-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-dim"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <path d="M6 9l6 6 6-6" />
              </svg>
            </div>

            <!-- エラーバナー(§10.5-5): アイコンを添える -->
            <div
              v-if="errorMessage"
              class="flex items-center justify-center gap-2 rounded-ui-sm bg-danger/10 p-3 text-center text-sm text-danger"
            >
              <svg
                class="h-4 w-4 shrink-0"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <circle cx="12" cy="12" r="9" />
                <line x1="12" y1="7.5" x2="12" y2="12.5" />
                <circle cx="12" cy="16" r="0.75" fill="currentColor" stroke="none" />
              </svg>
              <span>{{ errorMessage }}</span>
            </div>

            <button
              type="submit"
              :disabled="isLoading"
              class="enter enter--nudge group min-h-11 w-full rounded-ui-sm bg-ink-paper px-5 py-3 text-base font-semibold text-paper-ink transition-transform duration-base ease-expressive hover:-translate-y-0.5 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60"
              style="--enter-delay: 770ms; --enter-dur: var(--enter-quick)"
            >
              <span v-if="isLoading">処理中...</span>
              <span v-else class="inline-flex items-center justify-center gap-2">
                はじめる
                <svg
                  class="h-4 w-4 transition-transform duration-base ease-expressive group-hover:translate-x-1"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                >
                  <path d="M5 12h14M13 5l7 7-7 7" />
                </svg>
              </span>
            </button>
          </form>
        </div>
      </div>
    </div>
  </div>
</template>
