# 既知の問題インベントリ(ReAct 再構築後)

- 状態: **記録(2026-08-04。ReAct 再構築の完了時点で残っている問題の棚卸し)。同日夜、§1 の 4 件と §4-1 を解消・決着。さらにユーザーの実機テストで **1-5** を新規発見・設計確定(下記)**
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

## 2. 監視項目(発生条件つき・対処方針は確定済み)

| # | 項目 | 内容 |
| --- | --- | --- |
| **2-1** | **vLLM/xgrammar の guided decoding 不具合**: 「配列要素内の number フィールド」直後に空白トークンを無限出力する(gemma-4-31B で実機再現。max_tokens 増でも解消しない) | `update_profile` は**guided 失敗時に非 guided テキスト指示へフォールバック**して解消済み(`d66d1e6`)。**`main_agent` の `constraints.add[].weight` も同構造で潜在リスク**(簡易スキーマで再現確認済み)。症状(該当ターンで JSON 破損・生成が max_tokens まで走る)が出たら同じフォールバックを適用する。**追記(2026-08-04 深夜、1-6 レビュー中の実測)**: `ask_user` の `options` 配列直前(`reason` の直後)でも同種の空白無限出力が起こり得る — ただし**プロンプトに「options は 2〜4 個」の明記がある実運用構成では 8/8 正常**で、明記を外した合成プロンプトでのみ 6/6 再現。旧 flat・新 anyOf スキーマで差はない(1-6 の修正が原因でも悪化でもない)。破損時は `_call_main_agent` の再試行 1 回 → `main_agent_failed` の縮退で persist 必達は保たれる。監視のみ |
| 2-2 | フォールバック発動時の `update_profile` は約 17 秒かかる(通常 1.4〜5.8 秒) | 発動はバグ発話パターンのときだけ。頻発するようなら guided スキーマから number を外す再設計を検討 |
| 2-3 | **HITL の 10 分待機とリバースプロキシ**: keep-alive は実装済みだが、本番相当のプロキシ配下での長時間待機は未検証 | 実験環境(vite dev + host network)では問題なし。プロキシを挟む構成にしたら確認する |

## 3. UI・表示(旧 23 からの残存分の現状)

**再構築で解消したもの**: 旧 §3-5(空でも「希望条件を更新しました」が出る)→ **解消**(`state:profile` は差分があるときだけ送る。実機確認済み)。旧 §5-8(質問フォームの本文と選択肢の食い違い)→ 解消済みのまま。

| 旧 23 の # | 内容 | 現状(2026-08-04) |
| --- | --- | --- |
| §3-1 | 内部 enum(nature/water 等)が応答本文に混ざる | **今回の通し(6 ターン)では未観測。**respond の書き換えで改善した可能性が高いが、断定には足りない(要再観測) |
| §3-2 | プロフィール表示が生 enum(`short_walk_ok / relaxed`) | **未対応**(フロントの `profileSummary` は未変更) |
| §3-6 | 同じ通知が毎ターン重複して積み上がる | 要再確認(今回は差分のあるターンだけ通知が出ており、悪化はしていない) |
| §4-1〜4-9 | レイアウト(スターターカードの重なり・地図パネルの操作性・アニメーション等) | **未対応**(スコープ外のまま) |
| §5-1〜5-4 | 候補カード・旅程行が押せない、本文とカードの二重表示 | **未対応。**なお二重表示は新 respond の様式(本文に一覧 + カード)でも発生する |
| §5-5 | リロードで候補カードの説明文が消える | 要再確認(`meta.presented` の整備で改善した可能性) |
| §5-6〜5-7 | ウェルカム消失・パック ETA | 未対応 |
| §6-1〜6-4 | 言語・表記(ヘッダー "AI Agent by Qwen3" は実態と違うモデル名 — **今回も表示を確認**、ログイン画面英語 等) | **未対応** |

## 4. 仕様判断・文書作業の残り

| # | 項目 | 内容 |
| --- | --- | --- |
| 4-1 | **認証**(旧 [23 §7-1](23_ux_issues.md)): `POST /login` はユーザー名のみで、他人のユーザー名を打てばその人のスレッドが開く | **決定済み(2026-08-04)**: 研究プロトタイプとして許容し、[10_requirements.md FR-5.2](10_requirements.md) に明記した。実装変更なし |
| 4-2 | 設計文書への footnote: `update_profile` の guided フォールバック(§2-1)を [agent_react_architecture.md §2](30_design/agent_react_architecture.md) に追記 | **追記済み(2026-08-04)** |
| 4-3 | [24_architecture_as_built.md](24_architecture_as_built.md) の再執筆 | **完了(2026-08-04)** |

## 5. 環境メモ(コード起因でない)

- `frontend/dist/` が root 所有で `npm run build` が permission denied になる(前セッションのコンテナ実行の残留物)。ビルド自体は別出力先で成功確認済み。`sudo chown -R` での是正を推奨
- Codex プラグインは「実質的なコーディングタスクは Codex へ」と促す説明文を持つ。**実装をサブエージェントに委譲する際は指示書の冒頭に Codex 使用禁止を明記すること**(実際に 1 回、役割分担違反が起きた)
