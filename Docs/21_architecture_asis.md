# [as-is 記録] 旧アーキテクチャ

> **この文書は凍結された現行(旧)構成の記録である。** 目指す姿は [20_architecture.md](20_architecture.md) を参照。移行完了後、本文書は参照用としてのみ残る。

最終更新: 2026-07-28（Ollama/Celery を廃止し、vLLM 直接呼び出しへ移行後の構成）

本ドキュメントは guidanceLLM2 の現行アーキテクチャ、特に AI エージェント（`svc-agent`）の内部構造を記述する。

---

## 1. システム全体像

テキスト生成・埋め込み生成はすべて、**vLLM（OpenAI互換API）** への直接HTTPリクエストで行う。

| 用途 | .env キー | 例 | 備考 |
| --- | --- | --- | --- |
| テキスト生成 | `Inference_server` | `http://127.0.0.1:8000/v1` | コンテナ内からは `http://host.docker.internal:8000/v1` に上書き（compose の `environment`） |
| 埋め込み | `Embedding_server` | `http://<LAN の別マシン>:8001/v1` | 別マシンのvLLM。LAN IPのためコンテナからもそのまま到達可能（実アドレスは `.env` のみに置く） |

モデル名は `INFERENCE_MODEL` / `EMBEDDING_MODEL` 未指定時、各サーバの `GET /v1/models` から自動検出する。

```mermaid
graph TB
    subgraph Client
        FE["Frontend<br>Vue 3 + Vite + Leaflet<br>:5173"]
    end

    subgraph Gateway
        API["API Gateway (FastAPI)<br>:8080<br>/api/v1/chat, /api/route,<br>/api/nav/plan, /api/rt/*, /api/v1/users*"]
    end

    subgraph "AI エージェント"
        AGENT["svc-agent :9200<br>LangGraph 対話エージェント"]
    end

    subgraph "ナビゲーション パイプライン"
        NAV["svc-nav :9100<br>ガイダンス統合"]
        ROUTING["svc-routing :9101<br>経路探索"]
        ALONGPOI["svc-alongpoi :9102<br>沿道POI抽出"]
        LLMSVC["svc-llm :9103<br>ナレーション生成"]
        VOICE["svc-voice :9104<br>TTS (gTTS)"]
    end

    subgraph "推論サーバ (vLLM OpenAI互換API)"
        VLLM["生成 vLLM :8000<br>Inference_server"]
        EMBVLLM["埋め込み vLLM (別マシン) :8001<br>Embedding_server"]
    end

    subgraph DataStores
        APPDB[("app-db<br>PostgreSQL :5433<br>users / conversations / spot_realtime")]
        STATICDB[("static-db<br>PostGIS :5432<br>spots / facilities / access_points")]
        CHROMA[("chromadb :8001→8000<br>会話の長期記憶")]
        OSRM["osrm-car :5001<br>osrm-foot :5002"]
        PACKS[("packs/<br>音声・テキスト成果物")]
    end

    FE -->|"POST /api/v1/chat"| API
    FE -->|"POST /api/route, /api/nav/plan"| API
    API -->|"POST /invoke"| AGENT
    API --> NAV
    API --> ROUTING
    API -->|"MQTT (TTN) → spot_realtime"| APPDB

    AGENT --> VLLM
    AGENT -->|"/embeddings"| EMBVLLM
    AGENT --> APPDB
    AGENT --> CHROMA

    NAV --> ALONGPOI
    NAV -->|"POST /describe"| LLMSVC
    NAV -->|"POST /synthesize_and_save"| VOICE
    ROUTING --> OSRM
    ROUTING --> STATICDB
    ALONGPOI --> STATICDB
    NAV --> STATICDB
    LLMSVC --> VLLM
    VOICE --> PACKS
```

### LLM 呼び出しの一元化

全サービスの生成呼び出しは共通クライアント **`backend/worker/llm_client.py`** を経由する。

| 呼び出し元 | 関数 | 用途 | パラメータ |
| --- | --- | --- | --- |
| `agent_app/agent.py` `parse_intent` | `generate_text` | 意図解析 | `temperature=0.0`, `json_mode=True` |
| `agent_app/agent.py` `_extract_profile_from_text` | `generate_text` | プロファイル抽出 | `temperature=0.0`, `json_mode=True` |
| `agent_app/agent.py` `_generate_profile_question` | `generate_text` | プロファイル質問文生成 | デフォルト |
| `agent_app/agent.py` `generate_response` | `generate_text` | 応答生成（履歴つき） | デフォルト |
| `services/llm/generator.py` | `chat` | ナレーション生成 | デフォルト |
| `agent_app/vector_store_client.py` | `embed` | 長期記憶の埋め込み生成 | `Embedding_server` へ送信 |

- `json_mode=True` は vLLM の `response_format={"type": "json_object"}`（guided decoding）に対応。
- 会話履歴中の `system` ロールのメッセージ（長期記憶の要約）は、チャットテンプレート互換性のため
  先頭の system プロンプトへ統合される（`llm_client.generate_text` 内）。
- `embed` は `{Embedding_server}/embeddings` を呼び、入力順のベクトルを返す。

---

## 2. 対話フロー（1ターンのシーケンス）

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant GW as API Gateway
    participant AG as svc-agent (LangGraph)
    participant DB as app-db (PostgreSQL)
    participant VS as ChromaDB
    participant LLM as 生成 vLLM
    participant EMB as 埋め込み vLLM

    FE->>GW: POST /api/v1/chat {user_name, user_input, thread_id}
    GW->>AG: POST /invoke
    AG->>DB: ユーザー取得/作成 (start_session)
    AG->>LLM: プロファイル抽出 (JSONモード)
    AG->>DB: 直近3ターンの会話取得（短期記憶）
    AG->>EMB: 発話の埋め込み生成 (/embeddings)
    AG->>VS: 類似会話の検索（長期記憶, 最大2件）
    AG->>LLM: 意図解析 (JSONモード, temperature=0)
    Note over AG: intent に応じて推薦 / 旅程更新 / 質問生成
    AG->>LLM: 応答生成（履歴 + 推薦コンテキスト）
    AG->>DB: プロファイル・旅程・会話ログ保存 (end_session)
    AG->>EMB: 会話ターンの埋め込み生成
    AG->>VS: ベクトルを保存
    AG-->>GW: {response_text, itinerary, language, thread_id}
    GW-->>FE: ChatResponse
    Note over FE: itinerary が返ると /api/route を呼び地図を更新
```

---

## 3. LangGraph ステートグラフ（`agent_app/agent.py`）

### 3.1 状態 (AgentState)

| フィールド | 内容 |
| --- | --- |
| `user_name` / `language` | ユーザー識別・言語 (ja / en / zh) |
| `user_profile` | 年齢・旅行スタイル・頻度・興味など（JSONB永続化） |
| `itinerary` | 周遊計画（spot_id のリスト） |
| `chat_history` | 短期記憶 + 長期記憶を結合した会話履歴 |
| `user_input` / `parsed_intent` | 最新発話と意図解析結果 |
| `recommendations` | 推薦エンジンの出力（リアルタイム情報つき） |
| `response_text` | 最終応答テキスト |
| `awaiting_profile_fields` | 質問中のプロファイル項目 |

### 3.2 ノードと遷移

```mermaid
graph TD
    START(["START"]) --> start_session
    start_session["start_session<br>ユーザーロード・言語決定<br>前回の推薦を復元"]
    collect_user_profile["collect_user_profile<br>発話からプロファイル抽出 (LLM)<br>変化があればDB保存"]
    build_context["build_context<br>短期記憶: SQL 直近3ターン<br>長期記憶: Chroma 類似2件"]
    parse_intent["parse_intent<br>LLMで意図をJSON解析<br>失敗時は最大3回リトライ"]
    get_recommendations["get_recommendations<br>ペルソナ分類→候補生成→ランク付け<br>spot_realtime (天気/混雑) を付与"]
    update_itinerary["update_itinerary<br>追加/削除/並べ替え/リセット<br>名寄せ: 別名・簡繁変換・序数表現"]
    ask_for_profile["ask_for_profile<br>不足プロファイルの質問文生成 (LLM)"]
    generate_response["generate_response<br>ツアーガイド応答生成 (LLM)<br>spot_id→表示名の置換"]
    end_session["end_session<br>プロファイル・旅程・会話をDB保存<br>会話ターンをChromaへ保存"]
    END_(["END"])

    start_session --> collect_user_profile
    collect_user_profile --> build_context
    build_context --> parse_intent

    parse_intent -->|"request_recommendation<br>(プロファイル有)"| get_recommendations
    parse_intent -->|"request_recommendation<br>(プロファイル無)"| ask_for_profile
    parse_intent -->|"add_to_plan / delete_from_plan<br>reorder_plan / restart_plan"| update_itinerary
    parse_intent -->|"ask_question / chitchat<br>affirmation / negation など"| generate_response

    get_recommendations --> generate_response
    update_itinerary --> generate_response
    generate_response --> end_session
    ask_for_profile --> end_session
    end_session --> END_
```

意図の種類: `request_recommendation` / `add_to_plan` / `delete_from_plan` / `reorder_plan` / `restart_plan` / `ask_question` / `affirmation` / `negation` / `chitchat`

### 3.3 記憶アーキテクチャ

```mermaid
graph TD
    subgraph "ターン開始時: コンテキスト構築 (build_context)"
        UI["ユーザーの現在の発話"] --> QE["埋め込み vLLM<br>Embedding_server /embeddings"]
        QE --> VS[("ChromaDB<br>conversation_history")]
        VS --> LTM["長期記憶<br>類似会話 最大2件"]
        SQL[("app-db<br>conversations")] --> STM["短期記憶<br>直近3ターン (6メッセージ)"]
        LTM & STM --> CTX["LLMに渡す会話履歴"]
    end

    subgraph "ターン終了時: 永続化 (end_session)"
        TURN["完了した会話ターン<br>(ユーザー発話 + AI応答)"] --> SQL2[("app-db<br>conversations")]
        TURN --> EMB["埋め込み vLLM<br>Embedding_server /embeddings"]
        EMB --> VS2[("ChromaDB<br>conversation_history")]
    end
```

- 埋め込み生成は `.env` の `Embedding_server` が指す vLLM（OpenAI互換 `/embeddings`）への
  直接HTTPリクエストで行う（`llm_client.embed`）。モデル名は `/v1/models` から自動検出
  （`EMBEDDING_MODEL` で上書き可）。
- ChromaDB または埋め込みサーバに接続できない場合は長期記憶を自動的に無効化し、
  エージェントは短期記憶のみで動作を継続する（`vector_store_client.py` の遅延初期化 + try/except）。

### 3.4 推薦エンジン（`agent_app/recommender.py`）

```mermaid
graph LR
    P["user_profile<br>(Travel_Style / Travel_Frequency /<br>Preferred_Destinations)"] --> OH["OneHotEncoder"]
    OH --> KM["KMeans ペルソナ分類<br>persona_model.pkl"]
    KM --> PR["persona_recommendations.json<br>ペルソナ別候補 (+1.0)"]
    IT["itinerary 末尾スポット"] --> TM["travel_matrix.json<br>移動時間60分以内の近傍 (+0.5)"]
    PR & TM --> RANK["スコア合算 → 旅程内を除外 → 上位3件"]
    RANK --> RT["spot_realtime を付与<br>(天気 / 混雑, app-db)"]
```

学習成果物は `agent_app/processed/` に配置（`spot_info.json`, `travel_matrix.json`,
`persona_recommendations.json`, `persona_model.pkl`, `spot_embeddings.faiss`, `spot_id_map.json`）。

---

## 4. ナビゲーション時のナレーション生成（svc-llm）

`svc-nav /plan` はスポットごとに「通常ガイド + 状況別（曇り/雨/やや混雑/混雑）」のナレーション生成ジョブを
`svc-llm /describe` に送る。svc-llm はジョブを **ThreadPoolExecutor（既定4並列, `LLM_DESCRIBE_CONCURRENCY`）**
で vLLM に投げ、結果をまとめて返す。vLLM 側は継続バッチングで並行リクエストを処理する。

```mermaid
sequenceDiagram
    participant NAV as svc-nav
    participant LLM as svc-llm
    participant V as vLLM
    participant VO as svc-voice

    NAV->>LLM: POST /describe {language, jobs:[{job_id, spot}]}
    par 最大4並列
        LLM->>V: /chat/completions (ガイダンス or 状況別プロンプト)
        V-->>LLM: ナレーションテキスト
    end
    LLM-->>NAV: {items:[{job_id, spot_id, playback, situation, text}]}
    NAV->>VO: POST /synthesize_and_save {items}
    VO-->>NAV: packs/ 配下の MP3 + manifest
```

- RAGコンテキストは `KNOWLEDGE_DIR`（`knowledge/{lang}/faci_spot/{spot_id}.md`）からのファイル取得。
- `(spot_id, situation)` が nav 側での結合キーとなるため、レスポンスで必ず保持する。

---

## 5. モジュール構成（リファクタ後）

```
backend/
├── api/                      # API Gateway (FastAPI)
│   ├── main.py               # ルータ集約 + ロギングミドルウェア
│   ├── agent_router.py       # /api/v1/chat → svc-agent /invoke
│   ├── user_router.py        # ユーザー作成 / ログイン / セッション復元
│   ├── nav_router.py         # /api/nav/plan → svc-nav
│   ├── routing_router.py     # /api/route → svc-routing
│   └── realtime_router.py    # MQTT (TTN LoRaWAN) → spot_realtime, /api/rt/*
└── worker/
    ├── llm_client.py         # ★ vLLM (OpenAI互換) 共通クライアント（生成 + 埋め込み）
    ├── app_db_models.py      # users / conversations / spot_realtime (SQLAlchemy)
    ├── agent_app/            # ★ AIエージェント (svc-agent)
    │   ├── api.py            # FastAPI /invoke
    │   ├── agent.py          # LangGraph 定義（状態・ノード・エッジ）
    │   ├── prompts.py        # 意図解析 / プロファイル / 応答生成プロンプト (ja/en/zh)
    │   ├── recommender.py    # ペルソナ推薦エンジン
    │   ├── profile_taxonomy.py # プロファイル正規化辞書
    │   ├── db_client.py      # app-db アクセス
    │   ├── vector_store_client.py # ChromaDB 長期記憶（Embedding_server で埋め込み）
    │   └── processed/        # 学習済みモデル・データ
    └── app/services/
        ├── nav/              # ガイダンス統合 (svc-nav)
        ├── routing/          # OSRM 経路探索 (svc-routing)
        ├── alongpoi/         # 沿道POI (svc-alongpoi)
        ├── voice/            # gTTS 音声合成 (svc-voice)
        └── llm/              # ナレーション生成 (svc-llm)
            ├── main.py       # /describe（並列で vLLM 呼び出し）
            ├── describe.py   # 1ジョブの生成処理
            ├── schemas.py    # /describe の入出力スキーマ
            ├── generator.py  # llm_client への薄いラッパ
            ├── retriever.py  # knowledge MD の取得
            └── prompt.py     # ガイダンス / 状況別プロンプト
```

### 旧構成からの変更点（2026-07 リファクタ）

| 旧 | 新 |
| --- | --- |
| Ollama (qwen3:14b / embeddinggemma:300m) ×2コンテナ | vLLM 2台（生成: `Inference_server` / 埋め込み: `Embedding_server`） |
| Celery + Redis 経由の生成/埋め込みキュー | `llm_client.py` による直接HTTP呼び出し |
| `agent_app/llm_tasks.py`（Celeryタスク） | 削除（`llm_client.generate_text` / `llm_client.embed` に置換） |
| `services/llm/ollama.py` / `tasks.py` / `celery_app.py` | 削除 |
| 埋め込み: Ollama embeddinggemma → Celery embedding キュー | `Embedding_server` の vLLM `/embeddings` へ直接リクエスト |
| `docker-compose.worker.yml`（Ollama + Celeryワーカー） | 削除 |
| Redis サービス | 削除（利用箇所なし） |
| ChromaDB ホスト公開ポート 8000 | 8001（ホスト8000は vLLM が使用） |

> **注意**: 埋め込みモデルを切り替えた場合（旧Ollama embeddinggemma: 768次元 → 現行 Qwen3-Embedding-8B: 4096次元 など）、
> 作成済みの `conversation_history` コレクションとベクトル次元が一致せず検索がエラーになる。
> その場合は ChromaDB のボリュームを初期化するかコレクションを削除すること
> （エラー時も長期記憶が無効化されるだけでエージェントは動作する）。
