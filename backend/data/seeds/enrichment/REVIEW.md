# POI enrichment 目視補正リスト

`confidence` の昇順（`low` → `medium` → `high`）です。同一 confidence 内は `spot_id` 順です。`—` は閉鎖月なしの暫定値、`null` は営業時間未設定を表します。

## 特に確認してほしい 15 件

- `confidence: low` 6件: `spot_004`, `spot_017`, `spot_019`, `spot_023`, `spot_036`, `spot_041`。対応MDの不一致、複合POI、曖昧な季節表現が主因です。
- `rain_unsafe` 8件: `spot_005`, `spot_019`, `spot_022`, `spot_031`, `spot_032`, `spot_033`, `spot_034`, `spot_044`。安全 pre-filter から除外されるため、雨・荒天時の扱いを優先確認してください。
- 閉鎖月を推定で入れた6件: `spot_007`, `spot_016`, `spot_017`, `spot_019`, `spot_023`, `spot_041`。MDの「冬期」「夏期」等を月へ丸めており、月境界は未確認です。
- 上記3群の重複を除いた優先確認対象が15件です。

## 全43件

| spot_id | 名称 | stay_min | weather_fit | visit_difficulty | open_hours | 閉鎖月 | confidence | 要確認の理由 |
| --- | --- | ---: | --- | --- | --- | --- | --- | --- |
| spot_004 | 鳥海高原家族旅行村 | 120 | rain_poor | short_walk | null | 11,12,1,2,3 | low | 入力は酒田市、対応MDは由利本荘市で所在地不一致。営業時間も未取得 |
| spot_017 | 一ノ瀧神社 | 20 | rain_poor | short_walk | null | 12,1,2,3,4 | low | `spot_016` と同一の「一ノ滝・二ノ滝」MDで、神社固有情報がない。閉鎖月は推定 |
| spot_019 | 鳥海山大物忌神社 | 30 | rain_unsafe | hike | 24/7 | 10,11,12,1,2,3,4,5,6 | low | 名称・MDは山頂本社と2口ノ宮を包含する一方、座標は山頂。閉鎖月は隣接小屋から推定 |
| spot_023 | 西浜コテージ村・キャンプ場 | 60 | rain_poor | no_walk | null | 10,11,12,1,2,3,4,5 | low | 「主に夏期」から6～9月営業を推定。正確な営業月・時間が不明 |
| spot_036 | 鳥海高原花立牧場公園 | 120 | rain_poor | short_walk | null | — | low | 施設ごとに営業期間・時間が異なるため、単一値を設定できず季節も空配列で暫定 |
| spot_041 | 猿倉温泉 鳥海荘 | 90 | indoor | no_walk | null | 12,1,2,3 | low | 対応 `spot_041.md` は別施設「鳥海山荘」。別MDの冬期道路閉鎖から月を推定 |
| spot_001 | あがりこ大王 | 20 | rain_poor | short_walk | null | 12,1,2,3,4 | medium | 滞在時間と徒歩負荷は初期値。5～11月頃の月境界を確認 |
| spot_002 | 赤田の大仏 | 30 | indoor | no_walk | null | — | medium | MDは「日中」のみで時刻化せず。通年設定と駐車場からの徒歩負荷を確認 |
| spot_003 | ボツメキ湧水 | 15 | rain_ok | no_walk | null | — | medium | 自然スポットのため24時間記載を `no_concept` 扱い。滞在・徒歩負荷は推定 |
| spot_005 | 鳥海湖 | 30 | rain_unsafe | hike | null | 11,12,1,2,3,4 | medium | 安全除外対象。ブルーライン以外の到達路もあるため閉鎖月の適用範囲を確認 |
| spot_006 | 遊佐町総合運動公園 | 60 | rain_poor | no_walk | null | — | medium | 園内で利用する設備により滞在時間・徒歩負荷が変わる |
| spot_007 | 法体の滝 | 45 | rain_poor | no_walk | null | 12,1,2,3,4 | medium | 冬期道路閉鎖と推奨期から閉鎖月を推定。開通・閉鎖の月境界を確認 |
| spot_008 | 十六羅漢 | 30 | rain_poor | short_walk | null | — | medium | 海岸岩場の徒歩負荷と、荒天時を `rain_unsafe` に上げる必要があるか確認 |
| spot_009 | 釜磯の湧水 | 20 | rain_poor | short_walk | null | — | medium | 駐車場から湧水地点までの実歩行時間と海況による安全性を確認 |
| spot_010 | 奈曽の白滝 | 30 | rain_poor | no_walk | null | — | medium | 滝壺側まで下りる利用を標準に含めるなら徒歩負荷・滞在時間の増加が必要 |
| spot_011 | 道の駅象潟 | 60 | indoor | no_walk | null | — | medium | 物産館・温泉等で営業時間が異なる。推薦対象とする代表機能を決めて時間を入力 |
| spot_012 | 元滝伏流水 | 45 | rain_poor | short_walk | null | — | medium | 往復歩行を滞在時間に含める運用かを確認。冬季アクセス制約の記載なし |
| spot_013 | 鳥海マリモ | 30 | rain_poor | long_walk | null | 12,1,2,3,4 | medium | 観察地点まで片道30～40分。ソルバーでこの徒歩時間を別扱いできるか確認 |
| spot_014 | にかほ市 象潟郷土資料館 | 60 | indoor | no_walk | Tu-Su 09:00-16:30 | — | medium | MD自体が営業時間・休館日を「要確認」としている。祝翌日・年末年始も要補正 |
| spot_015 | 竜ヶ原湿原 | 45 | rain_poor | short_walk | null | 11,12,1,2,3,4 | medium | 高地湿原を `rain_poor` に留めるか、濃霧時の安全除外を強めるか確認 |
| spot_016 | 二ノ滝 | 45 | rain_poor | short_walk | null | 12,1,2,3,4 | medium | 冬期アクセス不可から閉鎖月を推定。増水時を `rain_unsafe` にするか確認 |
| spot_018 | 丸池様 | 20 | rain_poor | short_walk | null | — | medium | 専用駐車場から池までの徒歩時間がMDにないため負荷を推定 |
| spot_020 | 旧青山本邸 | 60 | indoor | no_walk | null | — | medium | 開館時間・休館日・季節情報が見つからず、通年を暫定設定 |
| spot_021 | 蚶満寺 | 45 | rain_ok | short_walk | null | — | medium | 拝観時間は「日中（要確認）」のみ。境内の徒歩負荷と通年設定も推定 |
| spot_022 | 三崎公園 | 45 | rain_unsafe | short_walk | null | — | medium | 安全除外対象。断崖遊歩道の雨・強風時リスクが全園に当たるか確認 |
| spot_024 | 祓川神社 | 15 | rain_poor | no_walk | 24/7 | 11,12,1,2,3,4 | medium | 境内24時間とアクセス道路閉鎖を分離。開閉月の境界を確認 |
| spot_025 | 赤滝 | 20 | rain_poor | short_walk | null | 11,12,1,2,3,4,5 | medium | 徒歩約20分は難易度境界。増水時を `rain_unsafe` にするか確認 |
| spot_026 | 湯の台温泉 鳥海山荘 | 90 | indoor | no_walk | Mo-Su 11:00-21:00 | — | medium | 日帰り入浴の時刻を採用。臨時休館と1～4月の露天風呂閉鎖は未構造化 |
| spot_027 | フォレスタ鳥海 | 90 | indoor | no_walk | null | — | medium | 日帰り入浴・食事の正確な営業時間が未取得。通年営業も暫定 |
| spot_028 | 黄桜温泉 湯楽里 | 90 | indoor | no_walk | null | — | medium | 日帰り入浴の正確な営業時間・休館日が未取得。通年営業も暫定 |
| spot_029 | ぽぽろっこ | 90 | indoor | no_walk | null | — | medium | 温泉・レストラン・物産館の代表営業時間が未取得。通年営業も暫定 |
| spot_030 | 鳥海温泉 遊楽里 | 90 | indoor | no_walk | null | — | medium | 日帰り入浴の正確な営業時間・休館日が未取得。通年営業も暫定 |
| spot_031 | 御浜小屋 | 30 | rain_unsafe | hike | null | 9,10,11,12,1,2,3,4,5,6 | medium | 安全除外対象。7月上旬～8月下旬の営業日と日中利用時間を確認 |
| spot_032 | 滝ノ小屋 | 30 | rain_unsafe | hike | null | 11,12,1,2,3,4,5 | medium | 安全除外対象。日中利用時間が不明。入力説明の鉾立ルート記載もMDと不一致 |
| spot_033 | 唐獅子平避難小屋 | 20 | rain_unsafe | hike | 24/7 | — | medium | 安全除外対象。通年利用可でも冬は雪に埋もれるため、空の閉鎖月でよいか確認 |
| spot_034 | 七ツ釜避難小屋 | 20 | rain_unsafe | hike | 24/7 | — | medium | 安全除外対象。通年利用可でも冬は雪に埋もれるため、空の閉鎖月でよいか確認 |
| spot_035 | 胴腹滝 | 20 | rain_ok | no_walk | null | — | medium | 駐車場から水汲み場までの実歩行時間と冬季アクセスを確認 |
| spot_037 | 金峰神社 | 30 | rain_ok | short_walk | 24/7 | — | medium | 24時間は境内参拝。社務所利用を対象にする場合は別の営業時間が必要 |
| spot_039 | 中山河川公園 | 45 | rain_poor | short_walk | null | — | medium | 河川増水の根拠が一般推定。荒天時の閉鎖・安全除外要否を確認 |
| spot_040 | 奈曽川河川公園 | 60 | rain_poor | short_walk | null | — | medium | 河川増水の根拠が一般推定。キャンプ場の営業時間・季節は別途必要か確認 |
| spot_042 | 牛渡川 | 30 | rain_poor | short_walk | null | — | medium | 通年アクセスは推定。梅花藻の見頃だけを季節制約にするか確認 |
| spot_043 | 花立クリーンハイツ | 45 | indoor | no_walk | Mo-Tu,Th-Su 09:00-17:00 | — | medium | 時刻と水曜休館はMD根拠あり。高原施設の冬季営業有無は未確認 |
| spot_044 | 万助小屋 | 20 | rain_unsafe | hike | 24/7 | — | medium | 安全除外対象。通年利用可でも冬山到達を候補に残す運用でよいか確認 |

`high` は0件です。`stay_min` は全件で初期推定を含むため、指示書の「4フィールドとも一次情報」の基準を満たすレコードはありません。
