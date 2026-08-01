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

## Local Development

### Frontend
```bash
cd frontend
npm install
npm run dev
```

### Backend (local run)
```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8090
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
