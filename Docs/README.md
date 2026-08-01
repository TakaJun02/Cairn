# ドキュメント体系

このプロジェクトは **Docs 駆動開発** で進める。仕様・設計は本ディレクトリの文書で決定し、承認された文書に基づいて実装する(ルールの全文は [/CLAUDE.md](../CLAUDE.md))。

---

## 🚀 はじめて読む人・新しいセッションの読む順序

**まず [90_backlog.md](90_backlog.md) を読む。**現在地と残タスクがそこにある。

**実装に入るなら、この順に読む**(Phase 1 の場合):

| 順 | 文書 | 何が書いてあるか |
| --- | --- | --- |
| 1 | [20_architecture.md](20_architecture.md) | 全体構成・パッケージ構造・依存ルール・Phase 表。**実装の正** |
| 2 | [30_design/agent_planning_phase.md](30_design/agent_planning_phase.md) | 対話エージェントの全設計。**Part II §14〜24 が実装粒度**。§18.2 が**型と enum の正** |
| 3 | [30_design/data_model.md](30_design/data_model.md) | 全テーブルの DDL。**永続化の形の正** |
| 4 | [40_api/chat_sse.md](40_api/chat_sse.md) | SSE とREST の契約。**フロント⇔バックの正** |
| 5 | [30_design/narration_qa.md](30_design/narration_qa.md) | 知識検索サブエージェント |
| 6 | [30_design/recommendation_planning.md](30_design/recommendation_planning.md) | 推薦とソルバーの方式。**§4.4 が述語 17 種の定義の正** |
| 補 | [30_design/understand_node.md](30_design/understand_node.md) | `understand` ノードだけの詳細解説(読み物) |
| 補 | [30_design/agent_patterns_survey.md](30_design/agent_patterns_survey.md) | 外部知見の調査記録 |

**Phase 2(geo / パック / 音声)を実装するなら、この 3 本**(すべて決定稿 2026-08-01):

| 順 | 文書 | 何が書いてあるか |
| --- | --- | --- |
| 1 | [50_operations/osrm.md](50_operations/osrm.md) | 地図データの切り出しと起動。**Phase 1 より先に要る**(§下記) |
| 1' | [50_operations/database.md](50_operations/database.md) | PostGIS + pgvector のイメージと初期化手順 |
| 2 | [30_design/geo.md](30_design/geo.md) | 経路・接近・移動時間行列・沿道 POI。**`travel_times` の意味の正** |
| 3 | [30_design/packs_pipeline.md](30_design/packs_pipeline.md) | パック生成ジョブ・ナレーション・TTS・manifest。**成果物の形の正** |

**Phase 3(リアルタイム / LoRa)と Phase 4(オフライン / フロント)**(すべて決定稿 2026-08-01):

| 文書 | 何が書いてあるか |
| --- | --- |
| [30_design/realtime_lora.md](30_design/realtime_lora.md) | 状況コードの配送・ペイロード・シミュレータ。**LoRa プロトコルの正** |
| [30_design/offline_field_mode.md](30_design/offline_field_mode.md) | 観光フェーズの動作(取り込み・到達判定・再生・タイル) |
| [30_design/frontend_nav.md](30_design/frontend_nav.md) | **フロントの変更インベントリ。**どこを触り、どこを触らないか |

> **注意 1: Phase 2 の一部は Phase 1 の前提である。**Phase 1 の ILS ソルバーは `static.travel_times` を必要とするので、**OSRM の再ビルドと `build-geo` / `build-travel-times` だけは Phase 1 の着手前に済ませる**([90_backlog.md §C-0](90_backlog.md))。
>
> **注意 2: フロントエンドは差分改修に限る**([ADR-0017](adr/0017-frontend-incremental-change.md)、2026-08-01 のユーザー指示)。**`NavView.vue` の分割と SW の作り直しは撤回済み。**触ってよい範囲は [frontend_nav.md](30_design/frontend_nav.md) が正。

> **⚠ 文書中の `spot_id` の例は実データと一致しない**(2026-08-01、実装中に確認)。各文書が例として繰り返し使う「鶴間池 = `spot_012`」「元滝伏流水 = `spot_007`」は**説明用の仮の値**である。実データは **`spot_012` = 元滝伏流水 / `spot_007` = 法体の滝**で、**「鶴間池」は 43 件に存在しない。**例は形を示すためのものなので文書は直さないが、**テストの期待値や検証に例の id をそのまま使わないこと。**

**同じ事実が 2 か所にあるときは、下表の「正」を優先する。**

| 事柄 | どこが正か |
| --- | --- |
| 型・enum(`PredEnum` / `PreferenceKey` / `Slot` / `ToolErrorCode` ほか) | **[agent_planning_phase.md §18.2](30_design/agent_planning_phase.md)** |
| テーブル定義・列・制約 | **[data_model.md](30_design/data_model.md)** |
| API の形・SSE イベント | **[40_api/chat_sse.md](40_api/chat_sse.md)** |
| 述語 17 種の意味とペナルティ | **[recommendation_planning.md §4.4](30_design/recommendation_planning.md)** 経路 1 |
| **経路・移動時間・沿道 POI の意味** | **[30_design/geo.md](30_design/geo.md)** |
| **パック成果物・variant・manifest** | **[30_design/packs_pipeline.md](30_design/packs_pipeline.md)** |
| **地図データの作り方・compose の OSRM** | **[50_operations/osrm.md](50_operations/osrm.md)** |
| **LoRa のペイロードと配信規則** | **[30_design/realtime_lora.md](30_design/realtime_lora.md)** |
| **フロントで触ってよい範囲** | **[30_design/frontend_nav.md](30_design/frontend_nav.md)** |
| 全体構成・依存ルール | **[20_architecture.md](20_architecture.md)** |
| 決定の理由 | **[adr/](adr/)** |

## ADR 一覧

| # | 決定 |
| --- | --- |
| [0001](adr/0001-modular-monolith.md) | モジュラモノリス(1 プロセス) |
| [0002](adr/0002-single-postgres.md) | PostgreSQL 1 台に統合(**2026-08-01: pgvector を発動**) |
| [0003](adr/0003-pack-generation-jobs.md) | パック生成をジョブにする |
| [0004](adr/0004-conversation-pipeline.md) | LangGraph をやめ型付きパイプラインに |
| [0005](adr/0005-itinerary-solver.md) | 旅程は自作 ILS ソルバー(TOPTW) |
| [0006](adr/0006-recommendation-hybrid.md) | 推薦はハイブリッド(決定的候補 + LLM リランク) |
| [0007](adr/0007-preference-elicitation.md) | 選好の引き出しは選択式 + `ask_user` |
| [0008](adr/0008-plan-then-execute.md) | 一括プラン方式(ReAct を採らない) |
| [0009](adr/0009-no-subagents.md) | サブエージェントを持たない(**2026-08-01: 適用範囲を限定**) |
| [0010](adr/0010-understand-bounded-agent.md) | `understand` は 2 Tool の有界エージェント |
| [0011](adr/0011-knowledge-search-subagent.md) | 知識検索だけはサブエージェント(Agent as a Tool) |
| [0012](adr/0012-knowledge-retrieval-pgvector.md) | 知識検索は pgvector + Qwen3-Embedding-8B + Tavily |
| [0013](adr/0013-leg-route-door-to-door.md) | 経路はレッグ単位・車+徒歩を door-to-door の 1 本に(**Phase 2**) |
| [0014](adr/0014-osrm-area-extract.md) | OSRM データを鳥海山エリアに切り出す(32 GB → 1 GB 未満) |
| [0015](adr/0015-pack-asset-composition.md) | パックのアセットは base + overlay の合成 |
| [0016](adr/0016-lora-terminal-driven-batch.md) | LoRa は端末駆動・1 通でパック全スポットを返す(**Phase 3**) |
| [0017](adr/0017-frontend-incremental-change.md) | フロントエンドは差分改修に限る(**Phase 4**。20 §10 を一部撤回) |

---

## 文書の構成

| 文書 | 内容 | 更新タイミング |
| --- | --- | --- |
| `00_project.md` | プロジェクトの目的・やりたいこと・スコープ | 目的が変わったとき |
| `10_requirements.md` | 機能要求・非機能要求 | 機能を足す/削るとき(実装前) |
| `20_architecture.md` | システム全体アーキテクチャ(to-be の正) | 構成を変えるとき(実装前) |
| `21_architecture_asis.md` | 旧アーキテクチャの記録(参照用・凍結) | 更新しない |
| `22_current_issues.md` | 旧構成の問題インベントリ(再設計の根拠、file:line付き) | 更新しない(記録) |
| `30_design/` | コンポーネント別の設計。手段が自明でないテーマは「選択肢比較 → 議論 → 決定稿」の順で書く | 該当コンポーネントの実装前 |
| `40_api/` | フロント⇔バックエンドの API 契約。**ただし `routes` / `packs` / `jobs` の契約は該当設計文書が持つ**([geo.md §3.3](30_design/geo.md) / [packs_pipeline.md §8](30_design/packs_pipeline.md)) | エンドポイントの追加・変更前 |
| `50_operations/` | 起動手順・実験再現手順・運用メモ | 手順が変わったとき |
| `90_backlog.md` | **残タスク。会話セッションをまたぐ引き継ぎ用** | セッションの区切りごと |
| `adr/` | 意思決定記録 (ADR) | 重要な技術判断をしたとき |

## 運用ルール

1. **実装より文書が先。** 新機能・変更はまず該当文書を書き(なければ作り)、レビューを経てから実装する
2. **文書間の重複を避ける。** 同じ事実は一箇所に書き、他からはリンクする
3. **as-is と to-be を混ぜない。** `20_architecture.md` は常に「目指す姿+現在どこまで到達したか」を示す
4. **ADR は上書きしない。** 決定を覆すときは新しい ADR を書き、旧 ADR の冒頭に「superseded by ADR-XXXX」と記す
5. **要求と手段を分離する。** `10_requirements.md` は手段を固定しない。手段の設計では現行(旧)実装を既定とせず、選択肢比較とユーザーとの議論を経て決める(詳細は [/CLAUDE.md](../CLAUDE.md) の「手段の決め方」)

## ADR の書式

`adr/NNNN-slug.md`:

```markdown
# ADR-NNNN: タイトル
- 状態: 提案 | 承認 | 廃止 (superseded by ADR-XXXX)
- 日付: YYYY-MM-DD

## 文脈(何が問題か)
## 決定(何をすると決めたか)
## 理由(なぜ他案でなくこれか)
## 影響(この決定で生じる制約・やること)
```
