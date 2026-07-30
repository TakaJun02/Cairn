# 残タスク(次のセッションの起点)

- 最終更新: 2026-07-31
- 用途: **会話セッションをまたぐときの引き継ぎ。**新しいセッションはこの文書から読み始める

> **現在地(2026-07-31)**
> FR-1(推薦)と FR-2(旅程プランニング)の**方式は決着済み**(ADR-0005〜0008)。旅程計画フェーズのエージェント設計 `30_design/agent_planning_phase.md` は**草案でユーザーレビュー待ち**。
> **まだ実装には入っていない。**ユーザー指示により「Docs/ でアーキテクチャを全部定義してから Codex に委譲する」方針(2026-07-31)。**現時点で Codex への委譲は行わない。**

## 進め方(このプロジェクトの原則)

- Docs 駆動開発。文書 → 承認 → 実装([/CLAUDE.md](../CLAUDE.md))
- 実装・調査は **Codex に委譲**(`--model gpt-5.6-sol`、`--effort` は付けない)。Claude は仕様・設計・レビュー
- ただし**上記の「全部定義してから」が解除されるまでは委譲しない**

---

## A. ユーザーレビュー待ち(最優先)

`30_design/agent_planning_phase.md` §13 の 5 論点。**3 と 5 はデータモデルの形を変える**ので、`data_model.md` を書く前に決着させる。

| # | 論点 | 影響 |
| --- | --- | --- |
| 1 | `plan` の最大手数を 3 にしてよいか(§1.3 P1) | 検証器の複雑さ |
| 2 | ステップ参照の語彙を 3 つに絞ってよいか(§1.2) | 後から追加可能。影響小 |
| 3 | **`answer_qa` を道具に含めるか** | 含めないと「鶴間池ってどんな所?」が `respond` の無根拠回答になる。**スキーマに影響** |
| 4 | undo の UI(§4.2): ボタンか自然言語か両方か | フロント設計 |
| 5 | **旅程を持たない状態での推薦を認めるか**(FR-1 単独の使い方) | **旅程と推薦の外部キー関係が変わる** |

レビューの結果を反映したら `agent_planning_phase.md` の状態を「草案」→「承認」に更新する。

## B. 未執筆の設計文書

ユーザー指示「実装に入る前に Docs/ にアーキテクチャを全て定義する」の残り。**文書名は `20_architecture.md` §14 の Phase 表を正とする。**

| 優先 | 文書 | 内容 | Phase |
| --- | --- | --- | --- |
| **高** | `30_design/data_model.md` | DB スキーマ全体。エージェント設計が要求する状態(`last_candidates` / `asked_slots` / `ask_streak` / `unmodeled_log` / 旅程の version / `turn_metrics`)が出揃ったので、**A の 3・5 が決まり次第まとめて固める** | 3 |
| 高 | `40_api/chat_sse.md` | SSE イベント契約(`agent_planning_phase.md` §6 を正式契約に昇格)+ その他 REST | 1 |
| 中 | `30_design/geo.md` | OSRM(car/foot)と PostGIS の使い方、43×43 距離行列の作り方、`--max-table-size` 前提 | 2 |
| 中 | `30_design/packs_pipeline.md` | 観光フェーズ用パックの生成ジョブ(ADR-0003)。旅程 version との対応 | 2 |
| 中 | `30_design/realtime_lora.md` | リアルタイム情報のシミュレータと狭帯域配送 | 3 |
| 低 | `30_design/offline_field_mode.md` | 観光フェーズのオフライン動作(SW・LoRa ブリッジ) | 4 |
| 低 | `30_design/frontend_nav.md` | Vue 構造、`state` イベントの受け方、地図、NavView 分割。**実機を見ながらの方がよい** | 4 |

**文書名の不整合(要修正):** `agent_planning_phase.md` §12 が `30_design/guidance_phase.md` を参照しているが、`20_architecture.md` §14 では観光フェーズは `offline_field_mode.md` / `packs_pipeline.md` に分かれている。どちらかに寄せる。

## C. データ拡充(実装前に着手可能)

`30_design/recommendation_planning.md` §6 で**実施すると決定済み**。43 POI に 4 フィールドを足す。半自動生成を Codex に委譲し、ユーザーが目視補正する。**B の文書群と独立して進められる**ので、レビュー待ちの間に着手してよい。

- `stay_min`(標準滞在時間)/ `weather_fit`(天候適性)/ `open_hours` + `season`(営業時間・季節)/ `visit_difficulty`(訪問難易度)

## D. 実装フェーズ(A・B 完了後に Codex へ委譲)

`20_architecture.md` §14 の Phase 1 から。委譲時は設計文書を指示書として渡す。

1. `backend/app/` 骨格(core/config/db/llm・ログ・healthz)+ users
2. `domains/conversation/`(`pipeline` / `understand` / `planner` / `executor` / `guards` / `respond` / `context` / `state`)
3. `domains/itinerary/`(`solver` / `penalties` / `ops`)+ `domains/recommendation/`
4. フロントの chat を SSE 化
5. 旧 svc-agent・chromadb・埋め込みサーバ依存の削除

**層 1 自動テストの仕様書は `agent_planning_phase.md` §5 のガードレール表。**実装と同時に書かせる。

## E. 実装時に決める調整項目(設計判断ではない)

`30_design/recommendation_planning.md` §8.2。先に実装を始めてよいが、値・見せ方として残る。

1. 暫定 → 確定の UI の見せ方(実機を見て決める)
2. ペナルティの重み初期値と編集距離の係数 `β`(シナリオ台本で合わせる)
3. ILS のパラメータ(反復回数・shake 件数・打ち切り)。43 地点の厳密解と比較して一度決める
4. 述語の初期実装セット(11 種のうち `weight`/`require`/`exclude`/`last`/`not_consecutive`/`time_window`/`lunch_break` から)
5. `stay_min` の初期値

## F. 文書の整備

- **ADR-0001〜0004 が「提案」のまま。**実装に入る前に「承認」へ更新するか、内容を見直す
- `21_architecture_asis.md` / `22_current_issues.md` は凍結(更新しない)
