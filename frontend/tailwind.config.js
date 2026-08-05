// tailwind.config.js
// デザインシステム「Chokai Signal」のトークン写像
// (Docs/30_design/frontend_design_system.md §3.4)

// 不透明色は必ずこのヘルパを通して写す(§3.0/§3.4)。`rgb(var(--color-x-rgb) /
// <alpha-value>)` の形にしないと、Tailwind は `bg-ink-base/[0.88]` のような
// token/alpha ユーティリティを無言で生成しない(2026-08-05 実測)。
const c = (name) => `rgb(var(--color-${name}-rgb) / <alpha-value>)`

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '"Noto Sans JP"',
          '"Hiragino Sans"',
          '"Yu Gothic UI"',
          'system-ui',
          'sans-serif',
        ],
        display: [
          '"Space Grotesk"',
          '"Noto Sans JP"',
          '"Hiragino Sans"',
          '"Yu Gothic UI"',
          'system-ui',
          'sans-serif',
        ],
      },
      colors: {
        // 不透明色は c() を通す(§3.0/§3.4)。token/alpha ユーティリティ
        // (`bg-ink-base/[0.88]` 等)を生成させるために必須。
        ink: {
          base: c('canvas'),
          surface: c('panel'),
          raised: c('raised'),
          high: c('high'),
          paper: c('paper'),
        },
        edge: {
          // それ自体が rgba() で半透明。透明度修飾子を付けないので従来どおり。
          DEFAULT: 'var(--color-edge)',
          strong: 'var(--color-edge-strong)',
        },
        fill: {
          hover: 'var(--color-fill-hover)',
          active: 'var(--color-fill-active)',
        },
        brand: {
          signal: c('signal'),
          soft: c('signal-soft'),
        },
        // 本文・補足・ラベルの文字色(§3.1)。`text-text` / `text-text-muted` /
        // `text-text-dim` として使う。
        text: {
          DEFAULT: c('text'),
          muted: c('text-muted'),
          dim: c('text-dim'),
        },
        // 反転面(送信ボタン)の上の文字。`text-paper-ink` として使う。
        paper: {
          ink: c('paper-ink'),
        },
        danger: c('danger'),
        warn: c('warn'),
      },
      borderRadius: {
        'ui-sm': 'var(--radius-sm)',
        ui: 'var(--radius-md)',
        'ui-lg': 'var(--radius-lg)',
        sheet: 'var(--radius-sheet)',
      },
      boxShadow: {
        hairline: 'var(--shadow-hairline)',
        soft: 'var(--shadow-raised)',
        glass: 'var(--shadow-overlay)',
      },
      transitionDuration: {
        fast: 'var(--motion-fast)',
        base: 'var(--motion-base)',
        slow: 'var(--motion-slow)',
      },
      transitionTimingFunction: {
        standard: 'var(--ease-standard)',
        expressive: 'var(--ease-expressive)',
      },
    },
  },
  plugins: [],
}
