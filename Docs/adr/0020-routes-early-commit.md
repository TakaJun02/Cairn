# ADR-0020: 経路キャッシュ(`app.routes`)はターンの一括 commit を待たず即時 commit する

- 状態: **提案(2026-08-04)**
- 日付: 2026-08-04
- 関係: [ADR-0013](0013-leg-route-door-to-door.md)(1 レッグ = 1 route)を**崩さない** / [ADR-0019](0019-react-main-agent-subagents.md) の「persist(N6)が 1 トランザクションで一括 commit」に**例外を 1 つ設ける** / [geo.md §3.3](../30_design/geo.md) / [25_known_issues.md §1-1](../25_known_issues.md)

## 文脈(何が問題か)

旅程作成・更新のたびに、`state:itinerary`(final)を受けたフロントエンドの `GET /api/v1/routes/{route_id}` が **404 になる**(実機で毎回再現。[25_known_issues.md §1-1](../25_known_issues.md))。

原因は**トランザクション間の可視性**である。コード上の順序は「route を書く → SSE を送る」で正しいが、書き込みが送出時点で**可視になっていない**。

1. `POST /chat` はターン全体を** 1 つの長寿命トランザクション**で貫く(`chat.py` の `session_scope`)。commit するのは N6 persist だけ — これは会話状態(profile / itinerary / messages)の原子性のための設計である
2. `plan_itinerary` / `edit_itinerary` の後段でレッグごとの route が **この未コミットのトランザクション内に INSERT** される
3. `state:itinerary`(final, route_id 入り)は**即時に SSE へ送出**される(ストリーミング UX の要)
4. ブラウザの `GET /routes/{id}` は**別コネクション・別セッション**であり、READ COMMITTED では未コミット行が見えない → 404
5. ターン終端(respond のストリーミング完了後)の commit でようやく可視になる → 「後から取ると 200」

つまり 404 の窓は「SSE 送出からターン終端まで」= ReAct の残り周回 + respond のストリーミング全体であり、数秒〜十数秒ある。

## 決定(何をすると決めたか)

**`app.routes` への書き込みは、ターンの一括 commit を待たず、専用の短寿命セッションで即時 commit する。**

- `RouteService.get_or_create`(save_route)の永続化を、会話ターンの session ではなく**専用の一時 session で行い、その場で commit する**
- これにより **`state:itinerary`(final)送出時点で `GET /routes/{id}` が 200 を返すことを SSE 契約として保証する**([chat_sse.md](../40_api/chat_sse.md))
- 防御として、フロントエンドは 404 に対して短い再試行を持つ([frontend_nav.md §3](../30_design/frontend_nav.md))。ただしこれは保険であり、主修正はバックエンド側である

### なぜ例外にしてよいか

`app.routes` は **`params_hash` をキーとする内容アドレスの冪等キャッシュ**である(`ON CONFLICT DO NOTHING`。[geo.md §3.2](../30_design/geo.md))。

- **会話状態ではない。**ターンが失敗して rollback しても、残った route 行は「再利用可能なキャッシュエントリ」であり、どの旅程からも参照されなければ無害である
- 同型の先例が既にある: `pending_ask` はターン途中の可視性のために**専用の一時 session で別 commit する**(`ask_registry.write_pending_ask_now`)。本決定はそれと同じ形を `app.routes` に適用するだけである
- N6 の一括 commit が守るべき原子性(「ユーザーが見た旅程は保存されている」= 不変条件 4)は、旅程本体(`itineraries`)には引き続き適用される。route はその旅程が参照する**先に存在すべき**キャッシュであり、先行 commit はむしろ整合的である

### 注意(実装時)

- `persist_turn` は `threads` の自己デッドロックを避けるため意図的に `FOR UPDATE` を使っていない(`repository.py`)。routes 側の一時 session は `app.routes` 単表しか触らないため干渉しないはずだが、レビューで確認すること
- 12 レッグの並列取得それぞれが一時 session を持つとコネクションを食う。**(2026-08-04 レビューで具体化)** (a) **OSRM への HTTP 往復(タイムアウト既定 20 秒)を session の外に出す** — DB 接続を握ったまま外部 I/O を待たない。短い session で包むのはキャッシュ照会(`get_route_by_hash`)と保存(`save_route`)だけ。(b) **レッグの並列度を Semaphore で絞る**(例 4)。既定プールは size 5 + overflow 10 = 同時 15 接続であり、進行中ターン 1 本が主 session 1 を常時占有するため、無制限並列(12 レッグ一斉)では同時 2 ターンで飽和する(NFR-8 の「同時〜数十」に足りない)

## 採らなかった案

| 案 | 却下理由 |
| --- | --- |
| SSE 送出をターン終端の commit 後へ遅延 | 旅程カード・地図の即時描画(ストリーミング UX)を壊す。`ChatEventBuffer` が `done` だけを保留する設計にも反する |
| `state:itinerary` に GeoJSON を同梱して読み返し自体を無くす | [geo.md §3.3](../30_design/geo.md) 案 C(フロントが経路を保持して詰め直す構造)への逆行。SSE フレームも肥大する |
| フロントの再試行のみ | 症状への対処。404 の窓はターン残り全体で長さが不定であり、地図描画がその間ストールする |

## 帰結

- `state:itinerary`(final)の受信者は route_id を**即時に解決できる**(契約)
- rollback されたターンの route 行が残り得る(無害。内容アドレスなので次回再利用される)
- 「commit は N6 だけ」の原則は「**会話状態の commit は N6 だけ。冪等キャッシュ(`app.routes`)と `pending_ask` は例外**」に改める(追記先は [agent_react_architecture.md §12](../30_design/agent_react_architecture.md) 末尾)
