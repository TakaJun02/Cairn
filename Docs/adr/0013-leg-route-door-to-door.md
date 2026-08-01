# ADR-0013: 経路をレッグ単位で持ち、車+徒歩を door-to-door の 1 本にまとめる

- 状態: **決定 (2026-08-01、Phase 2 設計)**
- 日付: 2026-08-01
- 関係: [ADR-0005](0005-itinerary-solver.md)(ソルバーが移動時間行列を必要とする)/ [ADR-0014](0014-osrm-area-extract.md)(この経路が引かれる地図データ)
- 設計: [30_design/geo.md](../30_design/geo.md) / [30_design/data_model.md §3](../30_design/data_model.md)

## 文脈(何が問題か)

対象の 43 地点には、**車道から離れた滝・湿原・登山道の先**がある。「車で駐車場まで行き、そこから歩く」が日常的に起きるエリアである。

旧実装はこれを次のように扱っていた。

| 旧実装 | 実態 |
| --- | --- |
| `car_to_trailhead` / `return_to_origin` をリクエストで受ける | **参照されない。**Gateway は常に `True` を注入していた([22 §6-4](../22_current_issues.md)) |
| 車ルートの終点が目的地から 50 m 以上離れていたら「到達失敗」と判定 | 判定の考え方は妥当。ただし `logger` 未 import で **NameError** が外側の except に飲まれていた([22 §6-1](../22_current_issues.md)) |
| 失敗したら最寄り access_point 経由で car + foot に分割 | **DB 障害時は「目的地の東 0.01 度」を返す**([22 §6-3](../22_current_issues.md)) |
| **移動時間** | **徒歩ぶんが入らない。**駐車場に着いた時刻を「到着」としていた |
| 経路の永続化 | **無い。**フロントが `/api/route` の結果を保持して `/api/nav/plan` に詰め直していた([22 §1-4](../22_current_issues.md)) |

最後から 2 つ目が最も重い。[recommendation_planning.md §4.0](../30_design/recommendation_planning.md) で**時間割付きの旅程**を採った以上、**徒歩 10 分の滝を 10 分短く見積もる**のは仕様の破れである。

## 決定(何をすると決めたか)

1. **1 レッグ = 起点から終点までの door-to-door の移動 1 本。**内部に mode の違う **1〜2 本のセグメント**(car / car+foot / foot)を持つ
2. **接近情報を先に解いて DB に置く。**`static.spot_approach`(43 行)が「車がどこまで入れるか」「そこから徒歩何分か」を持つ。実行時に判定しない
3. **`static.travel_times` の値は door-to-door。**`walk_sec(i) + drive(car_node_i → car_node_j) + walk_sec(j)`
4. **`car_to_trailhead` / `return_to_origin` フラグをスキーマから削除する**
5. **経路(`app.routes`)はレッグ単位で永続化し、`params_hash` で冪等にする。**`params` には**地図データのビルド識別子 `osrm_build` を含める**
6. **`travel_times` に欠損があればコマンドを失敗させる。**直線距離 × 係数のような推定で埋めない

## 理由(なぜ他案でなくこれか)

### 1. 「どう着くか」は地点だけで決まる

車をどこに置き、そこから何分歩くかは、**どこから来たかに依存しない。**43 件を一度計算しておけば、実行時の分岐がゼロになる。旧実装が毎回「車ルートを引いて終点を比べて、だめなら access_point を探す」をしていたのは、**地点の属性を経路の問題として解いていた**からである。

### 2. 時間は 1 つの値でなければならない

ソルバーは「9:40 に着いて 45 分いて 10:25 に出る」を計算する。ここに要るのは玄関から玄関までの時間である。**駐車場までの時間と徒歩時間を別々に持つと、足し忘れる場所ができる。**足した値を 1 つ持つ。

### 3. レッグ単位にすると版をまたいで再利用できる

旅程は編集のたびに再ソルブされ、版が増える([data_model.md §4.5.5](../30_design/data_model.md))。「宿 → 元滝」の経路は版が変わっても同じで、`params_hash` が同じなら既存行を返せる。**10 版作っても OSRM 呼び出しは新しいレッグのぶんだけ**になる。1 日単位・旅程全体単位ではこれができず、`ItineraryItem.leg_from_prev.route_id` からも指せない。

### 4. 受けて無視するフラグを残さない

`return_to_origin` は `ItineraryDay.destination` が明示的に持つ([data_model.md §4.5.2](../30_design/data_model.md))。`car_to_trailhead` はデータから決まる挙動になった。**どちらも表現の場所が別にある**ので、フラグは削除する。

### 5. 推定値を混ぜない

旅程の時刻はユーザーに提示され、パックに焼かれ、現地で使われる。**推定と実測が混ざった行列は、どこが推定なのか後から分からない。**同じ理由で、OSRM 断のときに直線距離で経路を作ることもしない(地図に描かれ、パックに焼かれる線に嘘を混ぜない)。「東へ 0.01 度」のフォールバック([22 §6-3](../22_current_issues.md))を復活させないという判断の一般化である。

### 6. 採らなかった案

| 案 | 不採用の理由 |
| --- | --- |
| route を 1 日単位 / 旅程全体単位で持つ | `leg_from_prev.route_id` から指せない。版ごとに全部引き直しになる |
| `travel_times` を駐車場までの時間にし、徒歩を別に持つ | 足し忘れが起きる。ソルバーの式が長くなる |
| `spot_approach` を `spots` の列にする | OSRM が要る派生データと、シード由来のデータを同じテーブルに混ぜない |
| 到達判定を「車ルートを引いて終点を比べる」で続ける | OSRM の `/nearest` が同じことを 1 呼び出しで返す |
| OSRM 断のとき直線距離 × 係数で経路を作る | §5 |
| フラグを残して実装する | 使う場面がない。**旅程が持つ情報の重複になる** |

## 影響(この決定で生じる制約・やること)

- **`static.access_points` の DDL を新設する**(旧構成は GeoJSON 直読みで DDL が無かった)。`access=private` の 2 件は接近候補から除外する
- **`static.spot_approach` を新設する**(43 行)。生成は `python -m app.cli build-geo`(OSRM 必須)
- **`app.routes` の列を変更する**: `params_hash`(UNIQUE)/ `mode_summary` / `distance_m` / `duration_sec` / `segments` を追加し、**`waypoints_info` を廃止**する
- **[data_model.md](../30_design/data_model.md) を更新する**(§3 の `travel_times` の意味、§4.7 の `routes`、新テーブル 2 つ)
- **`car_to_trailhead` / `return_to_origin` を API スキーマから削除する**
- **`build-geo` の失敗はデータの問題である。**フォールバックが無いので、`access_points.geojson` に駐車場を足すか bbox を広げて対処する
- **`leg_from_prev.mode`(行列のどちらの行を使ったか)と `routes.mode_summary`(実際のセグメント構成)は別物**である。一致させようとしない([geo.md §1.3](../30_design/geo.md))
- Phase 1 の実装(ILS ソルバー)は `travel_times` を必要とする。**`build-geo` / `build-travel-times` と OSRM の再ビルドは Phase 1 の着手前に済ませる**
