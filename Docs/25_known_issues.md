# 既知の問題インベントリ(ReAct 再構築後)

- 状態: **記録(2026-08-04。ReAct 再構築の完了時点で残っている問題の棚卸し)。同日夜、§1 の 4 件と §4-1 を解消・決着。さらにユーザーの実機テストで **1-5** を新規発見・設計確定(下記)。翌 8/4 夕、旧システム(動画)との対話 UX 比較評価で §6 を新規記録。同日、譲歩文の spot_id 露出(§1.7)を新規記録・設計確定。8/5、§1.7 を実装・レビュー是正まで完了し解消**
- 位置づけ: **「いま何が残っているか」の一覧の正。**[23_ux_issues.md](23_ux_issues.md)(旧・一括プラン方式の系に対する調査記録)の各項目の**現状**も本書で追跡する(23 は記録として凍結)
- 根拠: 2026-08-04 の実機通し確認(新規ユーザーでブラウザ操作。推薦 + HITL 質問 / 旅程作成 / 編集 / undo / QA / 再編集の 6 ターン)+ Codex 実装レビュー + サーバーログ
- 運用: **対応方針は該当設計文書を更新してから決める**(Docs 駆動開発)。解消したらこの文書の該当行を更新する

---

## 1. 実害あり → **全 4 件解消(2026-08-04 夜。実装 + 実機確認済み)**

**確認方法**: 新規ユーザー `fix-verify-0804` でブラウザ通し(推薦 → 起点の HITL 質問 → 旅程作成 → 編集)。コンソールエラー 0 件。

| # | 問題(当時) | 解消内容 | 正となる文書 |
| --- | --- | --- | --- |
| **1-1** | 旅程作成・更新のたびに routes 404(SSE 送出時点で route 行が未コミットで別コネクションから見えない競合)。フロントは再試行しない | **解消**。route はレッグごとに専用の短寿命 session で即時 commit(OSRM 往復は session 外・並列度 4)。`state:itinerary`(final)送出時点で全 `route_id` が GET 可能、が SSE 契約に。フロントは 404 限定リトライ + `allSettled` 部分描画を防御に追加。**実機: 経路 GET ×9 が全件一発 200** | [ADR-0020](adr/0020-routes-early-commit.md) / [geo.md §3.5](30_design/geo.md) / [chat_sse.md §1.2](40_api/chat_sse.md) |
| **1-2** | 編集の再ソルブで頼んでいないスポットの入れ替え(「三崎公園は外して」で牛渡川も外れ 2 件入った) | **解消**。編集は既定で訪問集合を固定(削除保護 + 挿入プール空。並び・時刻の再調整は許す)。再充填は `allow_refill`(明示要求時のみ true)。**実機: 「金峰神社は外して」の diff が削除 1 件のみ** | [ADR-0021](adr/0021-edit-turn-default-lock.md) / [recommendation_planning.md §4.5(7)](30_design/recommendation_planning.md) |
| **1-3** | 仮定前提の旅程に「確定」バッジ | **解消**。「確定」の語を廃止(provisional=「調整中」、final=バッジなし)。`assumptions`(未確認の前提)を版に永続化して SSE に載せ、**「仮の前提あり」チップ**で明示。**実機: v1 に「日付は明日と仮定」チップを確認** | [frontend_nav.md §2.4](30_design/frontend_nav.md) / [chat_sse.md §1.2](40_api/chat_sse.md) |
| **1-4** | 候補カードの所要時間が未確認の既定起点基準(facility ソート順先頭を暗黙採用) | **解消**。既定起点の暗黙選択を廃止。所要時間文は接地起点(既存旅程の起点)があるときだけ。旅程の起点未指定は `precondition_unmet` → **メインエージェントが ask_user で質問**(実機確認)。 | [recommendation_planning.md §3.3](30_design/recommendation_planning.md) / [agent_react_architecture.md §5](30_design/agent_react_architecture.md) |

**このラウンドで判明・積み残した小粒の論点**(いずれも実害小。[90_backlog.md](90_backlog.md)):
- 物理的に時間割が組めない編集での実行可能化フォールバック削除は Concession にならず diff にのみ現れる(ADR-0021 の明記済み例外)
- 旅程がまだ無い段階の推薦に起点を渡す口が無い(所要時間文が出ないのは仕様)
- `edit_itinerary.allow_refill` は guided スキーマ上 required で、既定 false の担保はプロンプトのみ

## 1.5 実害あり → **解消(2026-08-04 深夜。実装・レビュー・実機確認済み)**

| # | 問題 | 調査結果 | 対応 | 正となる文書 |
| --- | --- | --- | --- | --- |
| **1-5** | 「〇〇に行きたいです」と 1 スポットのみ指名しても、複数スポットが組み込まれたプランが返る。ユーザーは当初「他ユーザーの『行きたいスポット』が混入しているのでは」とデータ分離バグを疑った | **データ分離バグではないと確認済み**(コード直読: `repository.py` / `repo.py` の全クエリが `user_id` でスコープ済み、`profiles.user_id` は主キー、そもそも「行きたいスポット」を全ユーザー共有で保存するテーブル自体が存在しない)。実際の原因は `plan_itinerary`(新規作成)がユーザーの指名数に関わらず常にフル ILS で動き、観光地マスタ(43 件・全ユーザー共通・個人データではない)から空き時間を自動充填する**仕様どおりの挙動**だったこと | **解消(`51deed5`)**: `plan_itinerary` に `candidate_spots`(既定空)を追加し、挿入プールを `must_visit ∪ candidate_spots` に限定。実効プール(起終点除外後)が空なら `precondition_unmet`。レビュー(Opus 5 代行)の High 2 件(解 C の空旅程・起終点すり抜け)も是正済み(ADR-0022 追記)。テスト 373 passed。**実機確認(2026-08-04 深夜、新規ユーザー)**: 「明日、道の駅象潟を起点に元滝伏流水に行きたいです」→ エージェントが「他に立ち寄りたい場所は?」と HITL 質問 → 「特になし」→ **旅程は元滝伏流水 1 件のみ**(`v1` current、DB 照合済み)。エージェントは途中の `plan_itinerary` 失敗(候補ゼロ差し戻し)から自律回復して質問に至った | [ADR-0022](adr/0022-plan-turn-explicit-candidate-pool.md) / [recommendation_planning.md §4.5(8)](30_design/recommendation_planning.md) / [agent_react_architecture.md §3.3・§5](30_design/agent_react_architecture.md) |

## 1.6 実害あり → **解消(2026-08-04 深夜。実装・レビュー・実機確認済み)**

| # | 問題 | 詳細 | 対応 |
| --- | --- | --- | --- |
| **1-6** | **`ask_user` の引数不正がターン全体を落とす**(応答なし・保存なし。⑤必達の不変条件が破れる) | 実機で再現(起点名が解決できない発話 → メインが `ask_user` を選択 → クラッシュ)。**3 つの欠陥の連鎖**: (1) `_ask_user_args_schema`(prompts.py)が `kind` と `slot`/`surface` の排他を強制しない平坦なスキーマで、LLM が `kind=clarify` + `slot` 非 null を書ける。pydantic(`AskUserArgs`)の検証だけが排他を持ち、ValidationError が `_dispatch` の包括 except で **recoverable=false の INTERNAL** になる (2) `main_agent.py` がループ打ち切り時に `error_event(stage=tool)` を呼ぶが、`"ask_user"` は SSE 契約の `ErrorStage` 語彙にない (3) その契約外 stage の防御(`events.py` の `invalid_error_stage` warning)が **`extra={"message": ...}` と LogRecord 予約キーを上書きして KeyError** になり、防御自体が例外でターンを殺す。結果: `error(stage=persist, stream_failed)` → `done(message_id: null)`、**ユーザー発話も応答も保存されない** | **解消**(実装 + レビュー是正 M-1/M-2 + Low 4 件): (1) guided スキーマを kind ごとの anyOf 分岐にして排他を強制(**実機 A/B で xgrammar 適合を確認済み** — 8/8 正常・400 なし)+ **ValidationError の recoverable=true 差し戻しを `_dispatch` の包括 except 手前に一般化**(全 Tool の引数契約違反が打ち切りにならない。§10 C6 と整合) (2) `stage` は **ErrorStage 語彙内なら tool 名を保持、語彙外のみ `"main_agent"`**(`resolve_error_stage` ヘルパ) (3) `extra` のキーを `error_message` へ改名。**実機確認: 同一シナリオ(実在しない起点名)でクラッシュせず、起点確認の HITL 質問 → 回答後も done まで到達・応答保存(message_id 203)・クラッシュログ 0 件**。テスト 382 passed |

## 1.7 実害あり → **解消(2026-08-05。実装・レビュー・是正・テスト・実機確認済み)**

| # | 問題 | 原因 | 対応 |
| --- | --- | --- | --- |
| **1-7** | **譲歩メッセージに `spot_id` が露出する**(「必須希望の「spot_014」を旅程に入れられませんでした」が旅程カードと LLM のコンテキストの両方に出る。ユーザー報告 2026-08-04) | 譲歩文(`Concession.message_ja`)の唯一の生成点であるペナルティレジストリ(`predicates.py`)が `args` の生値で f-string を組む。`require` 等の `target` は spot_id であり、この文字列が (a) 旅程ダイジェスト経由で LLM コンテキスト(軌跡 + ③現在の旅程)へ、(b) `state:itinerary`・undo/redo・GET 経由でフロントの譲歩カードへ、いずれも**無変換で**流れる。ダイジェスト整形器(`itinerary_digest.py`)は「spot_id は一切含めない」を謳うが、`message_ja` だけが名前化の対象外だった(有効制約の `translate_constraint_args` は名前化済み)。加えてフロントの `spotName()` 等は名前が引けないとき spot_id へフォールバックする | **3 層で断つ**: (1) **発生源** — `PlanningSpot`/`PredicateSpot` に `name_ja` を追加し、レジストリはメッセージ生成時に spot_id → 表示名へ解決 (2) **整形・送出層** — ダイジェストと `state:itinerary`/REST 送出で `message_ja` 中の `spot_` トークンを表示名へ置換(旧形式の永続化済み譲歩文が undo/GET で再浮上する経路への防御)。ダイジェストの名前フォールバックも spot_id をやめ中立表記へ (3) **フロント** — 名前解決失敗時のフォールバックを「不明な地点」へ、譲歩文に表示フィルタ。なお `clarify` の `options[].value` と `state:candidates` の `spot_id` フィールドは**画面に描画されない機械用の値**なので対象外(契約どおり)。正: [recommendation_planning.md §4.4 経路 1](30_design/recommendation_planning.md) / [agent_react_architecture.md §5・§10 C7](30_design/agent_react_architecture.md) / [chat_sse.md §1.2](40_api/chat_sse.md) / [frontend_nav.md §2.4](30_design/frontend_nav.md)。**→ 解消(2026-08-05)**: 設計どおり 3 層を実装(発生源 = `predicates.py` の 13 述語を表示名化、整形・送出層 = `itinerary_digest.py` の `UNNAMED_SPOT_JA`/`mask_spot_ids`/`mask_concession_list` を ダイジェスト・`state:itinerary`・undo/redo・GET に適用、フロント = `spotDisplay.js` の表示前フィルタ + フォールバック「不明な地点」)。**レビュー(Opus 5 代行。Codex は利用上限 8/9 まで)の指摘 9 件を採用・是正**: ① `spotDisplay.js` の lookbehind 廃止(Vite 7 既定 target の Safari 16.0〜16.3 でモジュール評価時 SyntaxError → アプリ全体が起動不能になる High。`vite build` でバンドルに lookbehind 不在を確認) ② LLM コンテキストへの残経路 2 本(`history.py` の QA 行 spot_id フォールバック、`repository.py` の `itinerary_spot_names` フォールバック)を中立表記化し、機械要約は LLM へ載せる直前に最終マスク ③ `GET /thread` の `itinerary.concessions` マスク(chat_sse.md §1.2 に契約追記) ④ `state:candidates` の `name_ja` フォールバック中立化(同上追記) ⑤ 候補名の表示前マスク(旧形式 meta の `name_ja` に生 id が入るケース) ⑥ `load_spot_names` 軽量メソッド新設 + concessions 空時のロードスキップ + undo/redo が commit 成功後に名前解決失敗で 500 を返さない縮退 ⑦ NavView/PlanView の spot_id フォールバック除去(frontend_nav.md §2.4 の適用) ⑧ ルーターのマスクを検証する契約テスト ⑨ PENALTIES 13 述語の網羅テスト。不採用 3 件(中立表記 3 種は各文書明記の設計どおり / `_display` の fail-open は層 2 が拾う仕様 / IGNORECASE 非対称は露出なし)。あわせて既知の残課題(`main_agent.py` の `CandidateReference` フォールバック)も中立表記化。テスト backend **444 passed** / frontend **49 passed**、ruff クリーン。**実機確認(2026-08-05、スタック再起動後の新規ユーザー)**: 「明日、道の駅象潟を起点に、にかほ市 象潟郷土資料館と万助小屋に必ず行きたい。9 時発 11 時戻り」→ 時間予算に収まらず `require`(spot_044)が違反 → 譲歩発生。**SSE(provisional/final 全 8 版)・`GET /thread`・`GET /api/v1/itinerary`・undo・redo のユーザー可視フィールド計 330 個を機械検査して `spot_` トークン 0 件**。譲歩文は「必須希望の**「万助小屋」**を旅程に入れられませんでした」、AI 応答本文も「「万助小屋」を旅程に組み込むことができませんでした」と表示名。ブラウザで `/app/chat`(リロード復元=`GET /thread` 経路)と `/plan` を実描画し、譲歩カード・旅程行・スポット一覧のいずれにも spot_id なし・コンソールエラー 0 件。**別件として §2-4(譲歩文の重複蓄積)を新規発見** |

## 2. 監視項目(発生条件つき・対処方針は確定済み)

| # | 項目 | 内容 |
| --- | --- | --- |
| **2-1** | **vLLM/xgrammar の guided decoding 不具合**: 「配列要素内の number フィールド」直後に空白トークンを無限出力する(gemma-4-31B で実機再現。max_tokens 増でも解消しない) | `update_profile` は**guided 失敗時に非 guided テキスト指示へフォールバック**して解消済み(`d66d1e6`)。**`main_agent` の `constraints.add[].weight` も同構造で潜在リスク**(簡易スキーマで再現確認済み)。症状(該当ターンで JSON 破損・生成が max_tokens まで走る)が出たら同じフォールバックを適用する。**追記(2026-08-04 深夜、1-6 レビュー中の実測)**: `ask_user` の `options` 配列直前(`reason` の直後)でも同種の空白無限出力が起こり得る — ただし**プロンプトに「options は 2〜4 個」の明記がある実運用構成では 8/8 正常**で、明記を外した合成プロンプトでのみ 6/6 再現。旧 flat・新 anyOf スキーマで差はない(1-6 の修正が原因でも悪化でもない)。破損時は `_call_main_agent` の再試行 1 回 → `main_agent_failed` の縮退で persist 必達は保たれる。監視のみ |
| 2-2 | フォールバック発動時の `update_profile` は約 17 秒かかる(通常 1.4〜5.8 秒) | 発動はバグ発話パターンのときだけ。頻発するようなら guided スキーマから number を外す再設計を検討 |
| 2-3 | **HITL の 10 分待機とリバースプロキシ**: keep-alive は実装済みだが、本番相当のプロキシ配下での長時間待機は未検証 | 実験環境(vite dev + host network)では問題なし。プロキシを挟む構成にしたら確認する |
| **2-4** | **同一の譲歩文が版をまたいで重複蓄積する**(2026-08-05 発見。§1-7 の実機確認中に観測) | 1 ターン内で `plan_itinerary` → `edit_itinerary` × 3 と版が上がるたび、**同じ違反制約に対する譲歩が 1 件ずつ足し込まれ**、v4 では「必須希望の「万助小屋」を旅程に入れられませんでした」が **4 回**並んだ(SSE の `concessions`・`GET /thread`・旅程カードのいずれも 4 件)。露出そのものは無い(表示名で正しく出ている)が、同じ文が 4 回並ぶのはユーザーから見て明確な不具合。**原因は未調査**(版ごとの譲歩を累積しているのか、制約が重複登録されているのかの切り分けが要る)。対処方針も未確定 |

## 3. UI・表示(旧 23 からの残存分の現状)

**再構築で解消したもの**: 旧 §3-5(空でも「希望条件を更新しました」が出る)→ **解消**(`state:profile` は差分があるときだけ送る。実機確認済み)。旧 §5-8(質問フォームの本文と選択肢の食い違い)→ 解消済みのまま。

| 旧 23 の # | 内容 | 現状(2026-08-04) |
| --- | --- | --- |
| §3-1 | 内部 enum(nature/water 等)が応答本文に混ざる | **解消(2026-08-04 夜)**: respond の全面改訂(内部語の禁止を明文)+ 禁止語の事後検査(`find_forbidden_internal_terms`。PreferenceKey/Mobility 等の enum・spot_id・自己言及を検出し degraded 記録。層 1 テスト付き)。実機 4 ターンで露出なし・degraded 0 件([dialogue_style.md](30_design/dialogue_style.md)) |
| §3-2 | プロフィール表示が生 enum(`short_walk_ok / relaxed`) | **未対応**(フロントの `profileSummary` は未変更) |
| §3-6 | 同じ通知が毎ターン重複して積み上がる | 要再確認(今回は差分のあるターンだけ通知が出ており、悪化はしていない) |
| §4-1〜4-9 | レイアウト(スターターカードの重なり・地図パネルの操作性・アニメーション等) | **未対応**(スコープ外のまま) |
| §5-1〜5-4 | 候補カード・旅程行が押せない、本文とカードの二重表示 | カードの操作性は**未対応**。二重表示は [dialogue_style.md](30_design/dialogue_style.md) の新様式で実質軽減(本文=理由・見どころの語り、カード=タグ・滞在目安と役割が分離。旅程は本文で全行復唱しない)。構造対処は未着手 |
| §5-5 | リロードで候補カードの説明文が消える | 要再確認(`meta.presented` の整備で改善した可能性) |
| §5-6〜5-7 | ウェルカム消失・パック ETA | 未対応 |
| §6-1〜6-4 | 言語・表記(ヘッダー "AI Agent by Qwen3" は実態と違うモデル名 — **今回も表示を確認**、ログイン画面英語 等) | **未対応** |

## 4. 仕様判断・文書作業の残り

| # | 項目 | 内容 |
| --- | --- | --- |
| 4-1 | **認証**(旧 [23 §7-1](23_ux_issues.md)): `POST /login` はユーザー名のみで、他人のユーザー名を打てばその人のスレッドが開く | **決定済み(2026-08-04)**: プロトタイプ段階として許容し、[10_requirements.md FR-5.2](10_requirements.md) に明記した。実装変更なし |
| 4-2 | 設計文書への footnote: `update_profile` の guided フォールバック(§2-1)を [agent_react_architecture.md §2](30_design/agent_react_architecture.md) に追記 | **追記済み(2026-08-04)** |
| 4-3 | [24_architecture_as_built.md](24_architecture_as_built.md) の再執筆 | **完了(2026-08-04)** |

## 5. 環境メモ(コード起因でない)

- `frontend/dist/` が root 所有で `npm run build` が permission denied になる(前セッションのコンテナ実行の残留物)。ビルド自体は別出力先で成功確認済み。`sudo chown -R` での是正を推奨
- Codex プラグインは「実質的なコーディングタスクは Codex へ」と促す説明文を持つ。**実装をサブエージェントに委譲する際は指示書の冒頭に Codex 使用禁止を明記すること**(実際に 1 回、役割分担違反が起きた)

## 6. 対話 UX 比較評価での新規観測(2026-08-04 夕)

**経緯**: 旧システムのデモ動画(ChokaiGuidance.mp4)と現行を、新規ユーザー `ux-eval-0804` の同一シナリオ(漠然推薦 → プロフィール回答 → 「鳥海湖をプランに追加して」 → スポット QA)で比較。対話の様式面の後退(報告書調・次の一手の不在・語りの素材不足)は [dialogue_style.md](30_design/dialogue_style.md) に設計として切り出した。以下は**それ以外の個別事象**。

| # | 事象 | 詳細 | 状態 |
| --- | --- | --- | --- |
| **6-1** | **「鳥海湖をプランに追加して」で未提示 6 スポットが混入**(牛渡川・鳥海温泉 遊楽里・奈曽の白滝・金峰神社・元滝伏流水・御浜小屋) | サーバーログ確認: 当該ターン(`bd16b01f`)で `recommend` は実行されておらず、直前ターンの提示候補(釜磯の湧水・丸池様等)とも不一致 → **メインエージェントが `candidate_spots` を自発的に創作して渡した**とみられる。ADR-0022 の実機確認(1-5)は「明示候補に限定」の**ソルバー側**を検証したが、**エージェント側の判断基準**がプロンプトで担保されていなかった。さらに起点未解決の連鎖で**起点が目的地自身(鳥海湖)にフォールバック**し循環旅程になった | **解消(2026-08-04 夜)**: メインエージェントプロンプトに candidate_spots の判断基準を追補(指名時は空・recommend 結果からの転記のみ・創作禁止 — [dialogue_style.md §5-1](30_design/dialogue_style.md))。**実機確認**(`ux-accept-0804`): 同一シナリオで旅程は**鳥海湖 1 件のみ**(v1)、起点はユーザーの宿に解決 |
| **6-2** | **起点を聞く `ask_user` の選択肢が解決不能なカテゴリ名**(「鳥海山麓の宿 / 最寄り駅 / その他」) | チップ回答(`hotel`)は名寄せできず、`plan_itinerary` 8 回の試行錯誤の末に 6-1 のフォールバックへ連鎖。問題の本質は「**答えても解決しない質問**」 | **解消(2026-08-04 夜)**: ガード A7(解決不能な選択肢の送出前除去)+ プロンプトの具体値要件。**実機確認**: カテゴリ語(鳥海山麓の宿/最寄りの駅)とカタログ外の駅名(酒田駅/鶴岡駅)が除去され、エージェントは**本文での質問に自律回復**(解決不能なチップは一切表示されない)。残課題(記録済み・[dialogue_style.md §6](30_design/dialogue_style.md)): 選択肢を言い換えた ask_user 再試行が続きターンが長引く(実測 112 秒。カウンタ非消費は裁定済み)/名寄せ部分一致の誤解決経路 |
| **6-3** | **HITL 回答バブルの転記順序**: 質問チップへの回答(「鳥海山麓の宿」等)が、assistant 応答バブルの**後**に描画される | DB の `seq` は正しい(user 発話 → 回答 → assistant)。ストリーミング中のフロント側の挿入順の問題とみられる(リロード後は正順の可能性 — 要確認)。**2026-08-04 夜の実機でも再観測**(「23歳です…」が推薦応答の後に表示) | **未対応**(フロント。小粒) |
| **6-4** | **候補カードのフェーズバッジに「確定」が残存** | [1-3] の是正(「確定」の語の廃止)は旅程カードのみで、候補カード(`OC_ChatMessage.vue:46`)は未適用 | **解消(2026-08-04 夜)**: provisional=「候補」/ final=バッジなしに変更。実機確認済み |
