# フロントエンド デザインシステム「Chokai Signal」

- 状態: **決定稿 (2026-08-05、ユーザー承認)**
- 完成目標の見た目: [frontend_design_system.mockup.html](frontend_design_system.mockup.html)(本書のトークンで組んだモックアップ。**実装はこれに合わせる**)
- 根拠: [ADR-0023](../adr/0023-frontend-design-system.md)(デザインシステムの導入と表示層の作り直し)
- 参考実装: [TakaJun02/sarutahiko](https://github.com/TakaJun02/sarutahiko)(APU-Navi)—— ユーザー指定。**構造・余白・タイポグラフィ・モーションを踏襲し、配色は色相を差し替える**
- 触ってよい範囲の正: [frontend_nav.md](frontend_nav.md)(本書はその表示層版)
- 解消する既知issue: [23_ux_issues.md](../23_ux_issues.md) §4-1 / §4-2 / §4-3 / §4-4 / §4-5 / §4-6 / §6-1 / §6-2 / §6-3

---

## 1. 何を作るか(1 段落)

**鳥海山エリアの観光ガイダンスを「暗い山の夜」の面の上で行う、単一テーマのダーク UI。**画面は 1 つのキャンバス色の上に成り立ち、面の高さは色の明度差だけで表す(枠線ではなく)。差し色は 1 色 —— 丸池様の碧 —— に限り、それ以外の色は状態(危険・警告)にしか使わない。動きは短く、減速で終わる。

---

## 2. 設計原則

| # | 原則 | 具体的に何を意味するか |
| --- | --- | --- |
| **P1** | **1 画面 1 テーマ** | ダークだけ。ライトの面(旧実装の入力ドック `slate-50`、スターターカード `white/80`)を一掃する。**例外はない**(2026-08-05 改訂。ログイン画面が旧構成のグラス調パネルに戻り、ライトのシートが無くなったため — §10.3) |
| **P2** | **高さは明度で表す** | `canvas → panel → raised → high` の 4 段。枠線は境界の補助であって面の主張ではない。だから `--color-edge` は α 0.09 と極端に薄い |
| **P3** | **差し色は 1 色** | `--color-signal`(碧)。**現在地・選択中・フォーカス・リンク**にだけ使う。青・紫・緑を装飾目的で足さない(現行は blue-800 / blue-500 / slate / amber / indigo が混在) |
| **P4** | **本文は読める行長で止める** | 会話の本文は `max-width: 48rem` (`max-w-3xl`) で中央寄せ。1440px の画面でも 1 行 90 字にしない |
| **P5** | **重ねない** | 情報は積むか畳む。**本文の上にカードを浮かせない**(§4-1 / §4-3 の原因) |
| **P6** | **動きは仕事をするときだけ** | 無限ループのアニメーションは**環境光と待機表示だけ**に許す。操作対象(ボタン)は決して自走しない(§4-4 の原因) |
| **P7** | **内部語を出さない** | `tourist_spot` / `short_walk_ok` のような enum を画面に出さない(§3-2 / §3-4) |

### 2.1 アプリアイコンは差し替えない(2026-08-05 ユーザー判断)

`public/app-icon.png` は **CPSlab の研究室ロゴ**(橙の歯車 + 赤の Y + 緑の文字)で、本デザインシステムの寒色パレットとは色相が衝突する。**それでも差し替えない** —— 帰属を示すロゴであり、ブランドの判断はデザインシステムの都合に優先する。

**したがって、衝突を和らげるのは「置き方」の側の責任である。**

- アイコンの背後に**色を敷かない。**`--color-raised` などの**中立な暗い面**に置く(碧や藍のグラデーションタイルに載せると、ロゴの橙赤緑と真正面から競合する)
- アイコンの周囲に十分な余白を取り、`--color-edge` の細い枠で面から浮かせる
- アイコンの色を差し色として引き継がない。**碧は碧のまま**使う(ロゴに合わせて暖色を足さない —— P3 が崩れる)

---

## 3. デザイントークン

**すべて `frontend/src/assets/design-system.css` の `:root` に定義し、他のファイルは生の色を書かない。**

### 3.0 不透明色は「RGB 成分」で定義する(必読)

**不透明な色は 16 進で書かず、空白区切りの RGB 成分で定義し、そこから `rgb()` 版を導出する。**

```css
:root {
  --color-canvas-rgb: 11 14 16;                       /* ← Tailwind が使う */
  --color-canvas:     rgb(var(--color-canvas-rgb));   /* ← 生 CSS が使う */
}
```

**理由(2026-08-05、実測で判明):** Tailwind v3 で `bg-ink-base/[0.88]` のような**透明度修飾子つきのユーティリティを生成させるには、色定義に `<alpha-value>` プレースホルダが要る。**色を `var(--color-canvas)` という**ただの文字列**として渡すと、Tailwind は `<alpha-value>` を差し込む場所を持てず、**`token/alpha` 形式のクラスをエラーも警告もなく生成しない。**

実測(`tailwindcss` CLI に 6 クラスを食わせた結果):

| 書き方 | `bg-ink-base` | `bg-ink-base/[0.88]` |
| --- | --- | --- |
| `base: 'var(--color-canvas)'` | 生成される | **生成されない(無言)** |
| `base: 'rgb(var(--color-canvas-rgb) / <alpha-value>)'` | 生成される | **生成される** |

**これを間違えると、書いたクラスが黙って消える。**初版の本書(2026-08-05 午前)は前者を規定しており、実装で 18 箇所が無効化された。ログイン画面の副題が**白地に白文字**になって読めなくなった事例がある。

**成分値で定義するのは不透明色だけでよい。**`--color-edge` 系はそれ自体が `rgba()` であり、`border-edge/50` のような書き方をしないので従来どおりでよい。

### 3.1 面と文字

```css
:root {
  color-scheme: dark;

  /* 面(低い→高い)。成分 → rgb() の順で定義する(§3.0) */
  --color-canvas-rgb:    11 14 16;     /* #0b0e10 夜の山肌。アプリの地の色 */
  --color-panel-rgb:     17 21 24;     /* #111518 サイドバー・窓の地 */
  --color-raised-rgb:    24 29 33;     /* #181d21 カード・コンポーザ */
  --color-high-rgb:      34 42 46;     /* #222a2e ユーザー発話の吹き出し */
  --color-paper-rgb:     238 242 244;  /* #eef2f4 反転面(送信ボタン) */
  --color-paper-ink-rgb: 18 23 26;     /* #12171a 反転面の上の文字 */

  /* 文字 */
  --color-text-rgb:       240 243 245; /* #f0f3f5 本文・見出し    17.4:1 */
  --color-text-muted-rgb: 164 173 178; /* #a4adb2 補足   canvas 比  8.5:1 */
  --color-text-dim-rgb:   125 134 139; /* #7d868b ラベル canvas 比  5.2:1 */

  --color-canvas:     rgb(var(--color-canvas-rgb));
  --color-panel:      rgb(var(--color-panel-rgb));
  --color-raised:     rgb(var(--color-raised-rgb));
  --color-high:       rgb(var(--color-high-rgb));
  --color-paper:      rgb(var(--color-paper-rgb));
  --color-paper-ink:  rgb(var(--color-paper-ink-rgb));
  --color-text:       rgb(var(--color-text-rgb));
  --color-text-muted: rgb(var(--color-text-muted-rgb));
  --color-text-dim:   rgb(var(--color-text-dim-rgb));

  /* 境界と面の塗り(それ自体が半透明。成分化しない) */
  --color-edge:        rgba(236, 244, 247, 0.09);
  --color-edge-strong: rgba(236, 244, 247, 0.17);
  --color-fill-hover:  rgba(236, 244, 247, 0.055);
  --color-fill-active: rgba(236, 244, 247, 0.10);
}
```

### 3.2 差し色(鳥海山向けに色相を差し替えた部分)

参考実装は暖色(シグナル `#ff7657`、オーロラ 橙→黄→緑)。**本システムは寒色に振る。**由来は鳥海山の水と雪である。

```css
:root {
  /* シグナル: 丸池様の碧。canvas 比 9.3:1(実測) */
  --color-signal-rgb:      47 201 176;   /* #2fc9b0 */
  --color-signal-soft-rgb: 111 224 205;  /* #6fe0cd */
  --color-signal:      rgb(var(--color-signal-rgb));
  --color-signal-soft: rgb(var(--color-signal-soft-rgb));

  /* オーロラ: 深藍(鳥海湖) → 碧(湧水) → 白銀(残雪) */
  --aurora-copy: linear-gradient(105deg, #4f7df3 5%, #2fc9b0 48%, #cfe6ee 96%);
  --aurora-edge: linear-gradient(100deg,
                   rgba(79, 125, 243, 0.72),
                   rgba(47, 201, 176, 0.58),
                   rgba(207, 230, 238, 0.68));

  /* 環境光(会話面の下部にたまる藍) */
  --ambient-deep: 26, 58, 112;   /* rgb 成分。α は使用側で決める */

  /* 状態色(差し色とは別枠。装飾に使わない) */
  --color-danger-rgb: 242 109 109;  /* #f26d6d  6.6:1 */
  --color-warn-rgb:   232 181 103;  /* #e8b567 10.4:1 */
  --color-danger: rgb(var(--color-danger-rgb));
  --color-warn:   rgb(var(--color-warn-rgb));
}
```

> **`--color-signal` を「成功」の意味で使わない。**碧は本システムのブランド色であって、完了の合図ではない。

### 3.3 角丸・影・モーション・書体

参考実装の値をそのまま採る(よく調整されており、変える理由がない)。

```css
:root {
  --radius-sm:    0.625rem;
  --radius-md:    1rem;
  --radius-lg:    1.5rem;
  --radius-sheet: 2.5rem;

  --shadow-hairline: inset 0 0 0 1px rgba(236, 244, 247, 0.045);
  --shadow-raised:   0 16px 40px -22px rgba(0, 0, 0, 0.88),
                     0 1px 0 rgba(255, 255, 255, 0.025) inset;
  --shadow-overlay:  0 32px 100px -34px rgba(0, 0, 0, 0.94);

  --motion-fast:    140ms;
  --motion-base:    220ms;
  --motion-slow:    480ms;
  --motion-stagger:  60ms;
  --ease-standard:   cubic-bezier(0.2, 0.8, 0.2, 1);
  --ease-expressive: cubic-bezier(0.16, 1, 0.3, 1);
}
```

### 3.3.1 入場の振り付け(2026-08-05 追加・ユーザー指示)

> 画面が現れるとき、**もう少しゆっくり、かつコンポーネント間で差を持たせる。**一律の速さで一斉に出すと安っぽくなる。

**何が問題だったか。**旧ログイン画面はタイトル **0.7s**・パネル **0.8s** と**要素ごとに違う時間**を持ち、遅延 0.2s / 0.4s でずれて入っていた。作り直しで両方 `--motion-slow`(**480ms**)の**一律**にしたため、**速くなり、差も消えた。**

**入場は操作の応答ではない。**ボタンを押した反応(`--motion-fast` 140ms)とは目的が違う。**画面が立ち上がる所作**なので、ゆっくりでよく、要素ごとに違ってよい。専用の段を持たせる。

```css
:root {
  /* 入場専用。操作の応答(--motion-fast/base)とは別系統 */
  --enter-quick: 620ms;   /* 小さい操作子(入力欄・ボタン) */
  --enter-base:  760ms;   /* 標準 */
  --enter-slow:  880ms;   /* 大きい面(パネル・見出し) */
  --enter-scene: 1200ms;  /* 背景・遠景 */
}
```

**3 つの規則。**

| # | 規則 | なぜ |
| --- | --- | --- |
| **M1** | **大きく遠いものほど、長く・早く始まる。**背景 → 見出し → 面 → 面の中身 → 操作子 の順 | 奥から手前へ組み上がって見える |
| **M2** | **動きの種類を変える。**遠景は**縮む**(`scale(1.05)→1`)、見出しは**上がりながら締まる**(`translateY` + `scale`)、面は**上がる**、中身は**わずかに上がる**だけ | 同じ動きの繰り返しは「一斉」に見える。**質の差が階層を伝える** |
| **M3** | **移動距離も階層に比例させる。**面は 1.75rem、中身は 0.5rem | 小さい要素が大きく動くと落ち着かない |

**実装は 1 つのユーティリティに寄せる**(要素ごとに専用クラスを作らない)。

```css
.enter          { opacity: 0;
                  animation: enter-rise var(--enter-dur, var(--enter-base))
                             var(--ease-expressive) var(--enter-delay, 0ms) both; }
.enter--settle  { animation-name: enter-settle; }  /* 遠景: scale(1.05) → 1 */
.enter--lift    { animation-name: enter-lift; }    /* 見出し: 上がりながら scale(0.96) → 1 */
.enter--rise    { animation-name: enter-rise; }    /* 面: translateY(1.75rem) → 0 */
.enter--nudge   { animation-name: enter-nudge; }   /* 中身: translateY(0.5rem) → 0 */
```

呼び出し側は `style="--enter-delay: 300ms; --enter-dur: var(--enter-slow)"` で振り付けだけ書く。

**適用するのは「画面が現れる瞬間」だけ**(ログイン画面・チャットの空状態)。**発話の到着(`message-list`)には適用しない** —— 1 ターンに何度も起きる高頻度のイベントで、遅くすると streaming が重く感じる。ここは現行の `--motion-slow` のままでよい。

**`prefers-reduced-motion: reduce` では全部止まる**(§11 のグローバル規則が `animation-duration` を潰す)。

**書体**

| 用途 | フォント |
| --- | --- |
| 本文(和文含む) | `"Noto Sans JP", "Hiragino Sans", "Yu Gothic UI", system-ui, sans-serif` |
| 見出し・数値・ラベル(`font-display`) | `"Space Grotesk", ` + 上と同じフォールバック |

`Space Grotesk` は **SIL OFL** の可変フォント。`frontend/src/assets/fonts/space-grotesk/space-grotesk-latin-var.woff2` に同梱し、`OFL.txt` も併置する(欧文のみ。和文はフォールバックが受ける)。`font-display: swap` / `font-weight: 300 700`。

### 3.4 Tailwind への写像

`tailwind.config.js` を書き換える。**`prefix: 'tw-'` を廃止する**([ADR-0023](../adr/0023-frontend-design-system.md) §4)。

**不透明色は必ず `rgb(var(--x-rgb) / <alpha-value>)` で写す**(理由と実測は §3.0)。

```js
// 定型: 不透明色はこのヘルパを通す
const c = (name) => `rgb(var(--color-${name}-rgb) / <alpha-value>)`

theme: { extend: {
  fontFamily: { sans: [...], display: ['"Space Grotesk"', ...] },
  colors: {
    ink:   { base:c('canvas'), surface:c('panel'), raised:c('raised'),
             high:c('high'), paper:c('paper') },
    paper: { ink:c('paper-ink') },
    text:  { DEFAULT:c('text'), muted:c('text-muted'), dim:c('text-dim') },
    brand: { signal:c('signal'), soft:c('signal-soft') },
    warn:  c('warn'),
    danger:c('danger'),
    // 半透明のトークンはそのまま渡す(透明度修飾子を付けないため)
    edge:  { DEFAULT:'var(--color-edge)', strong:'var(--color-edge-strong)' },
    fill:  { hover:'var(--color-fill-hover)', active:'var(--color-fill-active)' },
  },
  borderRadius: { 'ui-sm':'var(--radius-sm)', ui:'var(--radius-md)',
                  'ui-lg':'var(--radius-lg)', sheet:'var(--radius-sheet)' },
  boxShadow: { hairline:'var(--shadow-hairline)', soft:'var(--shadow-raised)',
               glass:'var(--shadow-overlay)' },
  transitionDuration:       { fast:'var(--motion-fast)', base:'var(--motion-base)', slow:'var(--motion-slow)' },
  transitionTimingFunction: { standard:'var(--ease-standard)', expressive:'var(--ease-expressive)' },
}}
```

**廃止するもの**: `prefix: 'tw-'`、`colors['gemini-blue']`、`colors['gemini-bg-light']`、`fontFamily.sans = Roboto`、`@tailwindcss/typography`(§9 の `.markdown-body` が置き換える)。

**`src/assets/base.css` は削除する。**Vue スターター既定の light/dark 二重テーマ(`--vt-c-*`)が P1 と衝突し、`a { color: hsla(160,100%,37%,1) }` が全リンクを乗っ取っている。`main.css` の残りも `design-system.css` に統合する。`main.js` の import を差し替える。

---

## 4. 画面の骨格(AppShell)

```
デスクトップ (lg: ≥1024px)                     モバイル (<1024px)
┌──────────┬────────────────────────┐        ┌────────────────────┐
│          │ ヘッダー (sticky)       │        │ ☰  Chokai Guide  ⓘ │
│ サイドバー │────────────────────────│        │────────────────────│
│  17.5rem │                        │        │                    │
│          │   会話 (max-w-3xl 中央) │        │  会話              │
│          │                        │        │                    │
│          │────────────────────────│        │────────────────────│
│ ユーザー  │ コンポーザ (sticky bottom)│        │ コンポーザ          │
└──────────┴────────────────────────┘        └────────────────────┘
                                              サイドバーは左からドロワー
```

- ルート要素に `.app-shell`。`height: var(--app-height, 100%)` でモバイルのアドレスバー伸縮に追従する(`100vh` を使わない)
- **スクロールするのは会話領域だけ。**`html, body { height:100%; overflow:hidden; overscroll-behavior:none }`
- サイドバーは `lg:` で常設(`w-[17.5rem] border-r border-edge`)、それ未満はドロワー(`translate-x` + `bg-black/70 backdrop-blur-[2px]` のスクリム)
- **環境光**: 会話面の背後に `.ambient-glow` を 1 枚敷く。`--ambient-deep` の藍が下部にたまるグラデーションを `84s` で往復させる。**会話が始まると `opacity` を落として後退させる**(空状態が最も明るい)

---

## 5. サイドバー(`OC_Sidebar`)

本システムにスレッド履歴はない(1 ユーザー 1 セッション)。したがってサイドバーが持つのは**銘・使い方・ユーザー**の 3 段。

| 段 | 中身 |
| --- | --- |
| **上**(`border-b border-edge`) | アプリアイコン(`h-11 w-11 rounded-ui shadow-soft`)+ `Chokai Guide`(`font-display font-semibold tracking-[-0.025em]`)+ 副題「鳥海山エリア観光ガイド」(`text-[11px] text-text-dim`) |
| **中**(`flex-1 overflow-y-auto`) | 使い方。見出し + 説明文 + **例文 2 つ**。例文は `rounded-ui-sm border border-edge bg-fill-hover` のカードにし、**押すと入力欄に入る**(現行は押せない `<li>`) |
| **下**(`border-t border-edge`) | ユーザー頭文字のアバター(`rounded-full bg-ink-high` + 右下に `bg-brand-signal` の在席ドット)、ユーザー名、ログアウトボタン(`h-11 w-11`) |

- 「会話ごとに決まっていく計画は、画面右のガイダンスマップに反映されます」の一文は**「地図ウィンドウ」の実態に合わせて書き換える**(§8 でウィンドウは右下起点になる)
- 下端は `pb-[calc(0.75rem_+_env(safe-area-inset-bottom))]`

---

## 6. ヘッダー

`sticky top-0 z-20 border-b border-edge bg-ink-base/[0.88] backdrop-blur-xl pt-[env(safe-area-inset-top)]`、中身は `max-w-3xl` で中央寄せ、`min-h-[68px]`。

| 画面 | 中身 |
| --- | --- |
| `<lg` | ☰(サイドバーを開く、`h-11 w-11`)/ `Chokai Guide` / ⓘ(このアプリについて) |
| `lg:` | `Chokai Guide` + `Powered by Gemma 4` / 右端に `CHOKAI / GUIDANCE` の小ラベルと ⓘ |

**「AI Agent by Qwen3」を廃止する**(§6-1)。実際の生成モデルは `.env` の `INFERENCE_MODEL=google/gemma-4-31B-it-qat-w4a16-ct` なので表記は **`Gemma 4`**。Qwen3 は埋め込み側の名前であり、生成モデルとして出してはいけない。

`Powered by` は `text-[9px] uppercase tracking-[0.16em] text-text-dim`、`Gemma 4` は控えめなグラデーション文字。

---

## 7. 会話面

### 7.1 空状態(メッセージ 0 件)

`max-w-3xl` を縦中央に。上から順に、`--motion-stagger` ずつ遅らせて `empty_item_enter`(下から 0.875rem + フェード)で入る。

1. アプリアイコン `h-14 w-14 rounded-ui shadow-soft`
2. 見出し `text-[clamp(2rem,5vw,3.6rem)] font-semibold leading-[1.12] tracking-[-0.05em]`
   例: 「**{ユーザー名}さん、**\n鳥海山のどこへ行きましょうか」
   —— **ユーザー名の行だけ `.aurora-copy`**(`--aurora-copy` を `background-clip:text` で乗せる)
3. **提案の丸ピル**(`rounded-full border border-edge-strong bg-ink-surface/65 min-h-11`、hover で `-translate-y-0.5`)。右端に `--color-signal-soft` の矢印、hover で左下へ 0.125rem 動く

**現行のフローティングカード 3 枚(`absolute bottom-[100px]`、`v-if` なし)は削除する。**提案は空状態にしか出さない —— これで §4-1(会話に永久に重なる)と §4-6(モバイルで 3 枚目が見切れる)が**構造的に消える。**

### 7.2 メッセージ列

`TransitionGroup name="message-list"`。各行 `px-4`、内側を `mx-auto max-w-3xl`、行間 `space-y-8`。

**入場**: アシスタントは下から 0.75rem + フェード。ユーザーは右下から `translate3d(0.75rem, 0.625rem, 0) scale(0.98)`(発話が入力欄から飛んでくるように見える)。

| 役割 | 見た目 |
| --- | --- |
| **ユーザー** | 右寄せ。`max-w-[88%] sm:max-w-[78%]`、`rounded-[1.35rem] rounded-br-md`(右下だけ角を落として発言者を示す)、`border border-white/[0.075] bg-ink-high shadow-soft`、`px-4 py-2.5 leading-7`、`whitespace-pre-wrap break-words` |
| **アシスタント** | **吹き出しを持たない。**キャンバスの上に直接組む(参考実装と同じ)。本文は `.markdown-body`(§9)。前置きにアプリアイコン `h-6 w-6` |

**待機表示**: 現行の Gemini 風スピナー(`animate-gemini-spinner-*`)は**残す** —— 既に動いており、`--motion-*` と衝突しない。ただし**リングの色を `--aurora-copy` の 3 色**(`#4f7df3` / `#2fc9b0` / `#cfe6ee`)に差し替える。`statusText` は `text-sm text-text-muted`。

**最新へ戻るボタン**: 会話が下端にないときだけ、コンポーザの上に `absolute -top-14 left-1/2` で丸ボタンを出す(`bg-ink-raised/70 backdrop-blur-md shadow-glass`)。**現行の入力欄右の常設 ↓ ボタンは廃止する。**

### 7.3 コンポーザ(`OC_ChatInput`)

**会話領域の外(`.main` 直下)に `absolute bottom-0 left-0 right-0`。**上に `.composer-dock` のグラデーション(キャンバス色へ溶ける)を敷き、本文がその下をくぐる。

> **2026-08-05 訂正:** 初版は `sticky bottom-0` と書いていたが、**モックアップは `absolute` を採っており、実装もそれに合わせた**(本書冒頭が「実装はモックアップに合わせる」と定めているため)。本文を実装に追従させる。
>
> **`absolute` の代償を明示する:** コンポーザはスクロール領域の外にあるので、**その高さぶんの余白は会話側が自分で確保しなければならない。**会話がある側だけでなく、**空状態にも同じ下端余白が要る**(これを怠ると §13 条件 16 に落ちる)。

```
.composer-shell  rounded-[1.6rem] p-2 flex items-end gap-2
                 border border-edge-strong  bg-ink-raised  shadow-soft
:focus-within    border-color rgba(236,244,247,.32) / 面を 1 段上げる

/* フォーカスの表示は「シェル」に付ける。textarea には付けない(§11 F2) */
.composer-shell:has(textarea:focus-visible)
                 outline 1px solid + outline-offset 3px
                 + composer_focus_aurora 3.6s(藍 → 碧 → 白銀 を巡回)
.composer-shell textarea
                 outline: none(枠を持たない素の入力欄)

--streaming      border-color transparent
                 padding-box に地色、border-box に --aurora-edge
```

> **2026-08-05 訂正 —— 初版の書き方が実装を誤らせた。**初版は上の行を `textarea:focus-visible outline 1px` とだけ書いており、**アウトラインを textarea 自身に付ける**と読めた。実装もそうなり、**角丸 0 の textarea に矩形のアウトラインが描かれ、その角が 25.6px 角丸のシェルを突き破って外へ出た**(実測: シェル `radius 25.6px` / textarea `radius 0px` / `outline-offset 2px`)。ユーザー報告「入力している時に出現する四角枠が、元からある枠からはみ出る」はこれである。**枠を出す相手はシェルであって、中の素の入力欄ではない。**

- `textarea` は `rows=1`、`min-h-11`、**`max-h-[164px]`**(20px + 24px × 6 行)で自動伸長。`bg-transparent` / `outline-none` / `placeholder:text-white/45`
- 送信ボタンは `h-11 w-11 rounded-full`。**有効時は反転面**(`bg-ink-paper text-paper-ink`、hover で `-translate-y-0.5`)、無効時は `bg-white/[0.07] text-white/25`
- **送信中は同じ場所が停止ボタンになる**(現行どおり `@stop`)。色は `--color-danger` 系ではなく、`bg-ink-high` に停止アイコンで十分(送信は破壊的操作ではない)
- Enter で送信 / Shift+Enter で改行 / **`event.isComposing` を見る**(現行は `@keydown.enter.prevent` で IME 確定の Enter を送信に食われる)

### 7.4 質問フォーム(`OC_AskUserForm`)

**現行はコンポーザの上に貼り付く帯。これを会話の流れの中のカードに変える**(質問は会話の一部であって、器具ではない)。

```
rounded-ui-lg border border-edge-strong bg-ink-raised shadow-soft p-4
左肩に 碧の「?」バッジ (h-7 w-7 rounded-full bg-brand-signal/15 text-brand-signal)
見出し「確認させてください」/「AIからの質問」  font-display font-semibold
理由文 (prompt.reason)                        text-sm text-text-muted
選択肢  grid gap-2 sm:grid-cols-2
        min-h-11 rounded-ui-sm border border-edge-strong bg-fill-hover
        hover: border-brand-signal/50 + bg-brand-signal/[0.08]
自由入力 rounded-ui-sm bg-ink-base border border-edge  ← 現行の bg-slate-100(白)を廃止
「あとで」 text-text-dim の text ボタン
```

### 7.5 候補カード

`grid gap-2 sm:grid-cols-2`。1 枚は `rounded-ui border border-edge bg-ink-surface p-3`、hover で `border-edge-strong bg-ink-raised`。

- 名前 `font-medium text-text`
- 理由 `text-xs leading-relaxed text-text-muted`。タグ・所要時間・滞在目安は**丸チップ**(`rounded-full border border-edge bg-fill-hover px-2 py-0.5 text-[10px]`)に分ける(現行は `/` 連結の 1 行)
- `phase === 'provisional'` のとき見出し脇に「候補」バッジ

> **押せるようにはしない**(§5-1)。機能追加であり本作業のスコープ外([ADR-0023](../adr/0023-frontend-design-system.md) 影響節)。

### 7.6 旅程カード

`rounded-ui-lg border border-edge-strong bg-ink-raised p-4`。

```
見出し   旅程 v{n}        font-display font-semibold
バッジ   provisional のときだけ「調整中」(現行どおり。final ではバッジを出さない)
         assumptions があれば「仮の前提あり」(--color-warn 系)
右上     「元に戻す」(final かつ v>1)  rounded-ui-sm border border-edge-strong

日ごと:
  日付見出し  text-sm font-display text-brand-signal   ← 現行の text-blue-200 を差し色に
  各行  ┌──────┬─────────────────────────┬────────┐
        │ 09:30 │ ● 元滝伏流水             │ 40分   │
        └──────┴─────────────────────────┴────────┘
        時刻   font-display tabular-nums text-text-dim w-12
        ● は左の縦線(border-l border-edge)上のドット。行間を線でつなぎ、
          「順路」であることを見た目で表す(現行はただの箇条書き)
        滞在  text-xs text-text-dim

差分     border-t border-edge pt-3 の上に text-xs text-text-muted
譲歩     text-xs、--color-warn
```

### 7.7 内部語の遮断(P7)

表示層の責務として、次を**文字列に出さない**:

| 出さないもの | 代わりに出すもの | 現行の位置 |
| --- | --- | --- |
| `party` / `mobility` / `pace` の生 enum(`short_walk_ok / relaxed`) | 日本語ラベルの対応表を `lib/profileLabels.js` に置いて引く | `OC_ChatMessage.vue` の `profileSummary`(§3-2) |
| 全項目が空のときの「希望条件を更新しました」 | **何も描画しない** | 同 §3-5 |
| スポット名の括弧書き `（tourist_spot）` | 名前だけ | `NavView.vue:1168`(§3-4) |

対応表にない値が来たら、**その項目を落とす**(生の値を出さない)。既に `spotDisplay.js` が `spot_id` に対して同じ方針を取っている(直近コミット `af6eb0b`)ので、それに揃える。

---

## 8. ガイダンスマップ(`NavWindow` + `NavView` のコントロール)

**形はフローティング窓のまま**(ユーザー判断 2026-08-05)。作り直すのは外枠・ヘッダー・トグル・窓内の配置。

### 8.1 いま何が壊れているか(実機確認 2026-08-05)

420px 幅のパネルに「端末に取り込む」「LIVE SYNC」「Spots List」「FOLLOW」の 4 枚が重なり、**地図本体は右端の細い帯しか見えない**(§4-3)。トグルは `pulse-glow 2.5s infinite` で自走し、自動操作がクリックできない(§4-4)。

### 8.2 窓

```
.nav-window
  rounded-ui-lg  border border-edge-strong  bg-ink-surface
  shadow-glass   backdrop-blur-xl   overflow-hidden   ← 現行 overflow:visible で角が抜ける
  全画面時は rounded-none / border-0 / shadow-none
```

**ヘッダー**(ドラッグ把手。`useNavWindow` の `startDrag` はそのまま使う):

```
h-12  px-4  border-b border-edge  bg-ink-raised   ← 現行の青紫グラデーションを廃止
左  ▚ の把手アイコン(text-text-dim)+「ガイダンスマップ」
      font-display text-sm font-semibold tracking-[-0.02em]   ← 英語 "GUIDANCE MAP" を廃止(§6-3)
右  [⋯ メニュー] [⤢ 全画面] [× 閉じる]   各 h-11 w-11 rounded-ui-sm
      hover: bg-fill-hover
      (h-12 のヘッダーに 44px の標的が収まる。§11 に例外を作らない)
cursor: grab / :active grab bing、touch-action: none(現行どおり)
```

### 8.3 窓の中身 — 地図を主役に戻す

```
┌────────────────────────────────────────┐
│ ▚ ガイダンスマップ          ⋯  ⤢  ×   │  ← ヘッダー
├────────────────────────────────────────┤
│                                        │
│                                        │
│              地 図(全面)               │
│                                        │
│                              ╭───╮     │  ← 現在地追従のみ常設
│                              │ ⌖ │     │     h-11 w-11 rounded-full
│                              ╰───╯     │     bg-ink-raised/80 backdrop-blur
├────────────────────────────────────────┤
│ 訪問順  ① 元滝伏流水 → ② 丸池様 → ③ … │  ← 旅程ストリップ(新規)
└────────────────────────────────────────┘
```

| 要素 | 扱い |
| --- | --- |
| **地図** | 窓の面積を占める。**上に重なる常設要素は現在地追従ボタン 1 つだけ** |
| 「端末に取り込む」「オフライン資材」「LIVE SYNC」「LORA LINK」 | **ヘッダーの ⋯ メニュー(1 枚のポップオーバー)に畳む。**現在の状態は各項目の右に小さなドット/ラベルで示す。**機能と発火条件は現行のまま**(呼び出す関数を変えない) |
| 「Spots List」カード | **廃止し、下部の旅程ストリップに統合する** |
| **旅程ストリップ**(新規) | 窓の下端 `border-t border-edge bg-ink-raised/70 backdrop-blur`。訪問順に丸番号 + スポット名のチップを横スクロールで並べる。**`（tourist_spot）` は出さない**(§3-4)。空なら「まだ旅程がありません」 |

> **2026-08-05 訂正 — チップの強調は本作業の対象外。**初版は「現在地に最も近い/到達済みのチップを `border-brand-signal` にする」と書いていたが、**どちらも信頼できるデータ源が現状の store に無い。**当て推量で色を付けると、間違った位置を「現在地」と示してしまう。**全チップを同じ見た目にし、強調は [90_backlog.md](../90_backlog.md) に残す**(到達済みの判定は `rtStore` / 旅程の進行状態から出せる可能性があるので、そこから設計し直す)。

> **これが「旅程計画を表示する画面が見にくい」への回答である。**現行は旅程が「地図に重なるカード」でしか読めず、しかもそのカードが地図を潰していた。**旅程は窓の一部として定位置を持ち、地図と同時に読める。**

### 8.4 トグル —— **浮かせず、ヘッダーに置く**

```
現行: 画面右上に固定 (top-92px right-16px)、56px 角丸、青紫グラデ
      pulse-glow 2.5s infinite で自走
新:   ヘッダー右端のピル(§6 の ⓘ の隣)
      lg:  [● ガイダンスマップ ›]  min-h-[38px] rounded-full
           border border-edge-strong bg-fill-hover
      <lg: 同じ位置のアイコンボタン (h-11 w-11、地図ピン)
      hover: -translate-y-px / border-color rgba(碧,.45)
      無限アニメーションを持たない(P6)
      旅程が更新されたときだけ、碧のドットが 1 回だけ脈打って止まる
```

**浮かせるのをやめる理由**(モックアップ検証 2026-08-05):

当初は「右下に浮かせる」で設計したが、**モックアップに起こしたら右寄せのユーザー発話に重なった** —— これは直そうとしている §4-5 そのものである。**画面のどこに浮かせても、いずれかの要素と衝突しうる。**

**ヘッダーは sticky で常に見えており、本文の領域と重ならないことが構造的に保証される。**地図の開閉は毎秒行う操作ではないので、親指から遠いことは許容できる。加えて、ヘッダーに置くと**ガイダンスマップが「浮遊するガジェット」ではなく「行き先のひとつ」として読める。**

- `prefers-reduced-motion: reduce` で残る動きも止める
- サイドバーの案内文も実態に合わせる(「画面右の」→「上部の」)

### 8.5 触らないもの(再掲)

`lib/useNavWindow.js` の座標計算・ドラッグ・境界クランプ、`NavMap`、`audioManager`、`usePosition`、`geoutils`、`loraBridge`、`offlinePack`、`tiles`。**`NavView.vue` は分割しない**([ADR-0017](../adr/0017-frontend-incremental-change.md) の該当判断は維持)。

---

## 9. 本文の組版(`.markdown-body`)

`@tailwindcss/typography` を捨て、`design-system.css` に自前で書く。理由: prose のトークンが本デザインシステムのトークンと二重になり、`prose-invert prose-zinc lg:prose-lg prose-p:*` の上書きが現行で既に 4 段重なっている。

```
本文        color #dedfd9 相当 → var(--color-text) の 1 段落ち、line-height 1.78
段落間      * + * に margin-top 1rem
見出し      font-display / font-weight 650 / letter-spacing -0.022em
            h1 1.375rem  h2 1.1875rem(下線 border-edge)  h3 1.0625rem  h4 1rem
            見出しの前は margin-top 1.75rem
リスト      li::marker を rgba(碧, 0.72)
リンク      var(--color-signal-soft)、下線は薄く、hover で濃く
コード      bg rgba(236,244,247,.075) / border edge / radius .375rem
pre         bg #070a0b / border edge / radius var(--radius-sm)
引用        border-left 2px solid var(--color-signal) / text-muted
表          block + overflow-x auto(横に溢れさせない)
```

**`marked` + `DOMPurify` の経路は現行のまま**(サニタイズは維持)。

---

## 10. ログイン画面

> **2026-08-05 改訂(ユーザー指示)。**初版は参考実装に倣って「写真の hero + 下から立ち上がる明るいシート」に作り替えたが、**ユーザーの判断で旧実装の構成に戻す。**
>
> > ログイン画面は以前のものに戻してください。以前のログイン画面をベースに、ブラッシュアップしてください。**ログイン画面は参考にせずに**、以前のものをブラッシュアップする。
>
> **したがってログイン画面だけは参考実装([sarutahiko](https://github.com/TakaJun02/sarutahiko))を参照しない。**戻す先は `git show HEAD:frontend/src/views/LoginView.vue`(2026-08-05 の作り直し前)。

### 10.1 戻す構成(旧実装の骨格)

```
┌────────────────────────────────────────┐
│                                        │  ← 鳥海山の写真を全面に敷く
│   Chokai Guide                         │     (public/chokai.jpg、cover/center)
│   鳥海山エリア観光ガイド                  │     上から黒のグラデーションを重ねる
│                                        │
│        ╭──────────────────────╮        │
│        │  おかえりなさい        │        │  ← 中央に浮くグラス調のパネル
│        │  ユーザー名を…         │        │     backdrop-blur + 半透明の面
│        │  ┌────────────────┐  │        │
│        │  │ ユーザー名       │  │        │
│        │  └────────────────┘  │        │
│        │  ┌────────────────┐  │        │
│        │  │ 日本語      ▾   │  │        │
│        │  └────────────────┘  │        │
│        │  [    はじめる     ]  │        │
│        ╰──────────────────────╯        │
│                                        │
└────────────────────────────────────────┘
```

| 要素 | 旧実装 | 戻す |
| --- | --- | --- |
| 背景 | `chokai.jpg` を `bg-cover bg-center` で全面 | **そのまま** |
| 覆い | `bg-gradient-to-b from-black/90 via-black/60 to-transparent` | **考え方は同じ(上ほど暗く、文字を載せる余地を作る)が、値は §10.2-8 で見直す** |
| タイトル | 画面上部に `absolute`(`pt-24 sm:pt-32`)。大見出し + 小さい第 2 行 | **そのまま**(位置と 2 行構成) |
| フォーム | **中央に浮くグラス調パネル**(`backdrop-blur` + 半透明 + 角丸 + 枠) | **そのまま** |
| 入場 | タイトルが `fadeInScale`(0.2s 遅延)、パネルが `fadeInUp`(0.4s 遅延) | **そのまま**(値は §3.3 のモーショントークンに載せ替える) |

### 10.2 ブラッシュアップする点(旧実装の何を良くするか)

**「戻す」のは構成であって、色や粗さまで戻すのではない。**

| # | 旧実装の問題 | どうする |
| --- | --- | --- |
| 1 | パネルが `bg-slate-800/60 border-slate-700`、入力が `bg-slate-900/70`、ボタンが `bg-blue-600`、focus が `ring-blue-500/50` —— **アプリ本編と無関係の slate/blue 系** | **すべて Chokai Signal のトークンに載せ替える。**パネル = `bg-ink-surface/60 border-edge-strong rounded-ui-lg shadow-glass backdrop-blur-xl` / 入力 = `bg-ink-base/70 border-edge` / focus = `--color-signal` / 送信ボタン = **反転面**(`bg-ink-paper text-paper-ink`。§7.3 の送信ボタンと同じ扱い) |
| 2 | タイトルが `text-shadow: 1px 1px 10px` のべた影 | 影を `--color-canvas` 基準の柔らかいものにし、書体を `font-display` に |
| 3 | 「Chokai Guidance / by LLM」で**アプリ内の表記(Chokai Guide)と食い違う** | **`Chokai Guide` + 第 2 行を「鳥海山エリア観光ガイド」**に統一(サイドバーの銘と一致させる) |
| 4 | 小さいビューポート(780×493)でタイトルとパネルが重なる([23 §4-8](../23_ux_issues.md)) | タイトルを `absolute` + `mt-32` の力技でなく、**縦に積んで中央寄せする**(重なりが起き得ない組み方にする)。画面が低いときはスクロールで到達できること |
| 5 | 入力・ボタンの高さが 44px 未満 | §11 のとおり 44px |
| 6 | 文言が全部英語(§6-2) | **日本語のまま維持する**(作り直しで是正済み。§6-2 は解消として記録済みなので戻さない) |
| 7 | 「Sign up」で切り替わる登録モードが、パスワード欄が無いためログインと見分けられない(§6-4) | **1 フォームのまま維持する。**`POST /login` はユーザー名のみで未登録なら作成するので、2 モードを持つ必然性がない([23 §7-1](../23_ux_issues.md)) |

| 8 | **暗幕が画面の中盤で切れ、パネルが写真のいちばん明るい部分に浮く**(2026-08-05、実装後の実機確認で判明) | 下記 §10.2.1 |

> **6 と 7 は「戻さない」判断である。**どちらも既知issueとして解消済みで、戻すと退行になる。**ユーザーの指示は「デザインを戻す」であって「直した不具合を戻す」ではない**と解釈した。もし文言や 2 モードも旧に戻す意図であれば、この 2 行を差し戻す。

#### 10.2.1 暗幕は「下まで効かせる」(2026-08-05 追記)

**実機で判明した問題。**旧実装の `from-black/90 via-black/60 to-transparent` をそのまま使うと、**画面が高いほど暗幕が早く尽きる。**1440×900 では中央のパネルが**写真のいちばん彩度の高い部分(新緑と水面)の上に浮き**、グラス調パネル(`bg-ink-surface/60`)がその明るさを拾って濁った緑灰色になる。プレースホルダのコントラストも 4.6:1 まで落ちて余裕がない。780×493 では暗幕が相対的に広く効くため問題が出ず、**画面が広いときだけ壊れる。**

**写真は主役ではなく、質感である。**全面に敷く以上、フレーム全体を落ち着かせてからその上に情報を置く。

```
層 1  写真          bg-cover bg-center
層 2  縦の暗幕       上端で最も濃く、中盤で少し緩め、下端は再び締める
                    → 透明で終わらせない。「下限」を持たせる
                    色は純黒ではなく --color-canvas 基準にして地の色と馴染ませる
層 3  周辺減光       中央から外へ向かうごく薄い暗さ(任意。パネルの背後を鎮める)
```

- **暗幕を透明で終わらせない。**下端にも floor を残す(タイトルが載る上端 > パネルが載る中盤 ≧ 下端、の順に濃く)
- パネルの不透明度を上げる(`/60` では明るい背景を拾いすぎる)。**背景がどこであってもパネル内のコントラストが 4.5:1 を明確に上回る**ことを、写真の明るい領域で実測して確かめる
- **判定は 1440×900 で行う**(いちばん条件が厳しい)。条件 18 の実測は、**パネルが写真の明るい部分に重なる状態**で取ること

### 10.4 入場の振り付け(2026-08-05 追加・ユーザー指示)

§3.3.1 の規則をこの画面に当てる。**旧実装(0.7s / 0.8s・遅延 0.2s / 0.4s)より要素数を増やし、種類の差も付ける。**

| # | 要素 | 遅延 | 時間 | 動き(§3.3.1) |
| --- | --- | --- | --- | --- |
| 0 | **背景写真** | 0 | `--enter-scene` 1200ms | `--settle` `scale(1.05) → 1` + フェード |
| 1 | **`Chokai Guide`** | 100ms | `--enter-slow` 880ms | `--lift` 上がりながら `scale(.96) → 1` |
| 2 | オーロラの罫(§10.5-3) | 190ms | `--enter-base` 760ms | `--nudge` + 幅 0 → 3rem |
| 3 | 「鳥海山エリア観光ガイド」 | 250ms | `--enter-base` 760ms | `--nudge` |
| 4 | **パネル** | 340ms | `--enter-slow` 880ms | `--rise` `translateY(1.75rem) → 0` |
| 5 | 「おかえりなさい」 | 520ms | `--enter-quick` 620ms | `--nudge` |
| 6 | 「ユーザー名を入力してください」 | 580ms | `--enter-quick` 620ms | `--nudge` |
| 7 | 入力欄 | 650ms | `--enter-quick` 620ms | `--nudge` |
| 8 | 言語選択 | 710ms | `--enter-quick` 620ms | `--nudge` |
| 9 | ボタン | 770ms | `--enter-quick` 620ms | `--nudge` |

**最後の要素が落ち着くのは約 1.39 秒。**旧実装(約 1.2 秒)より少しだけ長く、**段は 2 段から 9 段に増える。**

**背景はさらに、入場後もごく緩やかに漂う**(`--motion-ambient` 相当の 40s 前後で `alternate`。振幅は 1〜2% に留める)。止まった写真より、わずかに生きている写真のほうが「立ち上がった画面」に見える。

### 10.5 さらに磨く点(2026-08-05・デザインリード判断)

| # | 対象 | 現状 | どうするか |
| --- | --- | --- | --- |
| **1** | **言語選択が素の `<select>`** | ブラウザ既定の矢印が出て、この画面で**唯一トークンの外にある部品**になっている | `appearance-none` にして**自前の chevron**(`--color-text-dim`、右 `1rem`)を重ねる。**`<select>` 要素自体は残す**(ネイティブの選択 UI とキーボード操作を捨てない) |
| **2** | **パネルの縁が一本調子** | `border-edge-strong` だけで、面が平ら | **上辺にだけ光を置く**(`inset 0 1px 0 rgb(255 255 255 / .07)`)。加えて面にごく浅い縦グラデーション(上が明るい)を敷き、§2 の P2「高さは明度で表す」を面そのものにも効かせる |
| **3** | **タイトルにブランドの気配がない** | 白のボールドだけ。アプリ本編の碧・オーロラと繋がっていない | **タイトルと副題の間に、短いオーロラの罫**(高さ 2px・幅 3rem・`--aurora-copy`・角丸)を 1 本置く。空状態はユーザー名をオーロラで抜くが、**ログイン時点では名前が無い**ので、罫がその役を担う |
| **4** | **ボタンが静的** | 反転面 + hover で持ち上がるだけ | 文言の右に**矢印**を添え、hover で右へわずかに滑らせる(§7.1 の提案ピルと同じ所作。画面をまたいで語彙を揃える) |
| **5** | **エラーバナーに記号がない** | 文字だけ | 本編の警告表示と同じく**アイコンを添える**(`--color-danger`) |
| **6** | **入力の focus が控えめ** | `ring-brand-signal/30` | 枠を碧にしたうえで**リングをわずかに広げる**。§11 の `:focus-visible` の規定は崩さない |

> **やらないこと。**パネルをさらに透かす(§10.2.1 でコントラストを確保したばかり)、写真を差し替える、ロゴを足す(§2.1 でアイコンは維持と決定済み)。

### 10.6 ライトの面について

初版 §10 は「ログイン画面のシートだけライトを許す」としていたが、**旧構成に戻すとライトの面は無くなる**(グラス調パネルはダーク)。したがって **P1「1 画面 1 テーマ」に例外がなくなる。**§2 の P1 と §13 条件 1 の但し書き(「ログイン画面のシートを除く」)も不要になる。

---

## 11. アクセシビリティ

| 項目 | 規定 |
| --- | --- |
| タップ標的 | **すべての操作要素は最小 44px**(`min-h-11` / `h-11 w-11`)。**例外を作らない。**地図窓のヘッダーボタン(§8.2)も 44px。見た目を小さくしたい場合は、アイコンを小さくして**標的だけ 44px に保つ**(背景は hover まで出さなければよい)。対象: ボタン・リンク・チップ・入力欄・メニュー行のすべて |
| フォーカス | 下記 **F1〜F3** |
| コントラスト | 本文 `--color-text` / 補足 `--color-text-muted`(8.5:1)/ ラベル `--color-text-dim`(5.2:1)。**それ以下の明度の文字を作らない** |
| 減モーション | `@media (prefers-reduced-motion: reduce)` で `animation-duration: .001ms` / `transition-duration: .001ms`、環境光と待機リングは停止 |
| 対話状態 | 質問フォームに `aria-busy`、旅程ストリップに `aria-label` |
| ドロワー | **開いている間、背景側(サイドバー以外)を `inert` にする。**ドロワー自身に `inert` を付けるのではない(`v-if` で出し入れするなら常に false になり無意味)。加えて **Escape で閉じ、開いたらフォーカスをドロワー内へ移し、閉じたら開いたボタンへ戻す** |
| 見出し | 各画面に `h1` を 1 つ。ヘッダーの `Chokai Guide` が `h1` |

### 11.1 フォーカス表示の規則(2026-08-05 追加)

**フォーカス表示は 1 つの言語にする。**同じ画面に 2 種類の表現が混ざると、どれが「今ここ」なのか読めなくなる。

| # | 規則 | なぜ |
| --- | --- | --- |
| **F1** | **既定は `:focus-visible` の外周線。**`outline: 2px solid var(--color-signal-soft); outline-offset: 3px`。ボタン・リンク・チップ・メニュー行はこれ | 1 か所で定義し、全体に効かせる |
| **F2** | **枠の形は、囲む相手の角丸に必ず一致させる。**角丸 0 の要素に外周線を出してはいけない —— **とりわけ、角丸を持つ器の中にある「枠を持たない素の入力欄」には出さない。矩形の角が器の丸みを突き破る。**この場合は**器のほうに出す**(`:has(… :focus-visible)`) | 実際にコンポーザで起きた(§7.3 の訂正) |
| **F3** | **自前の枠を持つ入力欄は、外周線ではなく「枠の変化 + にじみ」で示す。**枠を `--color-signal` にし、`ring` を自身の角丸に沿わせる。この場合は**既定の外周線を消す**(`outline-none`)—— **消さないと二重になる** | 入力欄は自分の輪郭を持っているので、外にもう 1 本引く必要がない |

| **F4** | **当たり判定を見た目より大きくした要素は、枠を「見えている部分」に出す。**§11 の「アイコンを小さくして標的だけ 44px に保つ」を使った箇所がこれにあたる。当たり判定の器(角丸 0・44px)に枠を出すと、**小さなチップの周りに大きな四角が出る** | 44px 化の副作用として実際に起きた(下記) |

**判定は 1 つの問いに落ちる: 「いま出ている枠は、どの要素の輪郭をなぞっているか」。**なぞる相手と形が一致していなければ直す。

> **F4 が生まれた経緯(2026-08-05)。**受け入れ条件 13(44px・例外ゼロ)を満たすため、旅程カードの「仮の前提あり」チップを `<summary>`(当たり判定 44px・角丸 0)+ 内側の `<span>`(`rounded-full` の小さいピル)という構造にした。**その結果、フォーカス時に小さなピルの周りへ 44px の四角い枠が出るようになった** —— コンポーザと同じ型の不具合を、**別の受け入れ条件を満たす作業が新たに作った。**
>
> **当たり判定と見た目がずれている要素はすべて F4 の対象である。**44px 化した箇所は一度すべて見直すこと。

---

## 12. 触らない範囲(この文書が保証すること)

**表示層の作り直しは、次のいずれの呼び出し方も変えない。**

`stores/chat.js` / `stores/nav.js` / `stores/user.js` の公開 API、`lib/` 配下の全モジュール(`api` / `sse` / `chatAnswerFlow` / `askAnswer` / `audioManager` / `usePosition` / `geoutils` / `loraBridge` / `loraCodec` / `offlinePack` / `poi` / `realtime*` / `routeRetry` / `spotDisplay` / `swClient` / `tiles` / `useNavWindow` / `uuid`)、`public/sw.js`、ルーティング定義。

`views/PlanView.vue` と `components/PlanForm.vue` は [ADR-0017](../adr/0017-frontend-incremental-change.md) が「未使用の残骸」と分類しており、**本作業でも触らない**(`/plan` は legacy ルート)。

---

## 13. 受け入れ条件

実装完了の判定はこの表で行う。**すべて実機(`http://localhost:5173/`)で確認する。**

| # | 条件 | 確認方法 |
| --- | --- | --- |
| 1 | **1 画面にライトの面が同居しない** | 1440×900 と 390×844 で全画面(ログイン含む)を開き、白い面が無いこと。**例外なし**(§10.3) |
| 2 | **提案カードが会話に重ならない**(§4-1 / §4-6) | メッセージが 1 件以上あるとき、提案要素が DOM に存在しない |
| 3 | **会話本文が `max-w-3xl` に収まる**(P4) | 1440px で本文ブロックの実測幅が 768px 以下 |
| 4 | **地図ウィンドウで地図が主役になっている**(§4-3) | 窓を開いた状態で、地図に重なる常設要素が現在地追従ボタン 1 つだけであること |
| 5 | **旅程が地図と同時に読める** | 旅程ストリップに訪問順が番号付きで並び、`（tourist_spot）` 等の内部語が無いこと |
| 6 | **トグルが自走しない**(§4-4) | Playwright の通常 `click` が "element is not stable" にならずに成立する |
| 7 | **トグルが本文に重ならない**(§4-5) | 390×844 / 1440×900 とも、トグルがヘッダー内にあり会話領域の矩形と交差しない |
| 8 | **モデル名が正しい**(§6-1) | ヘッダーに `Gemma 4`。"Qwen3" が画面上に無い |
| 9 | **UI ラベルが日本語**(§6-2 / §6-3) | ログイン画面・地図ウィンドウに英語の操作ラベルが無い(`Powered by` のような銘は除く) |
| 10 | **内部 enum が出ない**(§3-2 / §3-4 / §3-5) | 希望条件の表示が日本語。空の profile では何も出ない |
| 11 | **IME 確定の Enter で誤送信しない** | 日本語入力の変換確定で送信されないこと |
| 12 | **減モーションが効く** | `prefers-reduced-motion: reduce` で環境光と待機リングが止まる |
| 13 | **44px 標的**(§11) | **全**操作要素の実測高さが 44px 以上。**例外ゼロ**(地図窓のヘッダーボタンを含む) |
| 14 | **既存テストが通る** | `cd frontend && npm test` が作業前と同じ結果 |
| 15 | **触らない範囲が変わっていない**(§12) | `git diff --stat` に `lib/` と `stores/` と `public/sw.js` が現れない |
| **16** | **空状態でも提案ピルがコンポーザに隠れない**(§7.3 / 旧 §4-6 の再発防止) | **844×390(スマホ横持ち)と 390×500** で、各ピルの中心に `document.elementFromPoint` を撃って**ピル自身が返る**こと。隠れる場合はスクロールで到達できること |
| **17** | **`token/alpha` のユーティリティが生成されている**(§3.0) | `npx vite build` の出力 CSS に `.bg-ink-base\/`・`.bg-brand-signal\/`・`.text-paper-ink\/` が**実在**すること。**0 件なら §3.0 の写像を誤っている** |
| **18** | **ログイン画面の文字が読める** | パネル内の全テキストのコントラストが 4.5:1 以上(条件 17 が落ちると 1.0:1 になる) |
| **19** | **ログイン画面でタイトルとパネルが重ならない**(旧 §4-8) | **780×493・1440×900・390×844** で、タイトルの矩形とパネルの矩形が交差しないこと。低い画面では**スクロールで全要素に到達できる**こと |
| **20** | **入場がゆっくりで、要素ごとに差がある**(§3.3.1 / §10.4) | ログイン画面を読み込み、**`getAnimations()` で全アニメーションの `delay` と `duration` を実測**する。(a) **遅延が 5 段以上に分かれている**(旧実装の 2 段より豊か)(b) **時間が 2 種類以上ある**(一律でない)(c) **最短でも 600ms 以上**(`--motion-slow` 480ms より遅い)(d) 最後の要素が落ち着くのが **1.2〜1.6 秒**。あわせて **t=0 / 300 / 600 / 900 / 1400ms のスクリーンショット**を撮り、**段階的に組み上がっている**ことを目視で確かめる |
| **21** | **減モーションで入場が止まる** | `emulateMedia({reducedMotion:'reduce'})` で `getAnimations()` の全 `duration` が実質 0 になり、**全要素が最初から見えている**こと |
| **22** | **フォーカスの枠が器からはみ出さない**(§11.1 F2) | 全画面の**フォーカス可能要素を機械的に走査**し、**「自身の角丸 < 4px かつ、角丸 8px 超の祖先の内側にある」要素が、可視のアウトラインを持たない**ことを確認する。加えて**チャット入力欄・ログインの入力欄と言語選択・`ask_user` の自由入力**の 4 か所は、実際にキーボードで入力してフォーカス中の**拡大スクリーンショット**を撮り、枠が器の角丸をなぞっていることを目視する |
| **23** | **フォーカスの枠が二重にならない**(§11.1 F3) | 自前の枠を持つ入力欄で、`outline` と `box-shadow`(ring)の**両方が同時に可視**になっていないこと(computed style で `outlineColor` の alpha が 0、または ring が無いこと) |
</content>
</invoke>
