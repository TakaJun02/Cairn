# POI enrichment 根拠・未取得情報

## 調査方針

根拠は次の順に確認しました。

1. `backend/worker/data/knowledge/ja/**` の対応MDと横断MD
2. 入力の `osm_ids` を使ったOpenStreetMap element API / Overpass API
3. 入力の `tags`・`description` と知識MDの施設種別からの推定

公式サイトのWeb探索は行っていません。営業時間は、時刻または24時間利用が知識MDで明示された9件だけを文字列化しました。屋外の自然スポットはMDに「24時間」とあっても、指示書どおり `open_hours: null` / `open_hours_source: no_concept` としています。

`md_slug` と現行ファイル名の対応には `backend/worker/data/knowledge/ja/faci_spot/rename_md.py` の対応表を使いました。`spot_016` と `spot_017` は同じ元MDを複製した特例です。

## POIごとに採用した知識MD

表内のパスはすべて `backend/worker/data/knowledge/ja/` からの相対パスです。

| spot_id | 直接対応MD | 補助的に使ったMD・注意 |
| --- | --- | --- |
| `spot_001` | `faci_spot/spot_001.md` | `faci_spot/spot_nakajimadai.md`, `nature/nature_ecosystem_beech_forest.md` |
| `spot_002` | `faci_spot/spot_002.md` | `courses/course_tokozan.md` |
| `spot_003` | `faci_spot/spot_003.md` | `courses/course_yashiosan.md` |
| `spot_004` | `faci_spot/spot_004.md` | 入力は酒田市、MDは由利本荘市で不一致。季節値は要確認 |
| `spot_005` | `faci_spot/spot_005.md` | `courses/course_fukura_oodaira.md`, `courses/course_hokodate.md`, `courses/course_kisakata.md`, `access/access_fukura_oodaira.md`, `access/access_hokodate.md`, `safety-and-preparation/safety_chokai_characteristics.md`, `safety-and-preparation/safety_chokai_snow_and_rock.md` |
| `spot_006` | `faci_spot/spot_006.md` | なし |
| `spot_007` | `faci_spot/spot_007.md` | `courses/course_hottai_tamada.md`, `history-and-culture/history_spot_hottai_falls.md` |
| `spot_008` | `faci_spot/spot_008.md` | `history-and-culture/history_spot_juroku_rakan.md` |
| `spot_009` | `faci_spot/spot_009.md` | なし |
| `spot_010` | `faci_spot/spot_010.md` | なし |
| `spot_011` | `faci_spot/spot_011.md` | 物産館・温泉等の時刻が異なるため、単一営業時間は不採用 |
| `spot_012` | `faci_spot/spot_012.md` | `nature/nature_ecosystem_wetlands.md` |
| `spot_013` | `faci_spot/spot_013.md` | `faci_spot/spot_nakajimadai.md`, `nature/nature_ecosystem_wetlands.md` |
| `spot_014` | `faci_spot/spot_014.md` | 時刻・休館日ともMD内に「要確認」表記あり |
| `spot_015` | `faci_spot/spot_015.md` | `courses/course_haraigawa.md`, `access/access_yashima.md`, `faci_spot/facility_trailhead_haraigawa.md`, `nature/nature_ecosystem_wetlands.md` |
| `spot_016` | `faci_spot/spot_016.md` | `courses/course_ninotaki.md`, `access/access_ninotaki.md`, `faci_spot/facility_trailhead_ichinotaki.md` |
| `spot_017` | `faci_spot/spot_017.md` | `spot_016` と同じ複合MD。`courses/course_ninotaki.md`, `access/access_ninotaki.md` |
| `spot_018` | `faci_spot/spot_018.md` | なし |
| `spot_019` | `faci_spot/spot_019.md` | `history-and-culture/history_spot_chokai_shrine.md`, `faci_spot/facility_hut_omuro.md`, 山頂へ至る各コースMD |
| `spot_020` | `faci_spot/spot_020.md` | 開館時間は「事前に要確認」のみ |
| `spot_021` | `faci_spot/spot_021.md` | 拝観時間は「日中（要確認）」のみ |
| `spot_022` | `faci_spot/spot_022.md` | 断崖・海岸遊歩道の記述を安全判定に使用 |
| `spot_023` | `faci_spot/spot_023.md` | 営業期間は「主に夏期」のみで、月は推定 |
| `spot_024` | `faci_spot/spot_024.md` | `courses/course_haraigawa.md`, `access/access_yashima.md` |
| `spot_025` | `faci_spot/spot_025.md` | `courses/course_haraigawa.md`, `access/access_yashima.md` |
| `spot_026` | `faci_spot/spot_026.md` | 日帰り入浴時間・無休・露天風呂の冬季制約を採用 |
| `spot_027` | `faci_spot/spot_027.md` | 日帰り入浴可の記述はあるが時刻なし |
| `spot_028` | `faci_spot/spot_028.md` | 温泉・レストラン・物産館の記述はあるが時刻なし |
| `spot_029` | `faci_spot/spot_029.md` | 温泉・宿泊・食事の記述はあるが時刻なし |
| `spot_030` | `faci_spot/spot_030.md` | 日帰り入浴可の記述はあるが時刻なし |
| `spot_031` | `faci_spot/spot_031.md` | `courses/course_fukura_oodaira.md`, `courses/course_hokodate.md`, `courses/course_kisakata.md` |
| `spot_032` | `faci_spot/spot_032.md` | `courses/course_takinokoya.md`, `courses/course_yunodai.md`, `access/access_yunodai.md` |
| `spot_033` | `faci_spot/spot_033.md` | `courses/course_momoyake.md`, `access/access_momoyake.md` |
| `spot_034` | `faci_spot/spot_034.md` | `courses/course_yashima.md`, `access/access_yashima.md` |
| `spot_035` | `faci_spot/spot_035.md` | `nature/nature_ecosystem_wetlands.md` |
| `spot_036` | `faci_spot/spot_036.md` | `faci_spot/facility_base_hanadate.md`。施設別営業で単一時刻・季節を特定不能 |
| `spot_037` | `faci_spot/spot_037.md` | 境内24時間参拝の記述を採用 |
| `spot_039` | `faci_spot/spot_039.md` | 河川・水遊びの記述から天候適性を推定 |
| `spot_040` | `faci_spot/spot_040.md` | 河川・水遊びの記述から天候適性を推定 |
| `spot_041` | `faci_spot/spot_041.md` | このMDは別施設「鳥海山荘」のため不採用。`faci_spot/spot_sarukura_onsen.md`, `access/access_sarukura.md` を代替参照 |
| `spot_042` | `faci_spot/spot_042.md` | 木道・見頃の記述を採用。通年アクセスは推定 |
| `spot_043` | `faci_spot/spot_043.md` | `faci_spot/facility_base_hanadate.md` |
| `spot_044` | `faci_spot/spot_044.md` | `courses/course_mansuke.md`, `access/access_mansuke.md` |

## 横断確認した知識MD

次のディレクトリは、該当POIだけを検索するのではなく全ファイルを読みました。

- `access/` 10件: `access_fukura_oodaira.md`, `access_general.md`, `access_hokodate.md`, `access_mansuke.md`, `access_momoyake.md`, `access_nagasaka.md`, `access_ninotaki.md`, `access_sarukura.md`, `access_yashima.md`, `access_yunodai.md`
- `courses/` 17件: `course_fukura_oodaira.md`, `course_haraigawa.md`, `course_hokodate.md`, `course_hottai_tamada.md`, `course_kisakata.md`, `course_koshikiyama.md`, `course_kuwanokidai.md`, `course_mansuke.md`, `course_momoyake.md`, `course_nagasaka.md`, `course_ninotaki.md`, `course_sarukura.md`, `course_takinokoya.md`, `course_tokozan.md`, `course_yashima.md`, `course_yashiosan.md`, `course_yunodai.md`
- `nature/` 9件: 動物3件、生態系2件、概要2件、植物2件。湿原・湧水・ブナ林と見頃の補助根拠に使用
- `safety-and-preparation/` 7件: 準備3件、安全4件。山岳POIの天候急変、濃霧、雪渓、岩場、増水リスクの補助根拠に使用

鳥海ブルーラインは `access/access_fukura_oodaira.md` に「11月上旬から4月下旬頃まで冬期閉鎖」、`access/access_hokodate.md` に冬期閉鎖と記載されています。祓川口は `access/access_yashima.md` に「11月上旬～4月下旬頃」閉鎖とあります。

## OSM `opening_hours` 調査結果

### 集計

| 結果 | 件数 | 注記 |
| --- | ---: | --- |
| `opening_hours` タグあり | 0 | 取得成功した要素がないため、存在を確認できた件数は0 |
| `opening_hours` タグなし | 0 | 取得成功した要素がないため、「タグなし」と判定した件数も0 |
| 取得不能 | 38 | OSM IDあり38件すべて |
| OSM IDなし・未試行 | 5 | `spot_035`, `spot_036`, `spot_039`, `spot_040`, `spot_043` |

2026-07-31にelement APIを試行しましたが、実行環境では `api.openstreetmap.org` のDNS解決に失敗しました。別経路でelement APIとOverpass APIのURLも試しましたが、API URLへのアクセスが環境の安全制約で拒否されました。そのため、38件は「タグなし」ではなく「取得不能」です。ネットワークが使える環境で下表のURLを再実行してください。

| spot_id | OSM element | element API URL | 結果 |
| --- | --- | --- | --- |
| `spot_001` | `node/2636183763` | `https://api.openstreetmap.org/api/0.6/node/2636183763.json` | 取得不能 |
| `spot_002` | `way/863048484` | `https://api.openstreetmap.org/api/0.6/way/863048484.json` | 取得不能 |
| `spot_003` | `node/2833676624` | `https://api.openstreetmap.org/api/0.6/node/2833676624.json` | 取得不能 |
| `spot_005` | `way/91551305` | `https://api.openstreetmap.org/api/0.6/way/91551305.json` | 取得不能 |
| `spot_006` | `way/994409242` | `https://api.openstreetmap.org/api/0.6/way/994409242.json` | 取得不能 |
| `spot_007` | `node/529343277` | `https://api.openstreetmap.org/api/0.6/node/529343277.json` | 取得不能 |
| `spot_008` | `node/6698541468` | `https://api.openstreetmap.org/api/0.6/node/6698541468.json` | 取得不能 |
| `spot_009` | `node/9722431027` | `https://api.openstreetmap.org/api/0.6/node/9722431027.json` | 取得不能 |
| `spot_010` | `node/7578006017` | `https://api.openstreetmap.org/api/0.6/node/7578006017.json` | 取得不能 |
| `spot_011` | `way/435104755` | `https://api.openstreetmap.org/api/0.6/way/435104755.json` | 取得不能 |
| `spot_012` | `node/5676101922` | `https://api.openstreetmap.org/api/0.6/node/5676101922.json` | 取得不能 |
| `spot_013` | `node/2636183765` | `https://api.openstreetmap.org/api/0.6/node/2636183765.json` | 取得不能 |
| `spot_014` | `way/921726074` | `https://api.openstreetmap.org/api/0.6/way/921726074.json` | 取得不能 |
| `spot_015` | `node/7669521356` | `https://api.openstreetmap.org/api/0.6/node/7669521356.json` | 取得不能 |
| `spot_016` | `node/755607568` | `https://api.openstreetmap.org/api/0.6/node/755607568.json` | 取得不能 |
| `spot_017` | `node/8831834671` | `https://api.openstreetmap.org/api/0.6/node/8831834671.json` | 取得不能 |
| `spot_018` | `way/1057635935` | `https://api.openstreetmap.org/api/0.6/way/1057635935.json` | 取得不能 |
| `spot_019` | `way/370068010` | `https://api.openstreetmap.org/api/0.6/way/370068010.json` | 取得不能 |
| `spot_020` | `way/1028270186` | `https://api.openstreetmap.org/api/0.6/way/1028270186.json` | 取得不能 |
| `spot_021` | `node/4380187991` | `https://api.openstreetmap.org/api/0.6/node/4380187991.json` | 取得不能 |
| `spot_022` | `way/736887604` | `https://api.openstreetmap.org/api/0.6/way/736887604.json` | 取得不能 |
| `spot_024` | `node/7669521366` | `https://api.openstreetmap.org/api/0.6/node/7669521366.json` | 取得不能 |
| `spot_025` | `node/755471150` | `https://api.openstreetmap.org/api/0.6/node/755471150.json` | 取得不能 |
| `spot_037` | `way/810491343` | `https://api.openstreetmap.org/api/0.6/way/810491343.json` | 取得不能 |
| `spot_042` | `relation/18072588` | `https://api.openstreetmap.org/api/0.6/relation/18072588.json` | 取得不能 |
| `spot_004` | `node/7715632985` | `https://api.openstreetmap.org/api/0.6/node/7715632985.json` | 取得不能 |
| `spot_023` | `node/5021916055` | `https://api.openstreetmap.org/api/0.6/node/5021916055.json` | 取得不能 |
| `spot_026` | `node/5862583451` | `https://api.openstreetmap.org/api/0.6/node/5862583451.json` | 取得不能 |
| `spot_027` | `node/683962544` | `https://api.openstreetmap.org/api/0.6/node/683962544.json` | 取得不能 |
| `spot_028` | `way/279126436` | `https://api.openstreetmap.org/api/0.6/way/279126436.json` | 取得不能 |
| `spot_029` | `way/814130444` | `https://api.openstreetmap.org/api/0.6/way/814130444.json` | 取得不能 |
| `spot_030` | `node/4880144022` | `https://api.openstreetmap.org/api/0.6/node/4880144022.json` | 取得不能 |
| `spot_031` | `way/713606173` | `https://api.openstreetmap.org/api/0.6/way/713606173.json` | 取得不能 |
| `spot_032` | `way/954237786` | `https://api.openstreetmap.org/api/0.6/way/954237786.json` | 取得不能 |
| `spot_033` | `node/12228635399` | `https://api.openstreetmap.org/api/0.6/node/12228635399.json` | 取得不能 |
| `spot_034` | `way/370274930` | `https://api.openstreetmap.org/api/0.6/way/370274930.json` | 取得不能 |
| `spot_041` | `node/1831766493` | `https://api.openstreetmap.org/api/0.6/node/1831766493.json` | 取得不能 |
| `spot_044` | `way/940355260` | `https://api.openstreetmap.org/api/0.6/way/940355260.json` | 取得不能 |

## 見つからなかったもの・手入力作業リスト

### 正確な営業時間が見つからなかった14件

- `spot_002` 赤田の大仏
- `spot_004` 鳥海高原家族旅行村
- `spot_011` 道の駅象潟
- `spot_020` 旧青山本邸
- `spot_021` 蚶満寺
- `spot_023` 西浜コテージ村・キャンプ場
- `spot_027` フォレスタ鳥海
- `spot_028` 黄桜温泉 湯楽里
- `spot_029` ぽぽろっこ
- `spot_030` 鳥海温泉 遊楽里
- `spot_031` 御浜小屋
- `spot_032` 滝ノ小屋
- `spot_036` 鳥海高原花立牧場公園
- `spot_041` 猿倉温泉 鳥海荘

この14件は `open_hours: null` / `open_hours_source: not_found` です。道の駅象潟と花立牧場公園は部分的な時刻記載があっても、施設全体を表す単一の営業時間にならないため採用していません。

### 季節値が推定・暫定の19件

- 閉鎖月を推定: `spot_007`, `spot_016`, `spot_017`, `spot_019`, `spot_023`, `spot_041`
- 閉鎖月不明のため `[]` を暫定設定: `spot_002`, `spot_011`, `spot_020`, `spot_021`, `spot_027`, `spot_028`, `spot_029`, `spot_030`, `spot_036`, `spot_039`, `spot_040`, `spot_042`, `spot_043`

特に `[]` は「通年」を意味するため、後者13件はユーザー確認後に閉鎖月が判明すれば必ず置き換えてください。

## 値の分布

### 入力と confidence

| 値 | 件数 |
| --- | ---: |
| `source_file: POI.json` | 30 |
| `source_file: facilities.json` | 13 |
| `confidence: low` | 6 |
| `confidence: medium` | 37 |
| `confidence: high` | 0 |

### `stay_min`

| 分 | 件数 |
| ---: | ---: |
| 15 | 2 |
| 20 | 9 |
| 30 | 10 |
| 45 | 8 |
| 60 | 6 |
| 90 | 6 |
| 120 | 2 |

未使用の許容値は10分・180分です。

### `weather_fit`

| 値 | 件数 |
| --- | ---: |
| `indoor` | 11 |
| `rain_ok` | 4 |
| `rain_poor` | 20 |
| `rain_unsafe` | 8 |

### `visit_difficulty`

| 値 | 件数 |
| --- | ---: |
| `no_walk` | 18 |
| `short_walk` | 17 |
| `long_walk` | 1 |
| `hike` | 7 |

### `open_hours`

| 値 | 件数 |
| --- | ---: |
| `null` | 34 |
| `24/7` | 6 |
| `Tu-Su 09:00-16:30` | 1 |
| `Mo-Tu,Th-Su 09:00-17:00` | 1 |
| `Mo-Su 11:00-21:00` | 1 |

`open_hours_source` は `knowledge_md` 9件、`no_concept` 20件、`not_found` 14件、`osm` 0件です。

### `season_closed_months`

| 配列 | 件数 |
| --- | ---: |
| `[]` | 28 |
| `[12,1,2,3]` | 1 |
| `[12,1,2,3,4]` | 5 |
| `[11,12,1,2,3]` | 1 |
| `[11,12,1,2,3,4]` | 3 |
| `[11,12,1,2,3,4,5]` | 2 |
| `[10,11,12,1,2,3,4,5]` | 1 |
| `[10,11,12,1,2,3,4,5,6]` | 1 |
| `[9,10,11,12,1,2,3,4,5,6]` | 1 |
