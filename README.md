# Guidance LLM

LLM × 位置情報 × 経路探索 × 音声処理を統合した、マイクロサービス型フルスタックプロジェクト。
FastAPI を中核に、地理データ/ベクトル検索/音声/ルーティングを Docker Compose で一体運用します。

---

## Highlights
- API Gateway + 各種サービス分割のマイクロサービス構成
- PostGIS/OSRM/ChromaDB/Redis を統合した実運用寄りの設計
- Vue 3 + Vite + Leaflet による地図 UI
- DB 初期化まで含めたワンコマンド起動

## Tech Stack
**Backend**: Python 3.11, FastAPI, Uvicorn  
**Frontend**: Vue 3, Vite, Pinia, Tailwind CSS, Leaflet  
**DB**: PostgreSQL, PostGIS  
**Search/Vector**: ChromaDB  
**Routing**: OSRM (car/foot)  
**LLM**: vLLM (OpenAI互換API) — 生成は `.env` の `Inference_server`、埋め込みは `Embedding_server` で接続先を指定  
**Infra**: Docker, Docker Compose

---

## Architecture (High-level)
```
Frontend (Vue/Vite) ──> API Gateway (FastAPI)
                             │
                             ├─ svc-nav / svc-routing / svc-alongpoi
                             ├─ svc-llm / svc-voice
                             ├─ svc-agent (LangGraph)
                             ├─ app-db (PostgreSQL)
                             ├─ static-db (PostGIS)
                             ├─ chromadb
                             └─ osrm-car / osrm-foot
                  vLLM (ホスト上, OpenAI互換API :8000) ←─ svc-llm / svc-agent
```

詳細は [`Docs/architecture.md`](Docs/architecture.md) を参照してください。

---

## Services & Ports
| Service | Port | Description |
| --- | --- | --- |
| Frontend (Vite) | 5173 | Web UI |
| API Gateway | 8080 | メイン API |
| vLLM 生成 (ホスト側で起動) | 8000 | テキスト生成 (`Inference_server`) |
| vLLM 埋め込み (別マシン) | 8001 | 埋め込み生成 (`Embedding_server`) |
| Static DB (PostGIS) | 5432 | 地理データ |
| App DB (PostgreSQL) | 5433 | ユーザ/会話 |
| ChromaDB | 8001 | ベクトル検索 (コンテナ内は8000) |
| OSRM car | 5001 | ルーティング |
| OSRM foot | 5002 | ルーティング |
| svc-nav | 9100 | ナビ/統合 |
| svc-routing | 9101 | 経路探索 |
| svc-alongpoi | 9102 | POI |
| svc-llm | 9103 | ナレーション生成 |
| svc-voice | 9104 | 音声 |
| svc-agent | 9200 | Agent API |

---

## Local Development

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Backend (local run)
```bash
uvicorn backend.api.main:app --host 0.0.0.0 --port 8080 --log-level debug
```

---

## Data & Assets
- OSRM map data: `backend/worker/data/map/`
- Knowledge data: `backend/worker/data/knowledge/`
- Packs: `packs/`

---

## Directory Overview
- `backend/` サーバーサイド (API/worker/services)
- `frontend/` フロントエンド (Vue/Vite)
- `Docs/` アーキテクチャドキュメント
- `docker-compose.yml` 統合開発環境

---

## Notes
- 依存関係: `backend/requirements.txt` / `frontend/package.json`
- 音声サービスには `ffmpeg` が必要です

---

## Contributing
Issue / PR を歓迎します。改善提案やバグ報告はお気軽にどうぞ。

---

## License
未設定（必要に応じて `LICENSE` を追加してください）
