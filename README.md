# Guidance LLM

LLM × 位置情報 × 経路探索 × 音声処理を統合したフルスタックプロジェクト。
FastAPI のモジュラモノリスを中核に、PostGIS/pgvector と OSRM を Docker Compose で一体運用します。

---

## Highlights
- FastAPI モジュラモノリスによる一貫した API
- PostGIS/pgvector/OSRM を統合した実運用寄りの設計
- Vue 3 + Vite + Leaflet による地図 UI
- DB 初期化まで含めたワンコマンド起動

## Tech Stack
**Backend**: Python 3.11, FastAPI, Uvicorn  
**Frontend**: Vue 3, Vite, Pinia, Tailwind CSS, Leaflet  
**DB**: PostgreSQL, PostGIS  
**Search/Vector**: PostgreSQL, pgvector
**Routing**: OSRM (car/foot)  
**LLM**: vLLM (OpenAI互換API) — 生成は `.env` の `INFERENCE_SERVER`、埋め込みは `EMBEDDING_SERVER` で接続先を指定
**Infra**: Docker, Docker Compose

---

## Architecture (High-level)
```
Frontend (Vue/Vite) ──> app (FastAPI :8090)
                             ├─ db (PostgreSQL + PostGIS + pgvector)
                             ├─ osrm-car / osrm-foot
                             └─ vLLM (ホスト上, OpenAI互換API :8000)
```

詳細は [`Docs/20_architecture.md`](Docs/20_architecture.md) を参照してください。

---

## Services & Ports
| Service | Port | Description |
| --- | --- | --- |
| Frontend (Vite) | 5173 | Web UI |
| app (FastAPI) | 8090 | メイン API |
| db (PostgreSQL + PostGIS + pgvector) | 5432 | アプリ・地理・ベクトルデータ |
| OSRM car | 5001 | ルーティング |
| OSRM foot | 5002 | ルーティング |
| vLLM（ホスト側で起動） | 8000 | テキスト生成 |

---

## Setup

```bash
cp .env.example .env     # 秘密と環境固有値だけを埋める
docker compose up -d
```

DB の初期化（マイグレーション・シード・ジオ派生データ）は [`Docs/50_operations/database.md`](Docs/50_operations/database.md) §3 を参照してください。

### 設定の置き場所

設定は役割で 3 層に分かれています（[`Docs/20_architecture.md`](Docs/20_architecture.md) §11）。**`.env` に全部を並べません。**

| 置き場所 | 何を書くか |
| --- | --- |
| `.env`（Git 追跡外） | **秘密**と**人によって値が変わるもの**だけ — パスワード、API キー、IP を含む URL、モデル名 |
| `docker-compose.yml` の `environment:` | 接続先のトポロジ（`POSTGRES_HOST` / `OSRM_*_URL` / `PACKS_ROOT` など）とプロジェクトの定数（`POSTGRES_DB` / `POSTGRES_USER` = `guidance`）。compose の構成が決めるので人によらない |
| `backend/app/core/config.py` の `Settings` | チューニング値の既定（`OSRM_*` / `GEO_*` / タイムアウト / 各種フラグ）。変えたい人だけ `.env` で上書きする |

最低限必要なのは `POSTGRES_PASSWORD` と、vLLM を既定（`http://127.0.0.1:8000/v1`）以外で動かしている場合の `INFERENCE_SERVER` / `INFERENCE_MODEL` です。埋め込み・Web 検索・LoRaWAN は未設定でも起動し、その機能だけが縮退します。

---

## Local Development

**コードはコンテナにマウントされます。** イメージが持つのは依存関係だけなので、`backend/` や `frontend/` を編集しても再ビルドは不要です（`app` は uvicorn の `--reload`、`frontend` は Vite の HMR が拾います）。

```bash
docker compose up -d          # 編集はそのまま反映される
docker compose build app      # 再ビルドが要るのは pyproject.toml を変えたときだけ
docker compose logs -f app
```

コンテナを使わずに動かす場合:

```bash
# Frontend
cd frontend && npm install && npm run dev

# Backend
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8090
```

---

## Data & Assets
- OSRM map data: `backend/data/map/`
- Knowledge data: `backend/data/knowledge/`
- Packs: compose の `packs_data` volume

---

## Directory Overview
- `backend/` サーバーサイド (FastAPI モジュラモノリス)
- `frontend/` フロントエンド (Vue/Vite)
- `Docs/` アーキテクチャドキュメント
- `docker-compose.yml` 統合開発環境

---

## Notes
- 依存関係: `backend/pyproject.toml` / `frontend/package.json`

---

## Contributing
Issue / PR を歓迎します。改善提案やバグ報告はお気軽にどうぞ。

---

## License
未設定（必要に応じて `LICENSE` を追加してください）
