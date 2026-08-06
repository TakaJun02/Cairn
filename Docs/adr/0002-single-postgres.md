# ADR-0002: データストアをPostgreSQL 1台(PostGIS)に統合する

- 状態: **承認 (2026-07-31、ユーザー判断)**(2026-07-30 改訂: 長期記憶のスコープ外化に伴い、pgvectorを必須構成から外した)
- 日付: 2026-07-29 / 改訂 2026-07-30 / 承認 2026-07-31

## 文脈(何が問題か)

現行の永続化は static-db(PostGIS)・app-db(PostgreSQL)・ChromaDB(会話長期記憶)・FAISSファイル(ロードされるが未使用)の4系統+プロセス内キャッシュに分散している。static側はORM定義が壊れており生SQLと二重管理、DB名の既定値が実装箇所ごとに3種類、マイグレーションツールなし、Chromaは埋め込み次元変更でサイレントに機能停止する([22 §4](../22_current_issues.md))。

さらに 2026-07-30、Chroma の唯一の用途だった**会話の長期記憶そのものが要求から外れた**([00_project.md](../00_project.md) スコープ外)。

## 決定(何をすると決めたか)

- PostgreSQL 16 の単一インスタンス(`postgis/postgis` イメージ)に統合し、スキーマ `static` / `app` で区分する。SQLAlchemy(async)+Alembicで管理する
- ChromaDBコンテナ、`chromadb`/`faiss-cpu` 依存、埋め込みクライアント、**別マシンの埋め込みvLLMへの依存を削除**する
- ベクトル検索が将来必要になったら、同一DBに pgvector 拡張を追加して再導入する(新しいデータストアは増やさない)

## 理由(なぜ他案でなくこれか)

- PostgreSQLを2台に分ける理由(負荷分離・権限分離)は実験規模では存在せず、スキーマ分離で目的(静的データと動的データの区別)は達成できる
- 長期記憶がスコープ外になった以上、ベクトルストアを持つ理由がない。機能を残して基盤だけ差し替える(Chroma→pgvector)案より、機能ごと落とすほうが要求に忠実で構成も軽い
- 接続先・資格情報・バックアップ対象が1つになり、再現性(NFR-2)に直結する

## 影響(この決定で生じる制約・やること)

- Alembic導入。既存データ(users/conversations/spot_realtime)の移行スクリプトをPhase 3で書く
- Chromaのベクトルデータは移行しない(必要になれば会話ログから再構築可能)
- ~~`.env` の `Embedding_server` と関連コード(`vector_store_client.py` ほか)は削除対象~~ → **2026-08-01 取り消し**(下記)
- 長期記憶を再導入する場合は、本ADRを更新(または新ADR)した上で pgvector 拡張+背景埋め込みタスクを追加する

## 2026-08-01 改訂 — pgvector を発動する([ADR-0012](0012-knowledge-retrieval-pgvector.md))

**本 ADR が用意した「ベクトル検索が将来必要になったら、同一 DB に pgvector 拡張を追加して再導入する」が発動した。**用途は**長期記憶ではなく知識検索**(`answer_qa` の後継)である。

| | |
| --- | --- |
| **取り消す** | `.env` の `Embedding_server` の削除。**埋め込みサーバは使う**(`Qwen/Qwen3-Embedding-8B`、4096 次元、実測確認済み) |
| **維持する** | **ChromaDB コンテナと `chromadb`/`faiss-cpu` 依存の削除。**ベクトルは同一 Postgres に持つので、**データストアは増えない**(本 ADR の中心的な主張はそのまま守られる) |
| **追加する** | `postgis/postgis:16-3.4` に `postgresql-16-pgvector` を入れた派生イメージ。`static` スキーマに `knowledge_chunks(embedding vector(4096))` |

**旧 `vector_store_client.py` は削除する**(Chroma 向けであり、埋め込み次元の不一致でサイレント停止する構造も含めて作り直す)。**長期記憶は引き続きスコープ外**である。
