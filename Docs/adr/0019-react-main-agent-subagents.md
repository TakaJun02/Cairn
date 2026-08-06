# ADR-0019: ReAct メインエージェント + サブエージェント構成への転換

- 状態: **承認 (2026-08-04、ユーザー判断。同日改訂: `ask_user` を Human-in-the-Loop の通常ツールに訂正)**
- 日付: 2026-08-03 発案(ユーザー指示)/ 2026-08-04 承認
- 関係: **[ADR-0008](0008-plan-then-execute.md)(一括プラン方式)・[ADR-0009](0009-no-subagents.md)(サブエージェント禁止)・[ADR-0018](0018-ask-user-resumable-tool.md)(`ask_user` の中断・復帰方式)を置き換える (supersedes)** / [ADR-0004](0004-conversation-pipeline.md)(状態は毎ターン DB から再構築)・[ADR-0005](0005-itinerary-solver.md)・[ADR-0006](0006-recommendation-hybrid.md)・[ADR-0011](0011-knowledge-search-subagent.md) は**崩さない**
- 設計の本体: [30_design/agent_react_architecture.md](../30_design/agent_react_architecture.md)(決定稿)

## 文脈(何が問題か)

一括プラン方式(ADR-0008)は「LLM が次に何をするかを決めるのは 1 ターン 1 回だけ」を核に、レイテンシの予見性と追跡性を買った。実装を終えて実機で計画フェーズを通した結果([23_ux_issues.md](../23_ux_issues.md))、この核に起因する限界が確認された:

1. **結果を見て直せない。**頼んでいない 6 スポット入りの旅程がそのまま「確定」として出る(§2-1)。plan 実行後に検知・修正する機会が構造的にない
2. **破棄に対するフォールバックがない。**引数 1 項目の不正で手が丸ごと破棄され推薦 0 件(§7-2)、ガードレールが `ask_user` を破棄すると質問自体が消える(§7-3)。一括検証 → 一括実行の方式では、破棄の後に取り返す手が残っていない
3. **前提だった「呼び出し 2〜3 回固定」はすでに崩れている。**知識検索サブエージェント([ADR-0011](0011-knowledge-search-subagent.md))で QA ターンは可変になり、`ask_user` の中断・復帰([ADR-0018](0018-ask-user-resumable-tool.md))でターンをまたぐ反復も既に存在する。現行系は実質「1 周だけ許された ReAct」である

ADR-0008 が ReAct を退けた根拠のうち「31B の多段自律判断は失敗域」は、guided decoding の毎周適用(スキーマで手の形を縛る)と、判断粒度を「次の一手」に限定することで当時の懸念とは条件が変わっている。ADR-0009 は自ら定めた覆す条件 3 つに知識検索が該当し、既に適用範囲を限定済みだった。

## 決定(何をすると決めたか)

**旅程計画フェーズのエージェントを、ReAct メインエージェント + 役割別サブエージェント構成に「完全に作り替える」**(2026-08-03/04 ユーザー指示。**修正ではない**。旧設計文書は廃止し、新設計は [agent_react_architecture.md](../30_design/agent_react_architecture.md) を正とする)。

1. **ターンの構造**: `load_context` → プロフィール更新(LLM 1 回、差分がなければ書かない)→ ReAct メインエージェントのループ → `done` → `respond`(ストリーミング)→ `persist`。**Understand ノードは持たない**(発話の一括 JSON 翻訳という段そのものを廃止)
2. **メインエージェントの Tool**: `recommend` / `plan_itinerary` / `edit_itinerary` / `search_knowledge` / `ask_user` / `done`。毎周 guided decoding で thought + 一手を出す
3. **サブエージェント**: レコメンド(判定 1 回: `ask_user` or `done`→ 既存推薦処理)/ 旅程計画(LLM ループを持たない完全ワークフロー、フロー 1〜4)/ 知識検索(現行を踏襲し `ask_user` を追加)
4. **メインエージェントは `spot_id` を扱わない。**POI は名前空間で扱い、名前⇄id の解決と実在照合はサブエージェントのコードが行う
5. **`ask_user` は Human-in-the-Loop の通常ツールである**(2026-08-04 ユーザー指示で確定)。検索ツールが結果を act に持ち帰るのと同じで、**UI 経由でユーザーに質問し、回答をそのエージェント(メイン / レコメンド SA / 知識検索 SA)の act にツール実行結果として持ち帰る**。**ターンは中断しない** — ターンの処理が回答を待ち(SSE は開いたまま)、回答は `POST /api/v1/chat/answer` で届く。ADR-0018 の中断・復帰機構(`pending_ask` 復帰・次ターン再開)は廃止し、「結果を返す 1 つの Tool・`kind` 2 用途」の核だけ引き継ぐ
6. **会話履歴 = LLM 要約(古い部分)+ 直近 2 ターン生テキスト。**要約は persist 内・done 送出後(クリティカルパス外)に生成する

**個別論点の決着(2026-08-04、ユーザー判断)**: (1) `done` は終了宣言のみで応答は `respond` の別呼び出し / (2) 制約 DSL(述語 17 種)はメインエージェントが書く / (3) 旅程 SA の A/B/C 解選択 LLM は残す / (4) ループ上限は 8 手 + コンテキスト予算 soft 70% / hard 85% / (5) 要約は persist 内・done 後 / (6) 1 ターン複数回の旅程書き換えを許す / (7) **レイテンシ増を受容する** / (8) 引数の部分不正は要素だけ落として実行し報告する / (9) **Understand は廃止**(プロフィール更新は Understand の後継ではなく独立した前段ステップ)。

## 理由(なぜ他案でなくこれか)

- **修正可能性 > 呼び出し回数。**実測された失敗(§2-1、§7-2、§7-3)はいずれも「結果を見てから次を決められれば」回復できる種類のものである。一括プラン方式の利点(呼び出し固定)は、失敗時に取り返せない構造という代償の方が大きいことが実機で確認された
- **語彙と検証の局所化。**タグ 80 語・mobility enum・spot_id をメインのプロンプトから追い出し、それを使うサブエージェントにだけ載せる。語彙の当て外しが対話全体を壊す構造(23_ux_issues §0.3)が解消される
- **`$N` 参照という間接層が不要になる。**前の手の結果は軌跡として LLM が直接読め、次の手の引数に自分で書ける
- **失敗の扱いが一様になる。**「検証で破棄して respond に言い訳を渡す」から「ToolError を結果として差し戻し、メインが次の一手で対処する」へ

## 影響(この決定で生じる制約・やること)

- **LLM 呼び出し回数は確実に増える**(単純推薦ターンで旧 2〜3 回 → 新 6 回前後)。**ターン所要時間の増加は受容する**(論点 7、ユーザー判断)。NFR-3(初回トークン最小化)へは `state: step` の実況・prefix caching 前提のプロンプト構成・`respond` のストリーミング維持で対処する
- **レイテンシの予見性・軌跡の固定長という ADR-0008 の利点は失う。**追跡性は構造化ログに毎周の thought / action / 結果ダイジェストを出すことで補う
- **旧設計文書は廃止**: [agent_planning_phase.md](../30_design/agent_planning_phase.md) / [understand_node.md](../30_design/understand_node.md) は凍結し、冒頭に新文書への誘導を記す。[24_architecture_as_built.md](../24_architecture_as_built.md) は旧実装の記録として凍結し、再実装後に書き直す
- ADR-0008・ADR-0009 の冒頭に **superseded by ADR-0019** と記す
- 契約文書の改訂が必要(**2026-08-04 完了**): [chat_sse.md](../40_api/chat_sse.md)(`state: step` 新設・`plan` 廃止・**`POST /api/v1/chat/answer` 新設**(HITL の回答経路)・質問はターン途中でストリームを開いたまま出す)/ [data_model.md](../30_design/data_model.md)(`history_summary` / `summarized_until_message_id` 追加。`pending_ask` は「表示中の質問」の復元用に限定。migration 0004)/ [narration_qa.md](../30_design/narration_qa.md)(`ask_user` 追加)/ [20_architecture.md](../20_architecture.md) §4
- 実装は [agent_react_architecture.md §16](../30_design/agent_react_architecture.md) の 5 段で Codex に委譲する(CLAUDE.md の役割分担)
