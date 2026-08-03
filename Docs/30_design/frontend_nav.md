# フロントエンド — 変更の範囲

- 状態: **決定稿 (2026-08-01)**
- 前提: **[ADR-0017](../adr/0017-frontend-incremental-change.md)(フロントエンドは差分改修に限る。ユーザー指示)** / [40_api/chat_sse.md](../40_api/chat_sse.md)(接続先の契約)/ [packs_pipeline.md](packs_pipeline.md) / [realtime_lora.md](realtime_lora.md)
- 関連: [offline_field_mode.md](offline_field_mode.md)(観光フェーズの動作)

---

## 0. この文書が決めること

**フロントエンドの「どこを触り、どこを触らないか」**を確定させる。

**この文書は変更インベントリである。**画面の作り直しを設計する文書ではない([ADR-0017](../adr/0017-frontend-incremental-change.md))。観光フェーズの動作は [offline_field_mode.md](offline_field_mode.md) が持つ。

### 0.1 原則

| # | 原則 |
| --- | --- |
| F1 | **動いているものは触らない。**地図・音声再生・位置判定・LoRa シリアルは実機で動いている資産である |
| F2 | **触るのは「新しい契約への接続」と「実害のあるバグ」だけ** |
| F3 | **新規 UI は既存の見た目に合わせる。**新しいデザインシステムを持ち込まない |
| F4 | **UI の状態はすべて `state` イベント由来**([chat_sse.md P2](../40_api/chat_sse.md))。ストリーム本文のパースで状態を作らない |

---

## 1. 触らないもの(明示)

| ファイル | 理由 |
| --- | --- |
| **`views/NavView.vue`(2,119 行)** | **分割しない**([ADR-0017](../adr/0017-frontend-incremental-change.md))。正しい分割線は実機でオフラインを通してからでないと引けない |
| `components/NavMap.vue` | Leaflet の描画。動いている |
| `lib/audioManager.js` | 再生キュー。**base + overlay は「2 件積む」だけ**で足りる([offline_field_mode.md §4](offline_field_mode.md)) |
| `lib/usePosition.js` / `usePosition.mock.js` / `lib/geoutils.js` | 位置取得と距離計算。動いている |
| `lib/loraBridge.js` の AT コマンド・Join・Android ブリッジ | 実機の LoRa モジュールと合っている。**decode 部分だけ**触る(§4) |
| `lib/useNavWindow.js` / `components/NavWindow.vue` | 案内ウィンドウ |
| `views/LoginView.vue` / `components/AppShell.vue` / `OC_Sidebar.vue` | 画面構成 |
| `lib/tiles.js` | タイル URL の定義(**`sw.js` 側をこれに合わせる**。§3) |
| `views/PlanView.vue` / `components/PlanForm.vue` / `stores/counter.js` / `components/icons/*` | **未使用の残骸。消してよいが優先しない** |

---

## 2. 触るもの — 接続の差分

### 2.1 API クライアント(`lib/api.js`)

| 変更 | 内容 |
| --- | --- |
| ベース URL | `'/back/api'` → **`import.meta.env.VITE_API_BASE ?? '/api/v1'`**。Vite のプロキシ設定で開発時を吸収する([22 §13-8](../22_current_issues.md) の「`import.meta.env` 使用 0 件」もここで解消) |
| 認証 | **`Authorization: Bearer <token>`** を付ける([chat_sse.md §4](../40_api/chat_sse.md))。トークンは `POST /login` の応答を `stores/user.js` が持つ |
| **`uuid` の自動注入を削除** | 全リクエストのクエリとボディに `uuid` を差し込んでいる([22 §12-6](../22_current_issues.md))。**消費者はログミドルウェアだけ**で、そのミドルウェアごと廃止される |
| エンドポイント | `POST /chat`(SSE。§2.2)/ **`POST /chat/answer`(質問への回答。§2.3)** / `GET /thread` / `GET /spots` / `POST /itinerary/undo` / `POST /packs` / `GET /jobs/{id}` |

### 2.2 チャットを SSE にする(`stores/chat.js`)

現行は**単発のブロッキング fetch**で、キャンセルもタイムアウトも無い([22 §13-3](../22_current_issues.md))。

```js
// 骨格（40 行程度。SSE のパースは自前で書く: chat_sse.md §1.1）
const ctrl = new AbortController()
const res  = await fetch('/api/v1/chat', {
  method: 'POST', signal: ctrl.signal,
  headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
  body: JSON.stringify({ message })
})
// res.body.getReader() を回して event: / data: を組み立て、kind で分岐
// 質問への回答は POST /api/v1/chat/answer（§2.3）。イベントはこのストリームに流れ続ける
```

| 受け取るもの | UI での扱い |
| --- | --- |
| `token` | 本文に追記(既存の `isPending` アニメーションはそのまま使える) |
| `state{kind:"candidates"}` | **推薦カード。**`phase:"provisional"` で描き、`"final"` で差し替える |
| `state{kind:"itinerary"}` | **旅程カード + 地図。**同上 |
| `state{kind:"ask_user"}` / `{kind:"clarify"}` | **`ask_user` 専用の入力フォーム(§2.3)。ターンの途中で来る — ストリームは開いたままにし、回答後の続きのイベントを同じリーダーで受ける**(2026-08-04、[chat_sse.md §1.4](../40_api/chat_sse.md)) |
| `state{kind:"step"}` | 「◯◯を探しています」等、手の実況の一行表示(2026-08-04: 旧 `searching` を統合。[chat_sse.md §1.2](../40_api/chat_sse.md)) |
| `state{kind:"profile"}` | プロファイル表示の更新 |
| `error` | `degraded:true` なら控えめな注記、`false` ならエラー表示 |
| `done` | ローディング解除 |

- **停止ボタンを付ける。**`ctrl.abort()` を呼ぶだけ。**停止しても見えていた旅程は消さない**([chat_sse.md §1.6](../40_api/chat_sse.md))
- **切断・リロード後は `GET /thread` で丸ごと取り直す**([chat_sse.md §1.5](../40_api/chat_sse.md))。再開機構は作らない
- **現行の「応答後に `navStore.fetchRoute` を呼ぶ」経路は削除する。**経路はサーバーが持ち、`state:itinerary` に `route_id` が入って届く([geo.md §3](geo.md))

### 2.3 【新規】`ask_user` 専用の入力フォーム(2026-08-03 改訂)

**決定(2026-08-03、ユーザー指示): 質問への回答は、メッセージ内のチップではなく「`ask_user` 専用の入力フォーム」で受ける。**

> **改訂前の決定を差し替える。**旧版は「`OC_ChatMessage.vue` にチップの行を足すだけで、新しいコンポーネントを増やさない」としていた。**実機で 2 つの実害が出たため改める**([23_ux_issues.md](../23_ux_issues.md)):
> - **§6-8**: サーバー側の `pending` が `null` なのにチップが残り、**古い問いに答えたつもりの送信**が起きる
> - **§6-5**: リロードすると同じチップ/カードが**複数のメッセージの下に重複描画**される
>
> 回答 UI を**会話ログから切り離し、現在の問いに対して 1 つだけ存在する**ようにすれば、どちらも構造的に起きなくなる。

#### 2.3.1 置き場所と形

**チャット入力欄の直上にドッキングする。**モーダルにはしない(NFR-4 / G5)。

```
┌──────────────────────────────────────────────┐
│  ……（会話ログ。質問文は token で流れてくる）   │
│                                              │
├──────────────────────────────────────────────┤
│ ❓ AI からの質問                    [あとで]  │  ← ask_user 専用フォーム
│    どのくらい歩けますか？                     │     現在の問いに対して 1 つだけ
│                                              │
│    ( ) あまり歩きたくない                     │  ← state.options
│    ( ) 30分程度なら                          │
│    ( ) 登山もしたい                          │
│                                              │
│    ┌────────────────────────┐  [ 回答する ]  │  ← 自由入力も併置（G5・FR-3.2）
│    │ 自由に書いても答えられます │               │
│    └────────────────────────┘               │
├──────────────────────────────────────────────┤
│  [ 質問してみましょう                    ]   │  ← 通常の入力欄（塞がない）
└──────────────────────────────────────────────┘
```

| # | 決めたこと | 理由 |
| --- | --- | --- |
| 1 | **フォームは `currentPrompt`(= サーバーの `pending_ask`)に対して 1 つだけ**。会話ログの中には出さない | 重複描画と、古い問いへの回答を構造的に無くす |
| 2 | **`pending` が消えたらフォームも消える** | [23_ux_issues.md §6-8](../23_ux_issues.md) の再発防止。**`GET /thread` の `pending` が唯一の真実** |
| 3 | **選択肢を選ぶと即送信**(1 タップで完了) | 現行のチップの速さを落とさない |
| 4 | **自由入力欄をフォーム内にも置く** | G5・FR-3.2。**選択肢に無い答えをその場で書ける** |
| 5 | **下の通常入力欄は塞がない**。ただし**回答待ちの間はその送信も `/chat/answer` に回す**(実行中ターンがある間 `POST /chat` は 409) | 質問に沿わない内容でもそのまま答えとして届き、扱いはエージェントが判断する |
| 6 | **送信は `POST /api/v1/chat/answer`**(2026-08-04 改訂。HITL) | 回答は実行中のターンに返り、**同じ SSE ストリームが続きを流す**([chat_sse.md §1.4](../40_api/chat_sse.md)) |
| 7 | **`kind:"clarify"` のときは `resolves` を付けて送る** | サーバーが `pending_ask` と突き合わせる([ADR-0019](../adr/0019-react-main-agent-subagents.md)) |
| 8 | **自由入力で答えたときは `resolves` を付けない** | サーバーが `answered_by:"free_text"` として扱う |
| 9 | **[あとで] で閉じられる**。閉じても `pending` は消えない | 質問が邪魔で操作できない状態を作らない。`pending` が生きている間(タイムアウト 10 分まで)はいつでも再表示できる |
| 10 | **送信中は選択肢と送信ボタンを `disabled` にする** | 二重送信の防止(サーバー側も弾く) |
| 11 | **リロードしても復元する** | `GET /thread` の `pending` から([chat_sse.md §3.1](../40_api/chat_sse.md)) |
| 12 | **フォーム表示中はスターターカードを隠す** | [23_ux_issues.md §5-3](../23_ux_issues.md)。重なって両方読めなくなる |

#### 2.3.2 `kind` による書き分け

**1 つのコンポーネントで両方を扱う**(`ask_user` は 1 つの Tool で `kind` が 2 用途を担うため。[ADR-0019](../adr/0019-react-main-agent-subagents.md))。違うのは見出しと、`clarify` が `surface` を持つことだけ。

| | `kind: "preference"` | `kind: "clarify"` |
| --- | --- | --- |
| 見出し | **AI からの質問** | **確認させてください** |
| 本文 | **`reason` をそのまま出す** | **「〇〇」はどちらですか**(`surface` を強調)+ `reason` |
| 送信時 | `resolves` **なし** | `resolves: {surface, value}` |

> **本文は `reason` を出す。フロント側で slot ごとの固定文を持たない**(2026-08-03 追記)。
> **実機で不整合が出たため明記する。**`slot:"origin"` の問いに対して固定文「どこから出発しますか？」を表示したが、実際の選択肢は「この条件で進める」「条件を変更する」で、**問いと選択肢が食い違った。**サーバー側の `pending_ask` は `reason:"仮定した旅程条件の確認"` を持っていたのに、**SSE と `GET /thread` の `pending` がそれを返していなかった**ことが原因である。
>
> したがって **`reason` を `state:ask_user` / `state:clarify` と `GET /thread` の `pending` に含める**([chat_sse.md §1.2](../40_api/chat_sse.md))。フォームは受け取った `reason` を表示し、**固定の対応表を持たない。**

#### 2.3.3 実装

**新規コンポーネント `components/OC_AskUserForm.vue` を 1 つだけ足す。**[ADR-0017](../adr/0017-frontend-incremental-change.md)(差分改修に限る)の範囲内で、**既存の見た目に合わせる**(原則 F3)。

- `views/ChatView.vue` の入力欄の直上に置く
- `stores/chat.js` の `currentPrompt` を唯一の供給源にする。**`OC_ChatMessage.vue` のチップ行は削除する**(2 か所に回答 UI を持たない)
- `sessionStorage` による補完([90_backlog.md §H-2](../90_backlog.md))は**サーバーの `pending` が正しく返るようになったら外す**

### 2.4 【新規】旅程カードと undo ボタン

- `state:itinerary` の `diff`(added / removed / moved / retimed)を差分として表示する
- **[元に戻す] は `POST /api/v1/itinerary/undo` を直接呼ぶ**(LLM を通さない。[chat_sse.md §2](../40_api/chat_sse.md))。`expected_current_version` を必ず付ける
- 応答は `state:itinerary` と同じスキーマなので、**描画コードは 1 本で済む**

### 2.5 【新規】パック生成の進捗パネル

- [端末に取り込む] → `POST /api/v1/packs {itinerary_version}` → **202** で `job_id`
- `GET /api/v1/jobs/{job_id}` を **2 秒間隔**でポーリング([packs_pipeline.md §8](packs_pipeline.md))
- **生成中もアプリは使える**(非モーダル。NFR-4)
- **`partial` を「完了」と表示しない。**「一部の案内を作れませんでした(N 件)」+ `missing` の内訳(FR-3.3)
- 完了後、観光フェーズの資材取得へ([offline_field_mode.md §2](offline_field_mode.md))

### 2.6 POI データをフロントに持たない

`src/assets/POI.json` と `facilities.json`(バックエンドとバイト同一の二重コミット: [22 §15-1](../22_current_issues.md))を**削除し、`GET /api/v1/spots` から取る**。

- 43 件を 1 回で取り、`stores/nav.js` に置く
- **`lib/spotCodes.js` は不要になる**([realtime_lora.md §9](realtime_lora.md))。`main.js` の `buildSpotCodeMap()` 呼び出しも消える

---

## 3. 触るもの — 実害のあるバグ

| # | バグ | 直し方 |
| --- | --- | --- |
| 1 | **Pinia 二重初期化**([22 §13-2](../22_current_issues.md))。`app.use(pinia)` の直後に `app.use(createPinia())` があり、**persistedstate プラグインの無い 2 個目で上書きされる** | `main.js` の 1 行を削除 |
| 2 | **`marked` 出力を `v-html` で直挿し**([22 §13-4](../22_current_issues.md))。`token` の中身は LLM 生成物である | **DOMPurify を通す**([chat_sse.md §5.3](../40_api/chat_sse.md)) |
| 3 | **SW のタイルキャッシュが空回り**([22 §13-5](../22_current_issues.md))。`TILE_HOSTS` は OSM 公式だが、`NavMap.vue` が使うのは別ホストで拡張子も無い | **`public/sw.js` の URL 判定を `lib/tiles.js` の実際の URL に合わせる。**作り直さない([ADR-0017](../adr/0017-frontend-incremental-change.md)) |
| 4 | **ハードコードされたバッファ値**(`NavView` の 350 m / 15 m が API 側の値と食い違う: [22 §12-7](../22_current_issues.md))。**2026-08-02 訂正**: 括弧内に「API の 300 m / 10 m」と書いていたが、**foot バッファは [geo.md §5.2](geo.md) で 10 m → 50 m に変更済み**である。いずれにせよフロントは自前の定数を持たない | **manifest の `trigger_radius_m` を読む**([packs_pipeline.md §7.2](packs_pipeline.md))。定数を 2 か所に持たない |
| 5 | **デッド UI**(`RTDoc` の `u` / `h` は供給源が無い: [22 §12-4](../22_current_issues.md)) | **表示を消す。**予報は要求に無い([realtime_lora.md §11](realtime_lora.md)) |

---

## 4. 触るもの — LoRa の decode(1 関数)

[realtime_lora.md §2.2](realtime_lora.md) のバイナリに合わせる。

```js
// lib/loraBridge.js の processLine 内、JSON.parse していた箇所を差し替える
const bytes = hexToBytes(hexData)
if (bytes[0] !== 0x01) return              // 未知のプロトコル版は捨てる
onDataReceived({ packEpoch: bytes[1], codes: Array.from(bytes.slice(2)) })
```

```js
// stores/rt.js — processRtDoc(json) を置き換える
applyDownlink({ packEpoch, codes }, manifest) {
  if (packEpoch !== manifest.pack_epoch) { this.stale = true; return }   // §4 pack_epoch
  manifest.spots.forEach((s, i) => {
    const c = codes[i]; if (c === undefined) return
    this.lastBySpot[s.spot_id] = { w: c >> 4, c: c & 0x0f, at: Date.now() }
  })
}
```

- **`0xF` は「不明」。**overlay を鳴らさない
- **HTTP ポーリング(`startPolling`)は計画フェーズ専用として残す。**観光フェーズでは呼ばない(FR-4.5)
- AT コマンド・Join・Android ブリッジ・シリアル接続は**一切触らない**

---

## 5. 受け入れ条件

| # | 条件 |
| --- | --- |
| 1 | チャットがトークン単位で流れ、**停止ボタンで止まる。止めても見えた旅程が残る** |
| 2 | `ask_user` / `clarify` で**専用フォームが入力欄の直上に出て**、選択肢を押しても自由入力しても同じように進む |
| 3 | **リロードしても画面が完全に戻る**(`GET /thread` 1 回。**専用フォームも残り、重複しない**) |
| 4 | 推薦カードと地図が **provisional で先に描かれ、final で差し替わる** |
| 5 | [元に戻す] が LLM を通さずに版を戻し、**二重クリックが 409 で弾かれる** |
| 6 | パック生成中もチャットが使え、**`partial` が「完了」と表示されない** |
| 7 | `src/assets/POI.json` / `facilities.json` が**存在しない** |
| 8 | **`npm run build` が通る**(CI の最低線: [20 §13](../20_architecture.md)) |

---

## 6. 決定の記録(2026-08-01)

| # | 決定 | 根拠 |
| --- | --- | --- |
| 1 | **`NavView.vue` を分割しない** | [ADR-0017](../adr/0017-frontend-incremental-change.md)(ユーザー指示)。分割線は実機を通してからでないと引けない |
| 2 | **SW は作り直さず URL 判定だけ直す** | 同上。空回りの原因はホスト不一致の 1 点 |
| 3 | ~~チップは既存のメッセージコンポーネントに行を足すだけ~~ → **2026-08-03 差し替え: `ask_user` 専用フォームを 1 コンポーネント足す**(§2.3) | 会話ログ内に回答 UI を置くと**重複描画と古い問いへの回答**が起きた([23_ux_issues.md §6-5 / §6-8](../23_ux_issues.md)) |
| 4 | **フォームが出ていても下の入力欄を塞がない** | G5・FR-3.2。自由入力でも答えられ、質問を無視して別の話も始められる |
| 5 | **undo はボタンから専用 REST を直接叩く** | [chat_sse.md §2.1](../40_api/chat_sse.md)。確実性を要する操作に解釈を挟まない |
| 6 | **進捗はポーリング・非モーダル** | 生成中もアプリが使えること(NFR-4) |
| 7 | **`uuid` の自動注入を削除する** | 消費者がいなくなる。[22 §12-6](../22_current_issues.md) |
| 8 | **POI データをフロントに持たない** | [22 §15-1](../22_current_issues.md)。`GET /spots` から取る |
| 9 | **閾値は manifest から受け取る** | サーバーとフロントで別々の定数を持たない |
| 10 | **予報(`u`/`h`)の表示を消す** | 供給源が存在しないデッド UI |
| 11 | **残骸の削除は優先しない** | [ADR-0017](../adr/0017-frontend-incremental-change.md)。動作に影響しない |

## 7. 採らなかった案

| 案 | 不採用の理由 |
| --- | --- |
| `NavView.vue` を機能別コンポーネントに分割 | [ADR-0017](../adr/0017-frontend-incremental-change.md)。バックエンド全面改修と同時に行うと切り分け不能になる |
| Service Worker を書き直す | 空回りの原因は URL 判定 1 点。作り直す必要がない |
| ~~回答専用のエンドポイントを作らない~~ | **2026-08-04 に転換**: `ask_user` の HITL 化([ADR-0019](../adr/0019-react-main-agent-subagents.md))で回答は実行中ターンに返す必要が生じ、**`POST /chat/answer` を新設**した(自由入力もこのエンドポイントで受ける) |
| **`ask_user` をモーダルで出す** | **生成中もアプリが使えること**(NFR-4)と、質問を無視して別の話を始められること(G5)に反する |
| SSE ではなく定期ポーリング | 最初のトークンまでの時間が伸びる(NFR-3) |
| TypeScript 化 | 差分改修の方針([ADR-0017](../adr/0017-frontend-incremental-change.md))と衝突する。**型は `openapi-typescript` で生成した `types.d.ts` を JSDoc から参照する**にとどめる |
| 状態管理を Pinia から入れ替える | 動いている。入れ替える理由がない |

## 8. 実装時に決めること(設計判断ではない)

| # | 項目 |
| --- | --- |
| 1 | `ask_user` 専用フォームの見た目(既存の Tailwind クラスに合わせる。原則 F3) |
| 2 | 進捗パネルの置き場所(サイドバー内かトースト上か) |
| 3 | SSE パーサの実装(40 行程度。ライブラリを入れるかは実装者判断) |
| 4 | `VITE_API_BASE` の既定値と Vite プロキシの設定 |
