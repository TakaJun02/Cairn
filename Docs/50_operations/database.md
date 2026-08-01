# データベースの用意

- 状態: **決定稿 (2026-08-01)**
- 前提: [ADR-0002](../adr/0002-single-postgres.md)(PostgreSQL 1 台に統合)/ [ADR-0012](../adr/0012-knowledge-retrieval-pgvector.md)(知識検索のベクトルを同一 DB に置く)
- 正: **スキーマは [30_design/data_model.md](../30_design/data_model.md)**。この文書は「どうやって立てるか」だけを書く

---

## 1. イメージ

**PostGIS と pgvector の両方が要る**が、公式イメージに両方入ったものは無い。**Debian パッケージを 1 つ足すだけの派生イメージ**を持つ。

```dockerfile
# docker/postgres/Dockerfile
FROM postgis/postgis:16-3.4
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-pgvector \
 && rm -rf /var/lib/apt/lists/*
```

```bash
docker build -t guidance-postgres:16-3.4-pgvector -f docker/postgres/Dockerfile docker/postgres
```

**検証済み(2026-08-01 実測)**

| 確認したこと | 結果 |
| --- | --- |
| 拡張のバージョン | **postgis 3.4.3** / **vector 0.8.5** |
| `vector(4096)` の作成と INSERT | **OK**([narration_qa.md §4.1](../30_design/narration_qa.md) の設計値) |
| コサイン距離演算子 `<=>` | **OK** |
| `ST_DWithin(geography, geography, m)` | **OK**([geo.md §5.1](../30_design/geo.md)) |
| `ST_LineLocatePoint(geometry, geometry)` | **OK**(同上。`route_position` の算出) |

> **`vector` 型の次元上限は 16,000** なので 4096 は問題ない。**ただし HNSW / IVFFlat の索引は 2,000 次元まで**である。本設計は[近似最近傍索引を張らない](../30_design/narration_qa.md)([ADR-0012](../adr/0012-knowledge-retrieval-pgvector.md))と決めているので影響しないが、**将来「遅いから索引を張ろう」としたときにここで詰まる**。そのときは次元削減か別の距離計算方式の検討が要る。

---

## 2. compose

```yaml
db:
  build:
    context: ./docker/postgres
  env_file: [.env]
  environment:
    POSTGRES_DB:       ${POSTGRES_DB}
    POSTGRES_USER:     ${POSTGRES_USER}
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
  volumes: [pgdata:/var/lib/postgresql/data]
  ports: ["5432:5432"]
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
    interval: 5s
    timeout: 5s
    retries: 20
```

- **DB は 1 台だけ**([ADR-0002](../adr/0002-single-postgres.md))。旧構成の `static-db` / `app-db` / `chromadb` は廃止する
- **初期化コンテナ(`static-db-init` / `app-db-init`)も廃止**し、`python -m app.cli` に統合する(§3)

---

## 3. 初期化の手順

```bash
docker compose up -d db
alembic upgrade head                    # DDL（拡張の作成もここ。static も app も）
python -m app.cli seed                  # static のデータ（backend/data/seeds/ から冪等 upsert）
python -m app.cli index-knowledge       # 知識 MD 118 本 → 文書・チャンク・埋め込み
python -m app.cli build-geo             # spot_approach（OSRM 必須。50_operations/osrm.md）
python -m app.cli build-travel-times    # travel_times（同上）
python -m app.cli validate-seeds        # 参照整合性の検査
python -m app.cli validate-knowledge    # 孤児 MD の報告
```

**分担**([data_model.md §7.5](../30_design/data_model.md))

| | 誰が管理するか |
| --- | --- |
| DDL(テーブル・索引・制約・**拡張**) | **Alembic** |
| `static` のデータ | **シード CLI**(`seed` / `index-knowledge`) |
| `static` のうち OSRM 由来(`spot_approach` / `travel_times`) | **別コマンド**(`build-geo` / `build-travel-times`)。OSRM の起動が要る |
| `app` のデータ | 実行時にアプリが書く |

**`CREATE EXTENSION` を Alembic に入れる**(イメージに焼かない)。拡張の有無がマイグレーション履歴に残るほうが、環境の差を追いやすい。

---

## 4. リセット

```bash
python -m app.cli reset-user  <user_name>   # 会話・旅程・プロファイルを消す（users 行は残す）
python -m app.cli delete-user <user_name>   # users 行ごと（CASCADE）
docker compose down -v                      # ★ ボリュームごと全消去（seed からやり直し）
```

[data_model.md §7.4](../30_design/data_model.md) のとおり**論理削除は導入しない**。`static` スキーマはユーザー削除の影響を受けない。

---

## 5. 旧構成からの移行

| 旧 | 新 |
| --- | --- |
| `static-db`(postgis)+ `app-db`(postgres)+ `chromadb` の 3 コンテナ | **`db` 1 つ** |
| `static-db-init` / `app-db-init`(`Dockerfile.init` + スクリプト 3 本) | **`python -m app.cli`** |
| 生 DDL + 生 SQL の二重管理、壊れた ORM 定義([22 §4-5](../22_current_issues.md)) | SQLAlchemy モデル + Alembic |
| DB 名が実装箇所ごとに 3 種類([22 §4-6](../22_current_issues.md)) | `.env.example` の `POSTGRES_DB` 1 か所 |

**会話・ユーザーデータの移行スクリプトは Phase 3 で書く**([20_architecture.md §14](../20_architecture.md))。実験データとして残す必要があるものを事前に確認すること。
