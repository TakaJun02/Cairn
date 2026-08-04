# 残タスク(次のセッションの起点)

- 最終更新: **2026-08-04**
- 用途: **会話セッションをまたぐときの引き継ぎ。**新しいセッションはこの文書から読み始める

> # ✅ 現在地(2026-08-04 深夜)— **ReAct 構成への作り替えが実装・レビュー・実機確認まで完了**
>
> **体制(2026-08-04 改訂)**: 仕様設計 = Fable 5 / 実装 = Sonnet 5 / 実装レビュー = Codex(CLAUDE.md)。この体制で 5 段実装 → Codex レビュー(Critical 2・High 15・Medium 8・テスト穴 7 を全件修正)→ compose 立ち上げ直し → Fable がブラウザ通し確認、まで完了した。
>
> - **コミット**: `0fc5fe7`(段1: 履歴要約+update_profile)→ `bdd4d18`(段2: ReAct ループ)→ `350d78b`(段3: レコメンド SA)→ `571cbd7`(段4: 旅程 SA)→ `790bd31`(段5: ask_user HITL)→ `a14d7b1`/`6e522dc`(レビュー修正)→ `d66d1e6`(実機確認修正)
> - **テスト**: バックエンド 339 passed / DB 統合込み +10 / フロント 26 passed。migration 0004 適用済み
> - **実機確認済みシナリオ**: HITL(質問→チップ回答→**同一ターン継続**→候補5件)/ 旅程作成(仮定の明示)/ 編集+diff / 自然言語 undo(時刻完全復元)/ QA(ナレッジ根拠)/ 編集系発話のプロフィール更新
> - **実機で発見し修正した問題**: ① xgrammar の guided decoding が「配列要素内の number」直後に空白無限ループ(実機再現済み)→ update_profile は guided 失敗時に非 guided フォールバック ② respond クローズドワールド検査が起点施設名を誤検知 → 軌跡テキストの名前を許可語彙に
> - **残る既知問題・監視項目は [25_known_issues.md](25_known_issues.md) が正**(2026-08-04 新設。routes 404 競合 / ILS の編集時入れ替え / xgrammar の number-in-array 監視 / UI 残存分 / 認証の仕様判断、を集約)
> - **文書の後追いは完了(2026-08-04)**: [24_architecture_as_built.md](24_architecture_as_built.md) を ReAct 構成で再執筆(照合済み・食い違い 5 件は同書 §7)。agent_react_architecture.md §2 に guided フォールバックの実装補足を追記。[23_ux_issues.md](23_ux_issues.md) は凍結し 25 へ引き継ぎ
>
> ---
>
> # 🔄 旧・現在地(2026-08-04 昼)— **計画フェーズのエージェントを ReAct 構成へ完全作り替え(設計決定済み・実装未着手)**
>
> **ユーザー指示(2026-08-03/04)により、一括プラン方式を廃止し、ReAct メインエージェント + サブエージェント構成に「完全に作り替える」ことが決まった(修正ではない)。**
>
> - **設計の正: [30_design/agent_react_architecture.md](30_design/agent_react_architecture.md)(決定稿。論点 9 件は 2026-08-04 に全決着)**
> - **[ADR-0019](adr/0019-react-main-agent-subagents.md) 承認**(ADR-0008・0009・**0018** を supersede)
> - **`ask_user` は Human-in-the-Loop の通常ツール**(2026-08-04 ユーザー訂正)。**ターンを中断しない** — UI 経由で質問し、回答(`POST /api/v1/chat/answer`)を**同一ターン内で**呼び出し元エージェントの act に持ち帰る。旧案の `pending_turn`・scope 復帰・SA 再実行は廃止
> - **[agent_planning_phase.md](30_design/agent_planning_phase.md) / [understand_node.md](30_design/understand_node.md) は廃止(凍結)。**実装の参照先にしない
> - **契約文書の改訂も完了(2026-08-04)**: [chat_sse.md](40_api/chat_sse.md)(`state:step` 新設・`plan` 廃止・`searching` 統合・**`POST /chat/answer` 新設**)/ [data_model.md](30_design/data_model.md)(`history_summary`・`summarized_until_message_id` 追加、`pending_ask` は表示中の質問の復元用に再定義 = migration 0004、§6 を LLM 要約方式に)/ [narration_qa.md](30_design/narration_qa.md)(内側 Tool に `ask_user`)/ [20_architecture.md](20_architecture.md) §4 / [frontend_nav.md](30_design/frontend_nav.md) §2.2〜2.3
> - **次の作業**: [agent_react_architecture.md §16](30_design/agent_react_architecture.md) の **5 段で Codex へ実装委譲**(`gpt-5.6-sol` / Effort max。各段の指示書を作ってから)
> - 現行実装(下記 2026-08-02 の成果)は**旧方式のまま動いている**。[23_ux_issues.md](23_ux_issues.md) の §1-2〜1-5(地図消失の競合)等の個別修正は、エージェント外の問題を除き**作り替え後に再評価**する

> # ✅ 現在地(2026-08-02)— **Phase 1〜4 の実装が完了し、ブラウザから通しで動く**
>
> **§C-1 / §D-1 / §E-1 の全項目を実装し、`docker compose` を立ち上げ直してブラウザから通し確認した。**
> ブランチ **`feat/rebuild-implementation`**(develop から分岐、16 コミット)。**まだ develop にマージしていない。**
>
> | Phase | 実装 | 検証 |
> | --- | --- | --- |
> | 1 | 骨格 / DB / 知識索引 / 認証 / ソルバー / 推薦 / 知識検索 / 対話 / SSE / フロント接続 | 実 LLM で推薦・旅程・編集・undo が動く |
> | 2 | geo / voice / パック用原稿 / パック生成ジョブ / API / 進捗 UI | **41 アセットのパックが `ready`(failed 0)** |
> | 3 | realtime(codec / スケジューラ / シミュレータ / MQTT) | codec 往復・フェアユース上限・値の NULL 保持 |
> | 4 | 観光フェーズ / LoRa decode / 実害バグ / 旧構成の削除 | compose が **5 コンテナ**、約 31 GiB 解放 |
>
> **テスト: バックエンド 194 件 + フロント 10 件が通過。**
>
> **実装中に設計文書を改訂した箇所**(いずれも実データ・実測に基づく):
> - **[geo.md §2.3.1](30_design/geo.md)** — 接近の候補に「車道スナップ点」を追加。`access_points` 33 件が 43 地点をカバーせず、**赤田の大仏の徒歩が 394 分**になっていた。`travel_times.car` の平均が **136.8 → 71.6 分**に是正
> - **[geo.md §1.2](30_design/geo.md)** — 1 レッグのセグメントを 1〜2 → **1〜3**(`foot → car → foot` を認める)
> - **[data_model.md §4.7](30_design/data_model.md)** — `app.realtime_simulator_state` を新設
> - **[README.md](README.md)** — 文書中の `spot_id` の例が実データと一致しない旨を注記
> - **[frontend_nav.md §3](30_design/frontend_nav.md)** — foot バッファの記述を 50 m に訂正
>
> **この環境固有の適合**(設計の変更ではない):
> - **`app` と `frontend` を `network_mode: host` にした。**ホストの vLLM が `127.0.0.1:8000` にしか bind しておらず、docker bridge → ホストの通信もこのマシンでは落ちるため。**app のポートは 8080 が別プロセスに占有されているので 8090**
> - **vLLM の xgrammar は JSON Schema の `uniqueItems` を実装していない**(400 になる)。guided decoding で使わないこと
>
> **残っている調整項目は §H。**

> # 🚀 現在地(2026-08-01)— **設計は完了。実装に入れる**
>
> **Phase 1〜4 の設計文書がすべて決定稿になった。**ADR は **0001〜0017**。**未決の設計論点はゼロ。**
> **「Docs/ で全部定義してから実装」(2026-07-31 のユーザー方針)の条件を満たした。**
>
> | Phase | 設計文書 | 状態 |
> | --- | --- | --- |
> | 1 | `agent_planning_phase` / `recommendation_planning` / `data_model` / `chat_sse` / `narration_qa` / `understand_node` | ✅ |
> | 2 | `geo` / `packs_pipeline` / `50_operations/osrm` | ✅(§A1) |
> | 3 | `realtime_lora` | ✅(§A2) |
> | 4 | `offline_field_mode` / `frontend_nav` | ✅(§A2) |
>
> **⚠ 着手前に片付けることが 5 件ある(§C-0)。**うち **`enrichment.json` の目視補正はユーザーにしかできない**(§B)。他の 4 件は Codex に委譲できる。
> **⚠ Phase 2 の一部は Phase 1 の前提である。**ILS ソルバーは `static.travel_times` を必要とするので、**OSRM の切り出しと `build-geo` / `build-travel-times` は Phase 1 の着手前**に済ませる(§C-0 項目 5)。
>
> **2026-08-01 の変更**: ① NFR-7(計測可能性)を削除(§A0-3) ② 会話履歴の構築方式を決定(§A0-3) ③ 知識検索をサブエージェント化(§A0-5) ④ **Phase 2 を全面設計**(§A1) ⑤ **Phase 3・4 を全面設計。フロントは差分改修に限ると決定**(§A2)

---

## A0. 決着済み — エージェント設計(2026-07-31)

**17 論点をまとめて決着させた。**記録は [agent_planning_phase.md §23](30_design/agent_planning_phase.md)、改訂の理由と採らなかったものは同 **§24**。

**設計を変えた主なもの**

| 変更 | 理由 | 反映先 |
| --- | --- | --- |
| **`understand` のフィールド順を入れ替えた**(`references` → 抽出系 → `intent` → `plan`) | EMNLP 2024 の実測: **JSON のキー順が生成順を強制する。**改訂前は「2 番目のやつ」を解決する前に `plan` を書かせていた | §3.1 / §3.1.1 |
| **`selection_hints` を追加** | 経路 3 の行き先がなく、無言破棄禁止の不変条件が数えられなかった | §3.1 |
| **`constraints` を旅程行に永続化 + `constraints_remove` を追加** | ターン限りだと過去の要望が再ソルブで消える。永続化する以上、取り消せないと制約が張り付く | **§4.4** |
| **`ToolError` 型を追加** | executor のスキップ / 中止の判断材料が「例外が飛んだか」だけでは粗かった | §18.2 |
| **P4(`$N` 検証)を強化** | 現行方式は **LLMCompiler**(ICML 2024)型。型整合・戻り値の種類・循環検出が抜けていた | §1.3 |
| **反復ループ対策を縮退表に追加** | vLLM issue #40080: **`gemma-4-31B` で構造化出力時に顕著** | §9 |
| **プロンプトの並び順を規定** | prefix cache と "lost in the middle" に同時に効く | §7.1 |
| **`plan_itinerary` の `days` 不足時は仮定して実行** | 質問だけを返さない(G4 と同じ理由) | §20.2 |
| **`persist` は `respond` の成否に関わらず走る** | §1.4 と §9 が矛盾していた | §16.6 |
| **制約をターン全体の値に移した** | 二重記述を避け、将来の分割を安くする | §18.1 |

**採らなかったもの**(理由は §24.2): 検証失敗時の再計画 / 手の並列実行 / LLM 呼び出しの並列化 / 自由記述の思考欄 / 常時 self-consistency・LLM critic / span の先行導入。

**[ADR-0009](adr/0009-no-subagents.md) を起こした** — サブエージェントを持たない。逐次計画タスクで単一エージェント比 **−70.0%**、MAST の失敗率 **41〜86.7%** という実証が根拠。**覆す条件も ADR に明記した。**

### A0-2. `understand` に聞き返しを持たせた(ユーザー提案、[ADR-0010](adr/0010-understand-bounded-agent.md))

**`understand` を `ask_user` と `done` の 2 Tool だけを持つ有界エージェントにした。**発話が理解できないとき、**plan を出す代わりに聞き返す**。

- **なぜ Tool ではなくノードの分岐か**: 参照が解けていないのに plan を書かせるのは矛盾している。**理解できていないなら、そう言うのが先**([agent_planning_phase.md §3.4.1](30_design/agent_planning_phase.md))
- **ADR-0008(ReAct を採らない)と矛盾しない**: LLM 呼び出しは増えず(2〜3 回のまま)、ターン内にループがなく、判断は 2 択 1 回。グラフに増えるのは片道の辺 1 本(E6)
- **ガードレール G6〜G9 を追加**: 選択肢を 2〜4 個具体的に出せないなら聞かない / 同じ曖昧さを 2 回聞かない / 連続は 1 ターンまで / plan を出せるなら聞かない
- **スレッド状態が 3 つ増えた**: `pending_clarification` / `resolved_ambiguities` / `clarify_streak` → **`data_model.md` に反映が要る**
- **最大のリスクは「聞きすぎ」。**G6〜G9 と、**§7 の会話履歴(第一防衛線)**で抑える。計測はしない(A0-3)

### A0-3. NFR-7(計測可能性)を削除し、会話履歴の構築方式を決めた(2026-08-01)

**NFR-7 を要求から削除した。**研究データを DB に貯めて取り出す機能は作らない。廃止したもの: `turn_metrics` / `unmodeled_log` テーブル、`export-metrics` CLI、SSE `done` の `metrics`、[recommendation_planning.md §7](30_design/recommendation_planning.md) の「層 0 システム計測」。**残るのは構造化ログ(stdout)だけ**で、縮退の明示は NFR-5 が引き続き要求する。反映済み: `10_requirements.md` / `20_architecture.md` / `agent_planning_phase.md`(§10 を全面改訂)/ `recommendation_planning.md` / `understand_node.md` / ADR-0004・0006・0008・0009・0010。**NFR の番号は詰めていない**(NFR-8・NFR-9 への参照があるため)。

**会話履歴を「近いほど詳しい 3 層」で構築すると決めた**([agent_planning_phase.md §7](30_design/agent_planning_phase.md))。直近 3 ターンは user も assistant も生テキスト、それ以前は user 発話は生 + assistant はイベント要約 1 行、予算(4,000 トークン)超過分は古い順に破棄。**要約 LLM は呼ばない**(イベント要約は `messages.meta` からコードが組み立てる)。

- **`understand` にも assistant の生テキストを渡す**(当初案の非対称をやめた)。「そこ、混むって言ってたよね」のような**応答本文への指示語**は構造化状態では解けないため
- **会話履歴と `last_candidates` は別物として両方持つ。**履歴は LLM が読む文脈、`last_candidates` は**コードが `spot_id` を検証する語彙**
- 副次効果として **ADR-0010 の聞き返し発火率が下がる**(履歴が第一防衛線)

### A0-4. `data_model.md` の設計判断を確定させた(2026-08-01)

執筆前に議論して決めた 6 点。**これで `data_model.md` は執筆のみになった。**

| # | 決定 |
| --- | --- |
| 1 | **`spots` と `facilities` を 1 テーブルに統合**し `kind` で区別。根拠: 両者のフィールドが完全一致し、`spot_id` 空間も共有していて衝突ゼロ(POI 30 + 施設 13 = 43) |
| 2 | **タグ語彙を 2 層にする。**選好語彙(15〜20 語、`interests` のキーはここだけ)と生タグ(80 語、検索・説明用)を対応表で結ぶ。根拠: **80 語のうち 48 語(60%)が 1 地点にしか付いていない** |
| 3 | **1 ユーザー 1 旅程、1 ユーザー 1 スレッド。**profile / 旅程 / 履歴 / `presented_spot_ids` のスコープがすべて揃う |
| 4 | **undo は旅程の版を戻す操作**(メッセージの再送ではない)。**追記のみ + 現在位置ポインタ + `parent_version`** で表現し、履歴は消さない。redo が副産物として得られる |
| 5 | **NFR-7 削除**(§A0-3) |
| 6 | **実験条件(A/B arm)は持たない。**リランク ON/OFF は設定 1 個 |

**私が決めて書く分**(異論が出れば直す): `itineraries` は JSONB スナップショット / 制約は旅程行の JSONB 配列で **id を version コピー時に維持** / 旅程がない間の一時制約はスレッド行 / スレッド状態 8 個は**列に分ける** / 旅程内の時刻は `date` + **その日 00:00 からの分(整数)** / enrichment はソルバーが読む値を列・根拠を JSONB / `static.travel_times` に 43×43×2 mode / `profiles` は 1 ユーザー 1 行 JSONB(`profile_events` は NFR-7 削除により**不要になった**)/ 多言語 JSONB はデータとして残しランタイムは ja のみ / DDL は Alembic・データはシード CLI。

### A0-5. 知識検索をサブエージェント化した(ユーザー指示、2026-08-01)

**`answer_qa` を `search_knowledge` に置き換え、独立したサブエージェント(Agent as a Tool)にした。**[ADR-0011](adr/0011-knowledge-search-subagent.md) / [ADR-0012](adr/0012-knowledge-retrieval-pgvector.md) / [narration_qa.md](30_design/narration_qa.md)。

**きっかけ**: ユーザーから **埋め込みサーバ(`Qwen/Qwen3-Embedding-8B`、4096 次元)は常時利用可能**であること、**`.env` に `tavily_APIkey` を追加した**ことが示された。設計は先行実装 [TakaJun02/sarutahiko](https://github.com/TakaJun02/sarutahiko) を参照した(コードを直接読んで確認)。

| # | 決定 |
| --- | --- |
| 1 | **Agent as a Tool。**メインから見た Tool は 1 つ(`search_knowledge`)。**道具の数は 5 のまま** |
| 2 | **返すのは検索結果ではなく回答**(`answer_ja` + `sources` + `coverage`)。ただし**第二の話者にはしない** — `respond` の素材である |
| 3 | **回数上限を置かず、コンテキスト予算で縛る**(soft 70% / hard 85%)。「最大 3 周」のような固定回数は**難しい質問だけを体系的に失敗させる** |
| 4 | サブの Tool は **`semantic_search` / `lexical_search` / `get_document` / `web_search`(Tavily)/ `answer`** |
| 5 | **pgvector を同一 Postgres に足す**(ADR-0002 が用意した発動条件)。**データストアは増やさない** |
| 6 | **Web 検索の結果は `spot_id` の供給源にしない**(クローズドワールドの外) |

**[ADR-0009](adr/0009-no-subagents.md) の適用範囲を限定した。**同 ADR が自ら書いた「覆す条件」3 つすべてに該当したため。**メインの `understand` → `act` → `respond` を分けない方針は変わらない。**

**実データから見つけた根拠**: 知識 MD 118 本を `##` で割ると **`## 概要` が 115 ファイル・`## アクセス` が 58 ファイル**に現れ、「あがりこ大王」の `## アクセス` チャンクは本文に「あがりこ大王」を 1 度も含まない。→ **埋め込み対象を `title + heading + 本文` にすることで解消**(MD の書き直しは不要)。

**代償**: QA ターンの LLM 呼び出しが 2〜3 回固定ではなくなる。SSE の `state{kind:"searching"}` で実況して体感を保つ。

**実装後に一度だけ測るもの**(§24.3): フィールド順入れ替えの効果 / guided decoding の有無 / ILS の最適性ギャップ / prefix cache のヒット率。
**評価集合はシナリオ台本(層 2)から派生させ、別途 authoring しない。**

---

## A1. 決着済み — Phase 2 の設計(2026-08-01、Opus 5 が単独で決定)

**ユーザー指示: 「Phase 1 は議論して決めた。Phase 2 は Opus 5 が設計・仕様を全て決定する」**(2026-08-01)。3 本の設計文書と 3 本の ADR を起こし、**Phase 2 に未決の論点は残っていない。**

| 文書 | 状態 |
| --- | --- |
| [30_design/geo.md](30_design/geo.md) | **決定稿。**決定 17 件は同文書 §9 |
| [30_design/packs_pipeline.md](30_design/packs_pipeline.md) | **決定稿。**決定 22 件は同文書 §13 |
| [50_operations/osrm.md](50_operations/osrm.md) | **決定稿。**決定 9 件は同文書 §8 |

### A1-1. 経路を「レッグ単位・door-to-door」にした([ADR-0013](adr/0013-leg-route-door-to-door.md))

**きっかけは仕様の破れを見つけたこと。**旧実装は「車で駐車場まで → 徒歩で滝へ」を分割していたが、**徒歩ぶんが旅程の時間に入っていなかった**。時間割付き旅程([recommendation_planning.md §4.0](30_design/recommendation_planning.md))を採った以上、これは直さなければならない。

| # | 決定 |
| --- | --- |
| 1 | **1 レッグ = door-to-door の 1 本**(内部に car / foot の 1〜2 セグメント) |
| 2 | **`static.spot_approach`(43 行)を新設**し、「車でどこまで入れるか」を事前に解く。**実行時に判定しない** |
| 3 | **`travel_times` は door-to-door**(両端の徒歩を含む)。`car` 行列に欠損があればコマンドを失敗させる |
| 4 | **`car_to_trailhead` / `return_to_origin` を削除**([20 §6](20_architecture.md) で保留されていた論点への回答) |
| 5 | **`app.routes` はレッグ単位・`params_hash` で冪等・`params` に `osrm_build` を含める**(地図を作り直すと経路キャッシュが自動で無効化される) |
| 6 | **旅程の時間計算は DB だけを使う。**OSRM が落ちても旅程は作れ、失われるのは地図の線だけ |
| 7 | 沿道 POI は **PostGIS 1 クエリ**。`nearest_idx` を **`route_position`(0〜1)** に置換。foot バッファは 10 m → **50 m** |

**`static.access_points` の DDL がどこにも無かった**ことが判明した(旧構成は GeoJSON 直読み)。geo.md で定義した。

### A1-2. OSRM データを鳥海山エリアに切り出す([ADR-0014](adr/0014-osrm-area-extract.md))

**実測: `car` 15 GB + `foot` 17 GB = 32 GB**(日本全国)。しかも**作り方がリポジトリのどこにも書かれていない**([22 §8-6](22_current_issues.md))ので、クリーン clone から起動できない(NFR-2 違反)。

| # | 決定 |
| --- | --- |
| 1 | **bbox `139.55,38.80,140.60,39.65`**(43 地点の外接矩形 + 各辺 20 km)。**32 GB → 1 GB 未満・生成 10 分** |
| 2 | 元データは Geofabrik の **`tohoku`**(全国版ではない) |
| 3 | 手順を **`scripts/build_osrm.sh`** にする |
| 4 | **`backend/data/map/BUILD`(ビルド識別子)を Git で追跡する。**経路キャッシュの無効化キーになる |
| 5 | compose の healthcheck を**実際の `/route` 照会**にし、`--max-table-size 200` を明示する |

**副産物として `.gitignore` の不具合を 1 つ見つけた**: `.env*` が **`.env.example` も無視している**。このままでは Phase 1 の C-0 項目 3(「`.env.example` を追跡して全キーに説明を付ける」)が成立しない。`!.env.example` を足す。

### A1-3. パックのアセットを base + overlay の合成にした([ADR-0015](adr/0015-pack-asset-composition.md))

旧構成の状況 variant は**排他 5 種**で、「雨で、しかも混雑」が表現できなかった。**実測すると成果物はすでに「本編 54 秒 + 注記 5 秒」**になっていたので、設計をその実態に合わせる。

| # | 決定 |
| --- | --- |
| 1 | **`base` 1 本 + overlay 4 本**(`weather_cloudy` / `weather_rain` / `congestion_mid` / `congestion_high`)。天気と混雑は**独立した軸**として重ねる |
| 2 | 生成対象は **`pack_assets.role`**(`visit` = 5 本 / `pass_by` = base のみ)。7 箇所に散っていた暗黙ルール([22 §5-5](22_current_issues.md))を列にする |
| 3 | **`pass_by` の base は 20〜40 秒。**時速 40 km で 300 m バッファの通過は約 27 秒しかない |
| 4 | **再生規則(`playback_rules`)を manifest にデータとして載せる。**フロントのコードに書かない |
| 5 | **Phase 3 への制約**: LoRa ダウンリンクは**天気と混雑を独立したコードとして送る**(単一の「状況コード」にしない) |

### A1-4. 保留されていた論点への回答

| 論点(出どころ) | 回答 |
| --- | --- |
| **`car_to_trailhead` / `return_to_origin` を実装するか削るか**([20 §6](20_architecture.md)) | **削る。**データから決まる挙動 + `destination` が持つ(A1-1) |
| **雨天代替行程を事前計算して同梱するか**([recommendation_planning.md §4.2](30_design/recommendation_planning.md)) | **しない。**FR-4.3 は案内内容の変化であって行程の差し替えではない。組合せが爆発し、端末 UI(Phase 4)に強く依存する。**代わりに雨 overlay が「近くの雨に強い場所」を 1 件だけ挙げる**([packs_pipeline.md §2](30_design/packs_pipeline.md)) |
| **variant の確定セット** | **base + overlay 4**(A1-3) |
| **旅程 version とパックの対応規則** | **冪等キーは `(user_id, itinerary_version, options)`。`route_id` は含めない。**`partial` / `failed` の再要求は同じ `pack_id` で再開する |
| **43×43 距離行列の生成タイミング** | **`build-geo` → `build-travel-times` の 2 段。**再計算は明示操作(`--force`)のみ |
| **OSRM の leg 取得の並列化** | **`asyncio.gather` + Semaphore(8)、20 秒で打ち切り、残りは `route_id: null`** |
| **OSRM 障害時の直線距離係数** | **持たない。**時間は行列にあるので、偽の線を引いて得るものがない |

### A1-5. Phase 1 の文書に入った差分

**[data_model.md](30_design/data_model.md) を改訂した**(決定稿のまま、Phase 2 の決定を反映):

- `static.access_points` / `static.spot_approach` を新設(§2.6)
- `travel_times` を **door-to-door** と定義、`foot` の閾値を **30 分 / 2.5 km** に確定(§3)
- `app.routes` をレッグ単位に変更(`waypoints_info` 列は廃止)、`pack_jobs` に `user_id` / `itinerary_version` / `params_hash`、`pack_assets` に `role` / `duration_s` / `bytes` を追加(§4.7)
- 決定の記録に 18〜22 を追加(§8)

---

## A2. 決着済み — Phase 3・Phase 4 の設計(2026-08-01、Opus 5 が単独で決定)

**ユーザー指示(2026-08-01)**: 「LoRaWAN に関わるところは今回のシステムであまり拘るつもりはないので、Opus 5 が適切に仕様と設計を決定して実装に移れる状態にしてください」/ 「**フロントエンド UI は、現在の状態から変更しなくて良い部分は変更しないでください。**変更や追加する必要がある部分(`ask_user` 用の入力欄など)があれば実装して OK です」

| 文書 | 状態 |
| --- | --- |
| [30_design/realtime_lora.md](30_design/realtime_lora.md) | **決定稿。**決定 13 件は同文書 §10 |
| [30_design/offline_field_mode.md](30_design/offline_field_mode.md) | **決定稿。**決定 12 件は同文書 §10 |
| [30_design/frontend_nav.md](30_design/frontend_nav.md) | **決定稿。**決定 11 件は同文書 §6 |

### A2-1. LoRa は端末駆動・1 通で全スポット([ADR-0016](adr/0016-lora-terminal-driven-batch.md))

**2 つの物理的制約が設計を決めた。**

| 制約 | 帰結 |
| --- | --- |
| **LoRaWAN Class A** はダウンリンクを uplink 直後の受信ウィンドウでしか届けられない | **サーバー push は原理的に作れない。**端末が聞き、サーバーが答える |
| **TTN フェアユースの下りは 10 通 / 日** | 現行の「1 スポット 1 往復」では **10 か所で 1 日ぶんを使い切る** |

| # | 決定 |
| --- | --- |
| 1 | **1 通のダウンリンクでパックの全スポットを返す。**`[ver, pack_epoch, code_0 … code_{N-1}]` = **2 + N バイト**(20 スポットで 22 バイト。AS923 の上限 51 バイトに収まる) |
| 2 | **索引を送らず `manifest.spots` の順に並べる。**1 スポット 1 バイト(上位 4 bit = 天気 / 下位 4 bit = 混雑) |
| 3 | **`0xF` = 不明。**値が無いことを表現できる(旧実装の乱数捏造[22 §6-2](22_current_issues.md)が構造的に不可能になる) |
| 4 | **`pack_epoch`(1 バイト)で並びの一致を保証する。**不一致ならコードを捨てる |
| 5 | **フェアユースの上限は `app.lora_downlinks`(DB)で数える。**プロセス内カウンタは再起動で消える |
| 6 | シミュレータはシナリオ JSON + CLI(`rt-load` / `rt-start` / `rt-set` / `rt-show`) |
| 7 | **端末側の変更は decode 1 関数だけ。**AT コマンド・Join・Android ブリッジは触らない |

**「拘らない」の具体化**: 適応的スケジューリング・ACK 再送・差分送信・予報の配信は**すべて採らない**(同文書 §11)。

### A2-2. フロントエンドは差分改修に限る([ADR-0017](adr/0017-frontend-incremental-change.md)、ユーザー指示)

**[20 §10](20_architecture.md) の一部を撤回した。**

| やらないこと(撤回) | 理由 |
| --- | --- |
| **`NavView.vue`(2,119 行)の分割** | バックエンド全面改修と同時に行うと**不具合の切り分けができなくなる**。正しい分割線は実機でオフラインを通してからでないと引けない |
| **Service Worker の作り直し** | 空回りの原因は **`TILE_HOSTS` の不一致 1 点**([22 §13-5](22_current_issues.md))。URL 判定を直せば済む |
| 動いているモジュールへの手入れ | `NavMap` / `audioManager` / `usePosition` / `geoutils` / `loraBridge`(AT 部)/ `useNavWindow` |
| 残骸の削除 | `PlanView` / `PlanForm` / `counter.js` / `icons/*`。**消してよいが優先しない** |

**触るのは 2 種類だけ**([frontend_nav.md §2・§3](30_design/frontend_nav.md)):

- **接続の差分**: SSE 化 / Bearer 認証 / **`ask_user`・`clarify` のチップ(新規)** / 旅程カードと undo ボタン / パック進捗パネル / `GET /spots`(フロントの POI コピー廃止)/ LoRa decode
- **実害のあるバグ**: Pinia 二重初期化 / DOMPurify / SW のタイルホスト / 閾値の二重定義 / 供給源のないデッド UI

### A2-3. 観光フェーズのオフライン動作([offline_field_mode.md](30_design/offline_field_mode.md))

| # | 決定 |
| --- | --- |
| 1 | **観光モードは明示的に切り替える**(`navigator.onLine` を当てにしない)。**モード中は HTTP を一切呼ばない**(FR-4.5 の抜け道を作らない) |
| 2 | manifest は **localStorage**、音声と経路は **Cache Storage**(既存 `sw.js` が `packs-` プレフィックスと Range に対応済み) |
| 3 | **取り込みの完了を自己検証する**(「入ったつもりで入っていない」を現地で気づかない事故を防ぐ) |
| 4 | 到達判定の閾値は **manifest の `trigger_radius_m`** から受け取る(定数を 2 か所に持たない) |
| 5 | 再生は **base → overlay** を既存の再生キューに積むだけ。**雨かつ混雑なら 2 本鳴る** |
| 6 | **音声が欠けていればテキストを字幕で出す**(部分成功のパックでも使える) |
| 7 | **室内で検証できる項目(3〜6)と実機が要る項目(機内モード通し・タイル)を分けた**(同文書 §9) |

---

## 進め方(このプロジェクトの原則)

- Docs 駆動開発。文書 → 承認 → 実装([/CLAUDE.md](../CLAUDE.md))
- 実装・調査は **Codex に委譲**(`--model gpt-5.6-sol`、`--effort` は付けない)。Claude は仕様・設計・レビュー
- **調査の成果物は Claude が一次資料で検証してから採用する**(2026-07-31 の調査では、Codex 報告 25 項目のうち **3 件に食い違い**があった)
- ~~ただし「Docs/ で全部定義してから」が解除されるまでは実装を委譲しない~~ → **✅ 2026-08-01 に条件を満たした。**Phase 1〜4 の設計文書がすべて決定稿になったので、**実装の委譲を開始してよい**
- **委譲の単位は §C-1 / §D-1 / §E-1 の「順」を 1〜2 個ずつ。**1 回で全部投げない(成果物のレビューが破綻する)

---

## A. 設計文書 — **全部揃った(2026-08-01)**

**文書名は [20_architecture.md §14](20_architecture.md) の Phase 表を正とする。**各文書は「書く」だけでなく**その文書で決める設計判断**を持っている。

| 優先 | 文書 | その文書で決めること | Phase |
| --- | --- | --- | --- |
| ~~高~~ | ~~`30_design/data_model.md`~~ | **✅ 決定稿 2026-08-01。**決定 15 件は同文書 §8 に記録 | **1** |
| ~~高~~ | ~~`40_api/chat_sse.md`~~ | **✅ 決定稿 2026-08-01。**決定 15 件は同文書 §6 に記録。**副作用: `state` に `kind:"clarify"` を追加**(ADR-0010 の反映漏れ)、**`users.api_token` を `data_model.md` に追加** | **1** |
| ~~高~~ | ~~`30_design/narration_qa.md`~~ | **✅ 決定稿 2026-08-01。**知識検索を**サブエージェント化**([ADR-0011](adr/0011-knowledge-search-subagent.md))し、**pgvector + Qwen3-Embedding-8B + Tavily の 4 Tool**([ADR-0012](adr/0012-knowledge-retrieval-pgvector.md))。決定 16 件は同文書 §12 | **1** |
| ~~中~~ | ~~`30_design/geo.md`~~ | **✅ 決定稿 2026-08-01。**決定 17 件は同文書 §9。**[ADR-0013](adr/0013-leg-route-door-to-door.md)** | **2** |
| ~~中~~ | ~~`30_design/packs_pipeline.md`~~ | **✅ 決定稿 2026-08-01。**決定 22 件は同文書 §13。**[ADR-0015](adr/0015-pack-asset-composition.md)** | **2** |
| ~~中~~ | ~~`50_operations/osrm.md`~~ | **✅ 決定稿 2026-08-01。**決定 9 件は同文書 §8。**[ADR-0014](adr/0014-osrm-area-extract.md)** | **2** |
| ~~中~~ | ~~`30_design/realtime_lora.md`~~ | **✅ 決定稿 2026-08-01。**決定 13 件は同文書 §10。**[ADR-0016](adr/0016-lora-terminal-driven-batch.md)** | **3** |
| ~~低~~ | ~~`30_design/offline_field_mode.md`~~ | **✅ 決定稿 2026-08-01。**決定 12 件は同文書 §10 | **4** |
| ~~低~~ | ~~`30_design/frontend_nav.md`~~ | **✅ 決定稿 2026-08-01。**決定 11 件は同文書 §6。**[ADR-0017](adr/0017-frontend-incremental-change.md)** | **4** |

**✅ 設計文書は全部揃った(2026-08-01)。**執筆順は `data_model` → `chat_sse` → `narration_qa` → `geo` → `packs_pipeline` → `osrm` → `realtime_lora` → `offline_field_mode` → `frontend_nav` だった。**これ以降に文書を足すのは、実装中に設計との乖離が出たときだけ**([/CLAUDE.md](../CLAUDE.md) の「先に文書を修正して承認を得る」)。

---

## B. データ拡充(A と独立に着手できる)

[recommendation_planning.md](30_design/recommendation_planning.md) §6 で**実施すると決定済み**(必須)。43 POI に 4 フィールドを足す。

**✅ 半自動生成は完了(2026-07-31、Codex)。残るのはユーザーの目視補正。**

成果物: `backend/data/seeds/enrichment/`(`enrichment.json` / `REVIEW.md` / `SOURCES.md`)。**既存の `POI.json` / `facilities.json` は未変更**(マージは目視補正のあと)。

| 検査項目 | 結果(2026-08-01 再確認) |
| --- | --- |
| 件数と `spot_id` | **43 件・過不足なし。**`stay_min` の欠損ゼロ |
| `open_hours` | **知識 MD 由来 9 件のみ**、`no_concept` 20 / `not_found` 14。**推測で埋めていない** |
| 季節閉鎖 | 15 件を検出 |
| `confidence` | `medium` 37 / **`low` 6**(spot_017 一ノ瀧神社 / spot_019 鳥海山大物忌神社 / spot_036 花立牧場公園 / spot_004 家族旅行村 / spot_023 西浜コテージ村 / spot_041 猿倉温泉 鳥海荘) |
| `weather_fit` | `rain_poor` 20 / `indoor` 11 / **`rain_unsafe` 8** / `rain_ok` 4。`rain_unsafe` は安全 pre-filter で除外される値なので**要確認** |
| `visit_difficulty` | `no_walk` 18 / `short_walk` 17 / `hike` 7 / `long_walk` 1 |

> **⚠ 2026-08-01 に見つけた不整合(対処済み)**: `data_model.md` の当初の CHECK は `weather_fit` 3 値・`visit_difficulty` 3 値だったが、**実データには `indoor` / `rain_unsafe` / `long_walk` があり、そのままではシードが落ちる**。**実データ側の語彙が正しい**(`rain_unsafe` は安全 pre-filter の入力で `rain_poor` と混ぜられない)ため、[data_model.md §1.4.1](30_design/data_model.md) で CHECK を広げ、順序尺度と `mobility` 対応表を定義した。

**目視補正は `REVIEW.md`(`confidence` 昇順)を上から見る。**優先は ① `low` の 6 件 ② `rain_unsafe` の 8 件 ③ 季節閉鎖を推定で入れたもの。`not_found` の営業時間 14 件は、必要になった時点で埋めればよい(`null` のままでもソルバーは動く)。

## C. Phase 1 実装ガイド(2026-08-01 追加)

> **新しいセッションで実装を始めるときは、ここから読む。**Phase 1 の設計文書は全部揃っている(§A)。
> **どの文書が何の正か**は [README.md](README.md) の表を見ること。矛盾を見つけたらそこで裁定する。

### C-0. 着手前に必ず片付けること(順序の前提)

> **✅ 2026-08-01、5 件すべて着手済み。**残っているのは「アプリのコードが無いとできない部分」だけである(下表の状態欄)。

| # | やること | 状態 |
| --- | --- | --- |
| 1 | **データを `backend/worker/data/` から `backend/data/` へ移す** | **✅ 完了**(`git mv` で履歴を保ったまま 358 ファイル)。`seeds/`(POI 30 + 施設 13 + access_points + メモ)と `knowledge/`(354 MD、うち ja 118)。死コードの `rename_md.py` 3 本は削除。**`map/` は移さず作り直した**(項目 5)<br>**⚠ フロントの `src/assets/POI.json` / `facilities.json` は残してある** — 消すと `spotCodes.js` / `poi.js` の import が壊れてビルドが通らない。**`GET /spots` への差し替えと同時に消す**([frontend_nav.md §2.6](30_design/frontend_nav.md)) |
| 2 | **Postgres イメージに pgvector を足す** | **✅ 完了。**`docker/postgres/Dockerfile` をビルドし、**postgis 3.4.3 / vector 0.8.5**、`vector(4096)` の INSERT、`ST_DWithin(geography)`、`ST_LineLocatePoint` まで実機確認済み([50_operations/database.md §1](50_operations/database.md)) |
| 3 | **`.env.example` と `.gitignore`** | **✅ 完了。**`.gitignore` に `!.env.example` と `backend/data/map/`(+ `!BUILD`)を追加。`.env.example` は全キーに説明つき。**キー名は `Settings` 実装時に突き合わせること**([20 §11](20_architecture.md)) |
| 4 | **`enrichment.json` の目視補正** | **✅ ユーザー確認済み(2026-08-01)。**「これで OK」 |
| **5** | **⚠ OSRM を切り出して再ビルドし、`build-geo` → `build-travel-times` を通す** | **✅ 地図データは完了。**`scripts/build_osrm.sh` で **32 GB → 264 MB**(car 105 MB / foot 159 MB)、所要 9 分。compose も新データを指すよう更新済み。**実機で検証済み**: car / foot のルート、`/nearest`(あがりこ大王 = スナップ 934 m → 駐車場経由と正しく判定)、**43×43 の `/table` が 1 リクエスト 35 ms・欠損ゼロ**([50_operations/osrm.md §4](50_operations/osrm.md))<br>**❌ `build-geo` / `build-travel-times` は未実行** —— `app.cli` がまだ無い。**C-1 の順 1〜2(骨格 + Alembic)の直後、順 5(ソルバー)の前に実行する**<br>**⚠ 旧データ 31 GB(`backend/worker/data/map`)はまだ消していない**([50_operations/osrm.md §6](50_operations/osrm.md)) |

### C-1. 実装の順序と、それぞれの「正」

| 順 | 作るもの | 読む文書 | 完了の判定 |
| --- | --- | --- | --- |
| 1 | **`backend/app/` 骨格** — `core/`(config / db / llm / logging)・`main.py`・`/healthz` | [20 §3](20_architecture.md)(構造・依存ルール)/ [20 §11](20_architecture.md)(設定) | `/healthz` が DB・vLLM・OSRM を**実チェック**して返る |
| 2 | **Alembic + 全テーブル + シード CLI** | **[data_model.md](30_design/data_model.md) 全体** | `init-db` → `seed` で 43 spots・80 タグ・12 選好キー・43×43 travel_times が入る |
| 3 | **知識インデックス** — `index-knowledge` / `validate-knowledge` | [narration_qa.md §11](30_design/narration_qa.md) | 118 文書・400〜600 チャンクが入り、`spot_id` が 43 件に付く |
| 4 | **users + 認証**(Bearer トークン) | [40_api/chat_sse.md §4](40_api/chat_sse.md) / [data_model.md §4.1](30_design/data_model.md) | `POST /login` がトークンを返し、`GET /api/v1/thread` が本人の分だけ返す |
| 5 | **`domains/itinerary/`** — ILS ソルバー・述語レジストリ(17 種)・編集 op | [recommendation_planning.md §4](30_design/recommendation_planning.md) / [ADR-0005](adr/0005-itinerary-solver.md) | 固定シードで同一解。ハード制約 100% |
| 6 | **`domains/recommendation/`** — スコアラ + リランク | [recommendation_planning.md §3](30_design/recommendation_planning.md) / [ADR-0006](adr/0006-recommendation-hybrid.md) | 全出力 `spot_id` が DB 照合を通る |
| 7 | **`domains/narration/`** — 知識検索サブエージェント | [narration_qa.md](30_design/narration_qa.md) / [ADR-0011](adr/0011-knowledge-search-subagent.md) | Tool 0 回の `answer` が弾かれる。埋め込み断で字句検索に縮退する |
| 8 | **`domains/conversation/`** — 6 ノード + 5 Tool + ガードレール | **[agent_planning_phase.md Part II(§14〜24)](30_design/agent_planning_phase.md)** | ガードレール G1〜G9 が層 1 テストで通る |
| 9 | **SSE エンドポイント** | [40_api/chat_sse.md §1](40_api/chat_sse.md) | 切断してもターンが完走し、`GET /thread` で画面が完全に戻る |
| 10 | **フロントの chat を SSE 化** | 同 §1.1・§3.1 | `AbortController` で停止でき、停止しても見えた旅程が残る |
| 11 | **旧構成の削除** — `backend/api/` `backend/worker/` `svc-*` / ChromaDB コンテナ | [20 §14](20_architecture.md) | compose が **db / osrm-car / osrm-foot / app / frontend の 5 つ**になる |

**5〜7 は 8 より先に作る。**`act` が呼ぶ Tool の中身が無いとパイプラインを通しで検証できない。

### C-2. 特に間違えやすい 6 点(2026-08-01 の監査で実際に文書がずれていた箇所)

| # | 落とし穴 | 正 |
| --- | --- | --- |
| 1 | **述語は 17 種**。表の「行」は分類 11 個なので数え違えやすい | [§18.2 `PredEnum`](30_design/agent_planning_phase.md) |
| 2 | **`profile.interests` のキーは選好キー 12 語**。生タグ 80 語ではない | [data_model.md §2](30_design/data_model.md) |
| 3 | **旅程の時刻は「その日 00:00 からの分」(整数)**。`"09:40"` 文字列ではない | [data_model.md §7.2](30_design/data_model.md) |
| 4 | **`md_slug` は使わない**(実データで 42 個中 41 個が死んでいる)。知識 MD との対応は `faci_spot/spot_NNN.md` ↔ `spot_id` | [data_model.md §1.2](30_design/data_model.md) |
| 5 | **`constraints` は旅程行にあり、`Itinerary` の中ではない**。版コピー時に **id を維持する** | [data_model.md §4.5.3](30_design/data_model.md) |
| 6 | **Web 検索の結果を `spot_id` の供給源にしない** | [narration_qa.md §6.1](30_design/narration_qa.md) |

### C-3. テスト

**層 1 自動テストの仕様書は [agent_planning_phase.md §5](30_design/agent_planning_phase.md) のガードレール表と [recommendation_planning.md §7](30_design/recommendation_planning.md) の検査表**である。実装と同時に書かせる。**計測テーブルは無い**(NFR-7 削除)ので、テストが唯一の自動的な安全網になる。

## D. Phase 2 実装ガイド(2026-08-01 追加)

> **Phase 2 = geo + パック用ナレーション + voice + packs + ジョブ API + 進捗 UI**([20 §14](20_architecture.md))。
> 決定はすべて済んでいる(§A1)。**未決の論点は無い。**

### D-1. 実装の順序と、それぞれの「正」

| 順 | 作るもの | 読む文書 | 完了の判定 |
| --- | --- | --- | --- |
| **0** | **OSRM の切り出しと再ビルド**(`scripts/build_osrm.sh`)+ compose の差し替え | [50_operations/osrm.md](50_operations/osrm.md) | `backend/data/map` が **1 GB 未満**。healthcheck が実 `/route` で通る。**Phase 1 の前に済ませる**(§C-0 項目 5) |
| **1** | **`domains/geo/`** — OSRM クライアント・`build-geo`・`build-travel-times` | [geo.md §2・§4](30_design/geo.md) | `spot_approach` **43 件**、`travel_times.car` **1,806 行・欠損ゼロ** |
| 2 | **`app.routes` と `POST /api/v1/routes`** | [geo.md §3](30_design/geo.md) | 同一 `params` が既存行を返す。`osrm_build` が変わると新しい行になる |
| 3 | **沿道 POI**(PostGIS 1 クエリ) | [geo.md §5](30_design/geo.md) | 全点総当たりが無い。`route_position` が返る |
| 4 | **`domains/voice/`** — `TTSPort` + gTTS | [packs_pipeline.md §6](30_design/packs_pipeline.md) | **1 件失敗しても他が続く。**ffmpeg に依存しない |
| 5 | **`domains/narration/pack_text.py`** — テンプレート 1 本 + 検証 | [packs_pipeline.md §5](30_design/packs_pipeline.md) | 空・拒否応答・長さ逸脱が TTS に流れない。**知識は `spot_id` 直引き** |
| 6 | **`domains/packs/` + `jobs/`** — ジョブランナー | [packs_pipeline.md §3・§4](30_design/packs_pipeline.md) | 途中でプロセスを落として再起動すると**続きから走る**。`partial` で成果物が配れる |
| 7 | **API**(`POST /packs` / `GET /jobs/{id}` / `GET /packs/{id}`)+ 静的配信 | [packs_pipeline.md §8](30_design/packs_pipeline.md) | 同一キーの再要求が**新しいジョブを作らない** |
| 8 | **フロントの進捗 UI** | [packs_pipeline.md §8.1](30_design/packs_pipeline.md) | 生成中もアプリが使える。**`partial` を「完了」と表示しない** |
| 9 | **旧サービスの削除** — `svc-nav` / `svc-routing` / `svc-alongpoi` / `svc-llm` / `svc-voice` | [20 §14](20_architecture.md) | compose が **db / osrm-car / osrm-foot / app / frontend の 5 つ**になる |

**4〜5 は 6 より先に作る。**ジョブランナーだけ先に作ってもアセットの中身が無い。

### D-2. 特に間違えやすい 6 点

| # | 落とし穴 | 正 |
| --- | --- | --- |
| 1 | **`travel_times` は door-to-door**(両端の徒歩を含む)。駐車場までの時間ではない | [geo.md §4.1](30_design/geo.md) |
| 2 | **`leg_from_prev.mode`(行列のどちらの行か)と `routes.mode_summary`(セグメント構成)は別物。**一致させようとしない | [geo.md §1.3](30_design/geo.md) |
| 3 | **経路の冪等キーに `osrm_build` を含める。**忘れると古い道の線が残り続ける | [geo.md §3.2](30_design/geo.md) |
| 4 | **パックの冪等キーに `route_id` を含めない**(旅程 version が経路を決める) | [packs_pipeline.md §3.2](30_design/packs_pipeline.md) |
| 5 | **variant は排他ではない。**base の後に overlay を重ねる。選択規則は manifest の `playback_rules` | [ADR-0015](adr/0015-pack-asset-composition.md) |
| 6 | **対話では経路断で縮退、パック生成では失敗させる。**向きが逆なのは意図的 | [geo.md §6.3](30_design/geo.md) |

### D-3. テスト

| 層 | 何を検査するか |
| --- | --- |
| `unit/` | `spot_approach` の選定ロジック(モックした OSRM 応答)/ variant と `role` の組み合わせ / manifest の組み立て / ジョブ状態遷移 / 原稿の検証規則 |
| `contract/` | `POST /routes` の冪等性 / `POST /packs` の 202 と再要求 / `GET /jobs` の形(respx で OSRM・vLLM をモック) |
| `integration/` | PostGIS の沿道 POI クエリ(compose の db を使う) |
| `smoke/` | **旅程 1 つからパックが `ready` になるまで**(`run_nav_test.sh` の置き換え。ジョブポーリング対応) |

**gTTS は外部 API なので CI では必ずモックする。**実物を叩くのは手元の smoke だけにする。

## E. Phase 3・Phase 4 実装ガイド(2026-08-01 追加)

> **Phase 3 = DB 統合 + realtime(シミュレータ + LoRa)。Phase 4 = 掃除 + 観光フェーズのオフライン動作。**
> **フロントは差分改修に限る**([ADR-0017](adr/0017-frontend-incremental-change.md))。触ってよい範囲は [frontend_nav.md](30_design/frontend_nav.md) が正。

### E-1. 実装の順序と、それぞれの「正」

| 順 | 作るもの | 読む文書 | 完了の判定 | Phase |
| --- | --- | --- | --- | --- |
| 1 | **`domains/realtime/` の状態ストアとシミュレータ**(`rt-load` / `rt-start` / `rt-set` / `rt-show`) | [realtime_lora.md §6](30_design/realtime_lora.md) | シナリオを流すと `spot_realtime` が時刻どおりに変わる。**値の無いスポットは NULL のまま** | 3 |
| 2 | **`codec.py`**(§2 のエンコード/デコード) | [realtime_lora.md §2](30_design/realtime_lora.md) | 純関数。**単体テストで往復が一致する。**未知の `ver` を捨てる | 3 |
| 3 | **`scheduler.py`**(フェアユース) | [realtime_lora.md §3](30_design/realtime_lora.md) | 11 通目が publish されず、**ログに `downlink_budget_exceeded` が出る** | 3 |
| 4 | **`mqtt.py`**(aiomqtt・lifespan で 1 つ) | [realtime_lora.md §5](30_design/realtime_lora.md) | uplink → downlink が往復する。**プロセス内キャッシュを持たない** | 3 |
| 5 | **フロント: LoRa decode + `rt` ストア** | [frontend_nav.md §4](30_design/frontend_nav.md) | `pack_epoch` 不一致でコードを捨てる。**AT コマンド部を触っていない** | 3 |
| 6 | **フロント: 接続の差分**(SSE / チップ / 旅程カード / 進捗パネル / `GET /spots`) | [frontend_nav.md §2](30_design/frontend_nav.md) | 同文書 §5 の受け入れ条件 8 項目 | 1〜2 と並行 |
| 7 | **フロント: 実害のあるバグ 5 件** | [frontend_nav.md §3](30_design/frontend_nav.md) | Pinia が 1 つ。`v-html` に DOMPurify。タイルがキャッシュされる | 4 |
| 8 | **観光フェーズ**(取り込み・到達判定・再生・タイル) | [offline_field_mode.md](30_design/offline_field_mode.md) | 同文書 §9 の受け入れ条件 7 項目。**うち 3〜6 は室内で確認できる** | 4 |
| 9 | **旧構成の削除とリポジトリ衛生** | [20 §14](20_architecture.md) / [22 §15](22_current_issues.md) | compose が 5 コンテナ。`backend/worker/` が無い | 4 |

**5 と 6 は独立している。**6(SSE・チップ)は Phase 1 のバックエンドができ次第すぐ必要になるので、**Phase 3 を待たない**。

### E-2. 特に間違えやすい 5 点

| # | 落とし穴 | 正 |
| --- | --- | --- |
| 1 | **サーバーから push できると思って設計する** | Class A ではダウンリンクは uplink 直後の窓だけ([ADR-0016](adr/0016-lora-terminal-driven-batch.md)) |
| 2 | **`manifest.spots` の並びを後から変える** | **並びが通信プロトコルの一部**になっている。変えるなら `pack_epoch` を上げる |
| 3 | **値が無いスポットを 0 で埋める** | `0xF` = 不明。**捏造しない**([22 §6-2](22_current_issues.md)) |
| 4 | **フェアユースのカウンタをプロセス内に持つ** | 再起動で上限が消える。`app.lora_downlinks`(DB) |
| 5 | **「ついでに」フロントを整理する** | [ADR-0017](adr/0017-frontend-incremental-change.md)。**触ってよい範囲は決まっている** |

### E-3. テスト

| 層 | 何を検査するか |
| --- | --- |
| `unit/` | **codec の往復**(エンコード → デコードが一致 / 未知の `ver` / `0xF`)/ スケジューラの上限判定 / シミュレータの時刻進行 |
| `contract/` | 管理 API(`simulator/{action}`)/ `GET /realtime/spots/{id}` の ETag |
| `integration/` | `lora_downlinks` のカウンタが日をまたいでリセットされる |
| 手動 | 機内モードでの通し(実機)。**それ以外は室内で再現する**([offline_field_mode.md §9](30_design/offline_field_mode.md)) |

## F. 実装時に決める調整項目(設計判断ではない)

[recommendation_planning.md](30_design/recommendation_planning.md) §8.2。値・見せ方として残るもので、文書を止めない。

1. 暫定 → 確定の UI の見せ方(実機を見て決める)
2. ペナルティの重み初期値と編集距離の係数 `β`(シナリオ台本で合わせる)
3. ILS のパラメータ(反復回数・shake 件数・打ち切り)。43 地点の厳密解と比較して一度決める
4. 述語の初期実装セット(**17 種**のうち `weight`/`require`/`exclude`/`last`/`not_consecutive`/`time_window`/`lunch_break` から)
5. `stay_min` の初期値

**Phase 2 のぶん**([geo.md §11](30_design/geo.md) / [packs_pipeline.md §15](30_design/packs_pipeline.md)):

6. 並列度とタイムアウト(geo 8 並列・20 秒、narrate 8、speak 3)
7. 沿道バッファ(car 300 m / foot 50 m)と `trigger_radius_m`(**実機で歩いて決める**)
8. 原稿の長さの上下限と禁止表現リスト(生成物を見ながら)
9. `along_poi_limit`(20)と `gc-packs --keep`(3)の既定値

**Phase 3・4 のぶん**([realtime_lora.md §12](30_design/realtime_lora.md) / [offline_field_mode.md §12](30_design/offline_field_mode.md) / [frontend_nav.md §8](30_design/frontend_nav.md)):

10. 端末の自動要求間隔(90 分)・1 日の自制回数(8)・最短送信間隔(5 分)
11. タイルの zoom 範囲(10〜16)と離脱判定の倍率(2 倍)
12. チップと進捗パネルの見た目(既存の Tailwind に合わせる)
13. `VITE_API_BASE` の既定値と Vite プロキシ設定

## G. 決着済みの記録(2026-07-31)

**`agent_planning_phase.md` §13 の 5 論点**(決定内容は同文書の各節に統合済み):

| # | 論点 | 決定 |
| --- | --- | --- |
| 1 | `plan` の最大手数 | **3 のまま**。足りなければ破棄がログに出るので、それを見て増やす |
| 2 | ステップ参照の語彙 | **3 つのまま**。追加は後方互換 |
| 3 | 知識検索を道具に含めるか | **含める**。方式は 2026-08-01 決定(ADR-0011/0012、`narration_qa.md`) |
| 4 | undo の UI | **両方**。ボタン = 専用 REST(LLM を通さない)、自然言語 = `edit_itinerary` の `revert` op |
| 5 | 旅程なしの推薦 | **認める**。推薦は旅程を FK 参照せず、旅程は `plan_itinerary` で遅延生成 |

**文書の承認状態**: `00_project` / `10_requirements` / `20_architecture` / ADR-0001〜0004 を 2026-07-31 に承認(ADR-0005〜0008 は 07-30〜07-31 に承認済み)。**未承認の文書はない。**

`21_architecture_asis.md` / `22_current_issues.md` は凍結(更新しない)。

---

## H. 実装完了後に残っている調整項目(2026-08-02)

**いずれも設計判断ではなく、動かしながら決める値・見せ方である。**機能は動いている。

| # | 項目 | 詳細 |
| --- | --- | --- |
| 1 | **`understand` が時間指定を `unmodeled` に落とすことがある** | 「9時から17時で」が `days[].start/end` に正しく反映されているのに、`respond` が「一部システムで未処理」と述べる。**プロンプトの調整**([agent_planning_phase.md §3.1](30_design/agent_planning_phase.md) の `handling` 判定)。機能上の実害はないが、応答が分かりにくい |
| 2 | **`ask_user` のチップのリロード復元** | `GET /thread` の `pending` が全 kind を返していない。フロントは sessionStorage で補完している。**正しくは `messages.meta` に選択肢を載せて `pending` から返す**(**ADR-0018 の `pending_ask` 統合に合わせて直す**) |
| 3 | **`build-geo` の徒歩 0 分問題** | 車道スナップ点が選ばれた 4 地点(赤田の大仏 119 m / 遊佐町総合運動公園 55 m / 胴腹滝 96 m / 牛渡川 289 m)は foot グラフ上で同一ノードにスナップし `walk_sec = 0` になる。**誤差は 1 地点あたり最大 ±4 分**([geo.md §2.3.1](30_design/geo.md) に記録) |
| 4 | **ILS の最適性ギャップが未実測** | 43 地点なら厳密解が計算できる([recommendation_planning.md §7](30_design/recommendation_planning.md))。**一度だけ測ってパラメータを確定する**(現在は反復 160 回・β 0.6) |
| 5 | **推薦スコアの同点が多い** | 粗いタグ語彙のため上位が同点で並ぶ(例: 温泉選好で 5 件が 3.5 点)。**LLM リランクが差をつける前提**の設計([ADR-0006](adr/0006-recommendation-hybrid.md))だが、重みの調整余地がある |
| 6 | **ヘッダの表示が "AI Agent by Qwen3"** | 実際のモデルは `gemma-4-31B`。表示だけの問題 |
| 7 | **フロントの残骸** | `PlanView.vue` / `PlanForm.vue` / `stores/counter.js` / `components/icons/*`。`PlanView` は router から参照されているので、消すなら router も直す |
| 8 | **実機が要る検証** | 機内モードでの観光フェーズ通し / LoRa 端末での decode / TTN のダウンリンク。**現在いずれも使えないため未検証**([offline_field_mode.md §9](30_design/offline_field_mode.md)) |

## I. 実機を触って出た課題(2026-08-03)

### I-1. UX 問題インベントリ

**`http://localhost:5173/` を実際に操作した所見を [23_ux_issues.md](23_ux_issues.md) に記録した。**計画フェーズを通す 8 手のうち **3 手が行き止まり**になっている。優先度は同文書 §9。

### I-2. `ask_user` を「結果を返す 1 つの Tool」に統合する(**2026-08-03 実装完了**)

**[ADR-0018](adr/0018-ask-user-resumable-tool.md) で決定(2026-08-03、ユーザー指示)。[ADR-0010](adr/0010-understand-bounded-agent.md) を置き換える。**

`ask_user` が 2 系統(T5 Tool と `understand` のノード内分岐)に割れており、**どちらも返り値を持たない**「呼んで終わり」の道具だった。これを **1 つの Tool + ターンの中断・復帰**に統合する。

**文書は反映済み**: [ADR-0018](adr/0018-ask-user-resumable-tool.md) / [agent_planning_phase.md](30_design/agent_planning_phase.md) §1.3・§2・**§3.1**・**§3.4**・§4.1・**§5.1**・§6・§10・§15.3〜15.7・§16.5・§18.7・§19.3・§20.2・§23・§24.3 / [understand_node.md](30_design/understand_node.md) §0・§0.1 / [chat_sse.md](40_api/chat_sse.md) §1.2・**§1.4**・§3.1 / [data_model.md](30_design/data_model.md) §4.2・§4.3

**実装で触るもの**

| 層 | 変更 |
| --- | --- |
| `understand` | guided schema から `action` / `clarify` を削除。`plan` の Tool enum に `ask_user` を入れる。`tool_results` をプロンプトに載せる |
| `guards` | G6〜G9 を `understand` から `validate_plan`/`act` 側へ移し、G1〜G5 と 1 組に統合(旧 G8 は G3 に吸収) |
| `planner` | P5 を「末尾のみ + plan 全体で 1 手」に |
| `tool_adapters` / `executor` | `ask_user` が `kind` を受け、`state:ask_user` / `state:clarify` を出し分ける。`pending_ask` を立てる |
| `context` | `pending_ask` があれば答えを Tool の結果に組み立て `tool_results` に入れる。**1 ターンで失効** |
| `repository` / DB | `threads.pending_clarification` → **`pending_ask`**、`clarify_streak` を `ask_streak` に統合(**マイグレーション**) |
| `pipeline` | **E6 の分岐を削除** |
| `respond` | 「聞き返し」モードを「質問」モードに統合(4 → 3) |
| フロントエンド | **`ask_user` 専用の入力フォームを新設する**(2026-08-03 追加、ユーザー指示。[frontend_nav.md §2.3](30_design/frontend_nav.md))。`OC_AskUserForm.vue` を 1 つ足し、入力欄の直上にドッキング。**`OC_ChatMessage.vue` のチップ行は削除**(回答 UI を 2 か所に持たない)。SSE の kind と `resolves` の形は変えない |

### I-3. プロンプトに「コードが検証する語彙」を載せる(**2026-08-03 実装完了**)

`understand` プロンプトに**生タグ 80 語**(`static.tag_vocabulary`)と **`recommend.filter.mobility` の enum 値**ほかを載せた。**載せていなかったために、平易な発話で推薦が 0 件になっていた**([agent_planning_phase.md §7](30_design/agent_planning_phase.md) の改訂 / [23_ux_issues.md §0.3](23_ux_issues.md))。

## J. 次にやること

1. **[23_ux_issues.md](23_ux_issues.md) §8 の優先度順に直す。**まず **§1-1(最初の 1 手が空振り)**と **§1-2〜1-5(旅程を作ると地図が消える)**
2. **判断を仰ぐ 2 件**: **§7-2**(引数 1 項目の不正で手を丸ごと破棄してよいか。[agent_planning_phase.md §23 論点 20](30_design/agent_planning_phase.md))/ **§7-3**(ガードレールが手を破棄したあとのフォールバック。案 A/B/C)
3. **`feat/rebuild-implementation` を develop にマージするか判断する**
4. §H の 1〜2 を直す(応答の分かりにくさと復元の穴)
5. §H の 4 を測って ILS のパラメータを確定する
6. 実機が用意できたら §H の 8
