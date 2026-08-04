# 現行アーキテクチャの問題インベントリ

- 状態: 記録(再設計の根拠資料)
- 調査日: 2026-07-29(コミット c238721 時点のコード精査)
- 位置づけ: 個別修正のチェックリストではない。対応は [20_architecture.md](20_architecture.md) の再設計で行う

---

## 1. 構成レベル: 「マイクロサービス」が分散モノリスになっている

| # | 所見 | 根拠 |
| --- | --- | --- |
| 1-1 | 7つのFastAPIサービスが全て同一イメージ・同一ソースマウント(`./backend:/app/backend`)・同一requirements。独立デプロイ・独立スケールの利点はゼロのまま、HTTP+JSONシリアライズと部分障害のコストだけ払っている | `docker-compose.yml:169-251` |
| 1-2 | GatewayがエージェントのDB内部モジュールを直接import。サービス境界が既に破れており、「分割」は名目のみ | `backend/api/user_router.py:12`, `realtime_router.py:19` |
| 1-3 | 内部サービスのポートが全てホストへ公開(9100-9104, 9200, 5432)。Gatewayの認証・ロギングをバイパスして直接叩ける。認証は全経路に存在しない | `docker-compose.yml:177,187,201,221,232,250` |
| 1-4 | `/api/route` の結果(polyline/legs/waypoints_info)を**フロントエンドが保持して `/api/nav/plan` に詰め直して再送**する。サーバ側に経路の永続化がなく、中間状態の運搬責任がフロントにある | `frontend/src/stores/nav.js:99-110`, `nav/main.py:15`(routingクライアントはimportのみで未使用) |
| 1-5 | サービス間URLは `os.getenv(..., ハードコードdefault)` で、`.env` に該当キーがなく常にdefaultで動作 | `backend/api/agent_router.py:9` ほか |
| 1-6 | `worker/` という名前はCelery時代の遺物。`worker/app/services` と `worker/agent_app` の二系統、importルートも2規約併存(`backend.worker.*` と `working_dir`前提の `worker.*`) | `script/init_app_db.py:8` vs `docker-compose.yml` |

## 2. 同期パイプラインとタイムアウト

| # | 所見 | 根拠 |
| --- | --- | --- |
| 2-1 | **パック生成が1本の同期HTTPリクエスト内で完結**(alongpoi→LLM 13ジョブ→gTTS 13件逐次→ファイル書込→manifest)。ジョブ概念なし。Gateway側タイムアウトはハードコード3000秒(50分) | `nav/main.py:201-310`, `nav_router.py:39` |
| 2-2 | チャットは**タイムアウト矛盾**: Gateway 180秒 < LLMクライアント600秒。長いターンは必ずGateway側で切れる | `agent_router.py:10` vs `llm_client.py:34` |
| 2-3 | 1チャットターンで LLM生成3〜8回+埋め込み2回+Chroma 2回+PG接続5本が**全て直列・同期**。並列化ポイントなし。プロファイル完備でも抽出LLMを毎ターン無条件実行 | `agent.py:975,1019-1328` |
| 2-4 | 主要エンドポイントが軒並み同期`def`+同期httpx。既定40のスレッドプールを長時間占有(nav系は最大50分) | `nav/main.py:201`, `llm/main.py:21`, `voice/main.py:176`, `api/nav_router.py:22` |
| 2-5 | svc-llmはリクエスト毎に`ThreadPoolExecutor(4)`を生成。グローバルな同時実行制御なし。1ジョブの例外で全ジョブ失敗(partial success不可) | `llm/main.py:24-29` |
| 2-6 | gTTSは逐次+失敗時最大99秒/件のリトライ。失敗記録がspot_id単位でクリアされず、同一spotの後続situationが連鎖的にクールダウンを食う。1件失敗でパック全体が500(書き込み済みMP3は孤児として残存、GCなし) | `voice/main.py:189-325` |
| 2-7 | 冪等性なし。再試行のたび `pack_id=uuid4()` で新パックがディスクに積み上がる | `nav/main.py:204` |
| 2-8 | リトライ/サーキットブレーカはgTTS以外に皆無。`tenacity` は依存に入っているが未使用 | `nav/client_*.py`, `requirements.txt:50` |

## 3. 対話エージェント内部(svc-agent)

| # | 所見 | 根拠 |
| --- | --- | --- |
| 3-1 | `agent.py` 1397行のうちLangGraphグラフ定義は実質48行。残りは簡繁変換表・スポット別名・序数表現・多言語ラベル・プロフィール正規化(約170行)・DBアクセス・`__main__`デモが同居 | `agent.py:47-620,1330-1397` |
| 3-2 | **`session_id` が常に `"default_session"`**。LangGraphのconfigを `state.get("configurable")` で取ろうとする誤りで、全ユーザー・全スレッドの会話が同一セッションとして保存される | `agent.py:1290-1291` |
| 3-3 | 会話履歴取得が `user_id` のみでフィルタ(スレッド無視)。**別旅程・別スレッドの会話が短期記憶に混入** | `db_client.py:81-89`, `agent.py:1045` |
| 3-4 | Chroma長期記憶に `user_id` メタデータを付けず、**全ユーザーの会話を横断検索**(文脈汚染+プライバシー) | `agent.py:1050,1307` |
| 3-5 | `MemorySaver`(プロセス内)のチェックポイント: 再起動で全スレッド状態消失、thread_idごとに状態が無制限にメモリ蓄積(TTLなし) | `agent.py:1376-1377` |
| 3-6 | intent解析失敗は4回リトライ後、無条件で `chitchat` に落として障害を無言で吸収。エラーもHTTP 200で返る | `agent.py:1133-1164,1207-1209` |
| 3-7 | import時に `Recommender()` を構築、FAISS(704KB)含む成果物5本をロード。**FAISSはロード後どこからも参照されない**。ファイル欠損時はサービス起動不能 | `agent.py:1331`, `recommender.py:23-38` |
| 3-8 | 実行時に書き換わるモジュールグローバル(簡繁マップの動的`update()`、ロックなしフラグ)、共有sklearnオブジェクト。並行リクエストでレース | `agent.py:104-210,1331` |
| 3-9 | プロンプト層(`prompts.py`)がCWD相対パスで知識MDを同期readし、RAG検索層を兼ねる | `prompts.py:580-600` |

## 4. データ層

| # | 所見 | 根拠 |
| --- | --- | --- |
| 4-1 | DBセッションをジェネレータ+`next()`で開き、`finally`(close)に到達しない。**1ターン5セッションがGC任せ**でプール枯渇リスク | `db_client.py:19-25`, `agent.py:980,1019,1041,1176,1288` |
| 4-2 | トランザクション境界なし。1ターンで個別コミット4回。途中失敗で部分更新が残る。ロールバック処理は全コードパスに不在 | `db_client.py:42,54,77` |
| 4-3 | プロフィールと旅程はJSONB丸ごと上書き(read-modify-writeなし)。揮発キャッシュ(`_last_recommendations`)まで同一カラムに同居。並行更新でlost update | `db_client.py:50-53`, `agent.py:1295` |
| 4-4 | 同一ユーザーを1ターンで4回SELECT。うち2回はlanguage引数なしで、レース時に言語jaの新規ユーザーを作りうる | `agent.py:981,1022,1042,1289` |
| 4-5 | PostgreSQLが2台(static/app)+Chroma+FAISSファイルの4系統。static側はORM定義が壊れており(import即`NameError`)、実体は生DDL+生SQLの二重管理。マイグレーションツールなし | `static_db_models.py:8-15`, `script/init_static_db.py` |
| 4-6 | static DBの既定DB名が実装箇所ごとに3種類(`static-db`/`static_db`/`nav_static`)に食い違う | `nav/spot_repo.py:28`, `routing/spot_repo.py:11`, `alongpoi/poi_repo.py:64` |
| 4-7 | ChromaのURLを`split("://")`で手パース。初期化失敗フラグが恒久化し、復旧してもプロセス再起動まで長期記憶が死んだまま。書き込み失敗はprintで握り潰し(サイレントデータロス) | `vector_store_client.py:34-68` |
| 4-8 | 埋め込みモデル変更で次元不一致→長期記憶が黙って無効化(運用手順のみで対処) | `21_architecture_asis.md` 注意書き |
| 4-9 | POIソースが二重(PostGISビューとfacilities.json直読み)。DB障害時はサイレントに縮退し、ファイル由来だけで「正常応答」 | `poi_repo.py:342-343,411-417` |

## 5. 契約(スキーマ)の重複と欠落

| # | 所見 | 根拠 |
| --- | --- | --- |
| 5-1 | 共有スキーマパッケージが存在せず、同じ型が最大4箇所に手書き再定義(`Coord`×3, `Segment`×4, `WaypointInfo`×3, `Leg`×3形式, situation Literal×4+フロント) | `api/schemas.py`, `nav/main.py`, `routing/main.py`, `alongpoi/main.py`, `llm/schemas.py`, `voice/main.py` |
| 5-2 | **nav側`Asset`に`text`フィールドがなく、LLM生成本文がレスポンスから黙って捨てられる**(Gateway側Assetは`text`を持つ) | `nav/main.py:55,288,305` vs `api/schemas.py:91` |
| 5-3 | `ChatRequest/ChatResponse` がGatewayとagentで独立定義され、既にdrift(itineraryの必須性が不一致) | `api/schemas.py:18-28` vs `agent_app/api.py:15-24` |
| 5-4 | `response_model=PlanResponse` 宣言と`JSONResponse`直返しの併用で契約検証がスキップされる。`_normalize_legs`が表記ゆれ(`from`/`from_`/`distance_m`/`distance`)を実行時吸収=契約が固まっていない証拠 | `nav_router.py:21,44-47`, `nav/main.py:117-131` |
| 5-5 | `(spot_id, situation)` の結合規約が3サービス+フロントの計7箇所に散在。situation 1種追加で7ファイル同時変更。along POIにはsituation版がないという非対称も暗黙 | `nav/main.py:70-75,235-251`, `llm/schemas.py:13`, `voice/main.py:68,219`, `NavView.vue:1137` |
| 5-6 | spot取得SQLがnavとroutingにコピペ二重実装(dataclass名だけ違う) | `nav/spot_repo.py:41-125` vs `routing/spot_repo.py:79-157` |

## 6. 正しさのバグ(調査中に発見)

| # | 所見 | 根拠 |
| --- | --- | --- |
| 6-1 | `osrm_client.py` は `logging` をimportせず `logger.warning/error` を3箇所で呼ぶ→**NameError**。外側exceptに飲まれ、「到達許容誤差超え」判定が本来と別経路で成立 | `osrm_client.py:126,136,141` |
| 6-2 | リアルタイム情報が欠損時、**`random.randint`で天気・混雑を捏造してDBに永続化**。データが乱数で汚染される | `realtime_router.py:69-71,82-83` |
| 6-3 | access_points DB障害時、「目的地の東0.01度」という無意味な座標をトレイルヘッドとしてフォールバックし経路生成 | `routing/logic.py:96-106` |
| 6-4 | `car_to_trailhead`/`return_to_origin` はリクエストで受けるが**参照されない**(Gatewayは常にTrue注入) | `routing/main.py:35-36,120-128` |
| 6-5 | 知識ベースの`md_slug`はnav→llmまで運ばれるが**retrieverが使わない**。さらにinitスクリプトは存在しないディレクトリを検証するため`md_slug`は常にNULL投入。結果、**知識MD 60件中17件が永久に到達不能** | `llm/retriever.py:76`, `script/init_static_db.py:314-317` |
| 6-6 | クライアント制御の`uuid`パラメータをサニタイズなしでログファイルパスに使用(パストラバーサル可)。デバイスごとの`RotatingFileHandler`を解放せずFDリーク | `api/main.py:20,30`, `logging_config.py:10-45` |
| 6-7 | CORSが`allow_origins`未指定+`allow_credentials=True`。オリジンリストはコメントアウト放置 | `api/main.py:67-80` |
| 6-8 | MQTT購読がGatewayプロセス内のdaemonスレッド(deprecated `on_event`起動)。複数ワーカーで動かすと多重購読+downlink重複送信。停止イベントは誰も参照しないデッドコード | `realtime_router.py:36,89-95,207-223` |
| 6-9 | プロセス内キャッシュ`_state`が無期限。他プロセスがDBを更新しても永久に反映されない | `realtime_router.py:34,64-66` |
| 6-10 | uplinkのBase64デコード文字列を無検証でDB upsertキーに採用 | `realtime_router.py:130-133` |
| 6-11 | `/health`が実体(gTTS)と異なる`xtts_v2`を返す。svc-agentには`/health`自体がない。依存先(DB/vLLM/Chroma)のチェックはどこにもない | `voice/main.py:142`, `agent_app/api.py` |

## 7. 死コード・残骸(削除候補)

| 対象 | 根拠 |
| --- | --- |
| `recommendation_engine.py` 全体(参照ゼロ。使用中の`recommender.py`と仕様が微妙に異なる二重実装で、どちらが正か判別不能) | `agent_app/recommendation_engine.py` |
| `static_db_models.py` 全体(import即NameError、参照ゼロ) | `worker/static_db_models.py:8-15` |
| Coqui XTTS一式(コメントアウトの死骸+ダミー無音生成+誰もimportしない`torch_patch.py`)。`.env`のCOQUI/VOICE_REF設定も全て未参照 | `voice/tts.py:16-116,213-218`, `voice/torch_patch.py`, `.env:24-33` |
| alongpoiバッファ方式一式(本番未使用。**なのにテストはこの死コードだけを検証している**) | `geo_ops.py:50-112`, `poi_repo.py:266-345`, `test_alongpoi_buffer.py` |
| 未参照env: `FORCE_INSERT`, `RT_MQTT_TOPIC`, `PACKS_DIR`, `PACKS_BASE_URL` | `.env:14,36-37,43` |
| `nav/client_routing.py`(importのみ)、`post_synthesize`、`CONDITIONAL_NARRATIONS`の値部分、`REQ_TIMEOUT`、pydantic v1互換分岐、コメントアウト済み旧スキーマ | `nav/main.py:15,70-75`, `api/schemas.py:6-11,56-63` |
| `chat_history`への追記(次ターンで丸ごと上書きされ実質デッド) | `agent.py:1311-1313` |
| `rename_md.py`が知識データ内にja/en/zh完全同一で3コピー | `data/knowledge/*/faci_spot/rename_md.py` |

## 8. 設定と再現性

| # | 所見 | 根拠 |
| --- | --- | --- |
| 8-1 | 中央設定なし。`os.getenv`が30箇所超に散在。`pydantic-settings`は依存に入っているが未使用 | `requirements.txt:13` ほか多数 |
| 8-2 | 同一設定に複数キー名(`Inference_server`/`INFERENCE_SERVER`)、タイムアウトが3系統の別env名 | `llm_client.py:23-34` |
| 8-3 | `Embedding_server`のLAN固有IPがコンテナから直接参照される(composeでの上書きは`Inference_server`のみ)。docstringにも私有IPが記載 | `.env`, `docker-compose.yml:242`, `llm_client.py:6` |
| 8-4 | composeにホスト固有絶対パス: コンテナ内で`/home/junta_takahashi/...`をmkdir、`/var/www/packs`マウント(nginx配信はcompose外の暗黙知) | `docker-compose.yml:212,220,230` |
| 8-5 | パス解決が3方式併存(CWD相対 / `__file__`基準 / env)。同一ディレクトリを別方式で解決 | `recommender.py:14`, `prompts.py:580`, `agent.py:224`, `retriever.py:14` |
| 8-6 | OSRMの`.osrm`データは`.gitignore`されており、**取得・生成手順がリポジトリのどこにも書かれていない**。クリーンチェックアウトから起動不能 | `.gitignore:13-14` |
| 8-7 | TTNのAPIキー平文が`.env`に置かれ、`env_file`で6コンテナへ無差別配布(Git追跡外であることは確認済み) | `.env:41-42`, `docker-compose.yml` |
| 8-8 | app-dbの資格情報既定値がcompose/スクリプト/.env実値で3系統。既定にフォールバックすると別DBを掴む | `docker-compose.yml:43-45`, `.env:15-19` |
| 8-9 | initコンテナの依存がDockerfile直書きでrequirements.txtと二重管理 | `script/Dockerfile.init:14-21` |
| 8-10 | `seed_spot_realtime.py`はspot_001〜044の決め打ち(実データ43件と不一致)、実行方法もどこにも書かれていない | `script/seed_spot_realtime.py:28-30` |

## 9. 可観測性

| # | 所見 | 根拠 |
| --- | --- | --- |
| 9-1 | agent側に構造化ログが皆無(`print` 39箇所)。応答本文をstdoutに出力(PII)。レベル・タイムスタンプ・相関IDなし | `agent_app/` 全体 |
| 9-2 | routerモジュールのimport時に`logging.basicConfig(DEBUG)`でルートロガーを破壊的に書き換え | `realtime_router.py:28` |
| 9-3 | Gatewayミドルウェアが全リクエスト/レスポンスボディをメモリ蓄積し`indent=2`で整形してファイル同期書き込み(routeは120KB超)。ストリーミング応答も壊す | `api/main.py:19-59` |
| 9-4 | request_idは生成されるがnavより先の下流サービスへ伝播しない。分散トレースの導線ゼロ | `nav_router.py:28` → `nav/main.py:203` 止まり |
| 9-5 | ターンごとのLLM呼び出し回数・所要時間などの実験計測が構造化されて残らない(NFR-7未充足) | — |

## 10. テスト

| # | 所見 | 根拠 |
| --- | --- | --- |
| 10-1 | `run_nav_test.sh`は削除済みの非同期API(`task_id`ポーリング)を前提としており**必ず失敗する** | `run_nav_test.sh:63-92` vs `nav/main.py:64-68` |
| 10-2 | systemテストは環境変数名・リクエスト形式・レスポンス期待値・モックURLの全てが現行実装と不一致で全滅 | `system/conftest.py:32`, `test_nav_plan_happy_path.py` |
| 10-3 | unitテストの一部は存在しないシンボルをimportして収集時エラー、または404固定の無効エンドポイントに200を期待 | `test_voice_tts.py:15`, `test_voice_endpoint.py:40` |
| 10-4 | 本番経路(navオーケストレーション、alongpoi本番クエリ、realtime、Gateway層、spot_repo群)のテストがゼロ。CIなし。`.pytest_cache`の失敗記録がツリーに残存 | `backend/test/navigation/` |
| 10-5 | pytest設定ファイルがなく、各conftestが`parents[4]`で`sys.path`を手動注入 | `*/conftest.py` |

## 11. ドキュメントと実装の乖離

| # | 所見 | 根拠 |
| --- | --- | --- |
| 11-1 | READMEが廃止済みRedisを現行スタックとして記載。ChromaDBと埋め込みvLLMのポート衝突(8001)も未説明 | `README.md:10,49,52` |
| 11-2 | `.env`はCoqui XTTS+CUDA前提のまま、実装はgTTS。voiceコンテナにGPU割り当てなし | `.env:24-33`, `voice/Dockerfile` |
| 11-3 | パック配信(nginx)とOSRMデータ準備が文書化されていない(リポジトリ外の暗黙知) | — |
| 11-4 | データディレクトリ内の`POIに関するメモ.txt`が暗黙の仕様書 | `data/POIに関するメモ.txt` |

## 12. 契約の実測不整合(フロント ⇔ Gateway)

| # | 所見 | 根拠 |
| --- | --- | --- |
| 12-1 | Gatewayが`JSONResponse`を直接返すため`response_model`の検証・シリアライズが**スキップ**され、宣言は文書上のみ。OpenAPIと実態が乖離 | `routing_router.py:37`, `nav_router.py:44-47` |
| 12-2 | フロントが再生選択キーに使う`Asset.situation`が**Gatewayスキーマに存在しない**。`extra="allow"`+検証バイパスで偶然動いている | `NavView.vue:844,1162` vs `api/schemas.py:91-105` |
| 12-3 | フロントのalong POIソートキー(`order_index`/`nearest_idx`)は**バックエンドが返さない**→ソートはno-op。`poi.category`参照も同様に常に空 | `NavView.vue:910,923` vs `reducer.py:57-65` |
| 12-4 | RTDocの`u`(今後の天気)/`h`(何時間後)は**供給源がバックエンドに存在しない**デッドUI。ETag/304はフロント側だけの片側実装(バックエンドはヘッダを読まず常に200) | `NavView.vue:1090-1092`, `rt.js:212-238`, `api.js:125-127` vs `realtime_router.py:228-231` |
| 12-5 | **`_normalize_legs`が緯度経度を取り違え**(polylineは`[lon,lat]`順なのに`lat: coord[0]`)。壊れた座標がmanifest.jsonに書き込まれる(フロントがlegs未使用のため未顕在) | `nav/main.py:110-114` |
| 12-6 | 全リクエストに`uuid`をクエリ+ボディへ暗黙注入。どのスキーマにも宣言されず、消費者はロギングミドルウェアのみ | `lib/api.js:13-40`, `api/main.py:20-30` |
| 12-7 | バッファ値の不整合: リクエストはcar 300m/foot 10m、フロントの再生判定は350m/15mの直書き | `api.js:100` vs `NavView.vue:834` |
| 12-8 | `Asset`は新形(`audio{url,...}`)と旧形(`audio_url`,...)の二重スキーマ+正規化validator。navは旧形のみ出力、フロントは両対応分岐を3箇所に散在 | `schemas.py:93-127`, `NavView.vue:456,799` |
| 12-9 | `manifest_url`はstoreに保存されるだけで**取得コードが存在しない**(「manifestに全情報を含める」設計が未実装) | `nav.js:114-121`, `schemas.py:174` |
| 12-10 | `/packs`はViteプロキシ対象外でdev環境では404。`/back`と`/packs`のリバースプロキシ契約(nginx)がリポジトリ外の暗黙知 | `vite.config.js:15-21` |
| 12-11 | OpenAPI/型生成/TypeScript/変換レイヤは一切なし。唯一の「契約」は`api.js`のJSDocと手書きtypedef(実装と乖離済み) | `lib/api.js`, `rt.js:29-36` |

## 13. フロントエンド

| # | 所見 | 根拠 |
| --- | --- | --- |
| 13-1 | `NavView.vue`が2,119行(フロント全体6,720行の31%)。責務分担の設計なし | `views/NavView.vue` |
| 13-2 | **Pinia二重初期化バグ**: `app.use(pinia)`直後に`app.use(createPinia())`を再実行し、persistedstateプラグインなしの2個目で上書き | `main.js:16-17` |
| 13-3 | チャットはストリーミングなしの単発ブロッキングfetch。クライアント側タイムアウト・リトライ・キャンセル(AbortController)は全て0件 | `api.js:43-44`, `chat.js:48` |
| 13-4 | LLM出力を`marked.parse()`+`v-html`で直挿し。サニタイザ(DOMPurify等)の依存なし → XSS面 | `OC_ChatMessage.vue:52,107` |
| 13-5 | Service Workerのタイルキャッシュが実質死亡(OSMホスト+`.png`前提だが実際はArcGIS・拡張子なし)。プリキャッシュ・クォータ再試行ロジック一式が空回り | `sw.js:15-20,120` vs `NavMap.vue:21` |
| 13-6 | navストアを`persist:true`でlocalStorageへ丸ごと永続化(GeoJSON+assets)。TTL判定はsetup時に一度だけ評価される実質デッドコード | `nav.js:9,131-134,181` |
| 13-7 | i18n: ライブラリなし、switch直書き。バックエンドは3言語対応済みだがフロントはエラー文言・トースト・状況別フォールバック等が日本語のみの非対称 | `LoginView.vue:22-28`, `NavView.vue:1091-1134` ほか |
| 13-8 | `import.meta.env`使用0件=ビルド時設定機構なし。Viteプロキシ先`http://api:8080`はcomposeサービス名直書きで、README記載のローカル起動手順では名前解決不能 | `vite.config.js:17` |
| 13-9 | タイルURLが2箇所に二重定義。認証はsessionStorageの有無のみ(トークン・有効期限・サーバ検証なし) | `tiles.js:2,7`, `NavMap.vue:21`, `router/index.js:42` |
| 13-10 | 未使用の残骸: `PlanForm.vue`(参照0)、`PlanView.vue`(到達不能)、`counter.js`、iconsコンポーネント5件、依存`idb`/`pathfinding`(import 0)。`vite-plugin-vue-devtools`が無条件有効で本番ビルドにも同梱 | `package.json`, `vite.config.js:12` |

## 14. 依存関係

| # | 所見 | 根拠 |
| --- | --- | --- |
| 14-1 | 全7コンテナが同一requirements.txtをインストール(faiss/pandas/sklearn/chromadb/shapelyを全サービスが持つ) | `backend/Dockerfile`, `docker-compose.yml` |
| 14-2 | import 0件の依存が7つ: `loguru`, `polyline`, `pydantic-settings`, `orjson`, `tenacity`, `python-frontmatter`, `requests` | `requirements.txt:13-51` |
| 14-3 | ベクトルストアが2系統併存(chromadb=会話記憶、faiss=未使用ロード)。`langgraph`は0.0.x系の極めて古いピン | `requirements.txt:33,36,44` |
| 14-4 | テスト依存(pytest/respx/pytest-asyncio)が本番requirementsに同居。dev分離なし | `requirements.txt:56-58` |
| 14-5 | frontendコンテナは起動のたびに`npm install`(npm ciではない)=非決定的インストール | `docker-compose.yml:164` |

## 15. リポジトリ衛生

| # | 所見 | 根拠 |
| --- | --- | --- |
| 15-1 | `POI.json`/`facilities.json`がフロント(`src/assets/`)とバック(`worker/data/`)に**バイト同一の二重コミット**。同期機構なし | `git ls-files`サイズ実測 |
| 15-2 | XTTS用参照音声wav 3本(計1.08MB)が追跡されているが現行gTTSでは使用不能。学習成果物(pkl/faiss)の再現手順も文書なし | `voice/refs/*.wav`, `agent_app/processed/` |
| 15-3 | `backend/test/navigation/.pytest_cache`(テスト失敗記録含む)が**git追跡下**。ルートの`.pytest_cache/`と`recoAI_chokai/`(完全に空)は未ignore | `git ls-files` |
| 15-4 | `backend/logs/`が37MB(root所有)。デバイスUUIDごとの無制限ログファイル生成が原因 | `api/logging_config.py` |
| 15-5 | ルートに91バイトの空`package-lock.json`(誤操作の副産物)が追跡されている。`package.json`は存在しない | `/package-lock.json` |
| 15-6 | リポジトリ直下の`packs/`は使われていない残骸(実配信は`/var/www/packs`)。所有者はwww-data/rootが混在 | `docker-compose.yml:220,230` |
| 15-7 | 陳腐化文書が`Docs/`外に散在: `agent_app/architecture_definition.md`(廃止済みOllama記載)、`learning_phase_architecture.md` | `backend/worker/agent_app/` |
| 15-8 | LICENSE/CHANGELOG/CI/lint/formatter/pre-commitのいずれも存在しない | `find`確認 |

---

## 16. 集約: 再設計で解くべき根本原因

1. **境界の誤設定** — プロセス境界(7サービス)は運用コストだけ生み、本当に必要な境界(ドメイン間の契約・型)は存在しない
2. **長時間処理の同期実行** — パック生成はバッチジョブなのにHTTP同期。対話はストリーミングなしの直列LLM呼び出し
3. **状態の多重管理** — DB2台+Chroma+FAISS+ファイル+プロセス内キャッシュ+フロント運搬。single source of truthがない
4. **契約の不在** — 型の重複定義、実行時の表記ゆれ吸収、検証スキップ。テストは古い契約を検証
5. **設定・手順の暗黙知化** — ホスト固有値の散在、未文書の外部依存(nginx/OSRMデータ)、動かない再現スクリプト
6. **可観測性の不在** — printベース、相関IDなし、実験計測が構造化されない
7. **フロントとの契約が「偶然動いている」状態** — 検証バイパス+`extra="allow"`+実行時の表記ゆれ吸収の三重の緩さの上に、存在しないフィールドを参照するデッドUIが載っている
