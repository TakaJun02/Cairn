# OSRM データの準備と起動

- 状態: **決定稿 (2026-08-01)**
- 前提: [ADR-0014](../adr/0014-osrm-area-extract.md)(OSRM データを鳥海山エリアに限定する)/ [geo.md](../30_design/geo.md)(利用側)
- 対象: NFR-2(clone + 少数コマンドで再現起動できる)

---

## 0. なぜこの文書があるか

**現状、クリーン clone からシステムを起動できない。**

| 事実(2026-08-01 実測) | |
| --- | --- |
| `backend/worker/data/map/car` | **15 GB**(`japan-latest.osrm` ほか 27 ファイル) |
| `backend/worker/data/map/foot` | **17 GB** |
| **合計** | **32 GB** |
| Git | `.gitignore` 済み。**作り方はリポジトリのどこにも書かれていない**([22 §8-6](../22_current_issues.md)) |
| 所有者 | `japan-latest.osrm.fileIndex`(1.0 GB)が **root 所有**で混在 |

対象エリアは鳥海山周辺の 43 地点に固定されている([00_project.md](../00_project.md))のに、**日本全国の道路網を載せている。**これを切り出し、生成手順をスクリプトにする。

---

## 1. 決定 — 鳥海山エリアの bbox に切り出す

### 1.1 範囲

| | 値 |
| --- | --- |
| 43 地点の外接矩形(実測) | lon **139.850 〜 140.285** / lat **39.000 〜 39.443** |
| access_points 33 件 | lon 139.943 〜 140.107 / lat 39.014 〜 39.206(内側) |
| **採用する bbox** | **lon 139.55 〜 140.60 / lat 38.80 〜 39.65** |
| 余裕 | 各辺に約 **20 km** |

**余裕を取るのは、迂回路が bbox の外を通ることがあるからである。**切り口で道が途切れると、OSRM は `NoRoute` を返す。20 km は日本海側の海岸線と国道 7 号・鳥海ブルーラインを丸ごと含む幅である。

### 1.2 元データ

**Geofabrik の日本サブリージョン `tohoku`**(秋田・青森・岩手・宮城・山形・福島)を使う。43 地点はすべて秋田県・山形県にあるので `tohoku` で足りる。全国版(`japan-latest.osm.pbf`)を落とす必要はない。

```
https://download.geofabrik.de/asia/japan/tohoku-latest.osm.pbf
```

---

## 2. 手順

### 2.1 前提ツール

```bash
sudo apt install -y osmium-tool     # 切り出し（Ubuntu 24.04 の universe にある）
# docker は osrm-backend の実行に使う（すでに導入済み）
```

> Docker イメージ版の osmium を使ってもよいが、**イメージ名は環境依存**なので既定は apt にする。

### 2.2 スクリプト `scripts/build_osrm.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${OSRM_WORK:-$REPO/backend/data/map/_work}"
BBOX="139.55,38.80,140.60,39.65"          # lon_min,lat_min,lon_max,lat_max
SRC_URL="https://download.geofabrik.de/asia/japan/tohoku-latest.osm.pbf"

mkdir -p "$WORK"

# 1) 元データを取得（-z で更新がなければ落とさない）
curl -fL -z "$WORK/tohoku-latest.osm.pbf" -o "$WORK/tohoku-latest.osm.pbf" "$SRC_URL"

# 2) bbox で切り出す
#    --strategy=complete_ways が重要。境界をまたぐ way を丸ごと残さないと道が途切れる
osmium extract -b "$BBOX" --strategy=complete_ways --overwrite \
  -o "$WORK/chokai.osm.pbf" "$WORK/tohoku-latest.osm.pbf"

# 3) プロファイルごとに前処理（MLD: extract → partition → customize）
for P in car foot; do
  DEST="$REPO/backend/data/map/$P"
  rm -rf "$DEST"; mkdir -p "$DEST"
  cp "$WORK/chokai.osm.pbf" "$DEST/chokai.osm.pbf"
  docker run --rm -v "$DEST:/data" osrm/osrm-backend \
    osrm-extract -p "/opt/$P.lua" /data/chokai.osm.pbf
  docker run --rm -v "$DEST:/data" osrm/osrm-backend osrm-partition /data/chokai.osrm
  docker run --rm -v "$DEST:/data" osrm/osrm-backend osrm-customize /data/chokai.osrm
  rm -f "$DEST/chokai.osm.pbf"
done

# 4) ビルド識別子を書く（geo.md §3.2 の params.osrm_build に入る）
{
  date -u +%Y%m%dT%H%M%SZ
  echo "source=tohoku bbox=$BBOX"
  sha256sum "$WORK/chokai.osm.pbf" | cut -c1-16
} | paste -sd' ' > "$REPO/backend/data/map/BUILD"

echo "built: $(cat "$REPO/backend/data/map/BUILD")"
```

### 2.3 `BUILD` ファイルが要点である

`backend/data/map/BUILD` の中身は `app.routes.params.osrm_build` に入る([geo.md §3.2](../30_design/geo.md))。

**これがあると、地図データを作り直したときに経路キャッシュが自動で無効になる。**無いと、**もう存在しない道で引かれた線がパックに焼かれ続ける**。

### 2.4 MLD と CH を混ぜない

- 本構成は **MLD**(`osrm-partition` + `osrm-customize`、起動時 `--algorithm mld`)
- CH(`osrm-contract`)の成果物と混ぜると `osrm-routed` が起動時に落ちる
- 現行 compose もすでに MLD で起動している。**変えない**

---

## 3. compose

```yaml
osrm-car:
  image: osrm/osrm-backend
  command: ["osrm-routed", "--algorithm", "mld", "--max-table-size", "200", "/data/chokai.osrm"]
  volumes: ["./backend/data/map/car:/data:ro"]
  ports: ["5001:5000"]
  healthcheck:
    test: ["CMD-SHELL",
           "curl -sf 'http://localhost:5000/route/v1/car/140.024,39.159;140.035,39.034' | grep -q '\"code\":\"Ok\"'"]
    interval: 30s
    timeout: 5s
    retries: 10

osrm-foot:
  image: osrm/osrm-backend
  command: ["osrm-routed", "--algorithm", "mld", "--max-table-size", "200", "/data/chokai.osrm"]
  volumes: ["./backend/data/map/foot:/data:ro"]
  ports: ["5002:5000"]
  healthcheck: (同上。profile を foot にする)
```

| 変えたこと | 理由 |
| --- | --- |
| マウント元を `backend/worker/data/map` → **`backend/data/map`** | Phase 1 のデータ移設に合わせる([90_backlog.md §C-0](../90_backlog.md)) |
| データ名を `japan-latest` → **`chokai`** | 中身を名前が表す |
| **`--max-table-size 200` を明示** | 43 地点の `/table` は既定 100 でも通るが、**意図を compose に書く**([geo.md §4.2](../30_design/geo.md)) |
| healthcheck を `/` → **実際のルート照会** | 「嘘をつかない health」([22 §6-11](../22_current_issues.md))。座標は あがりこ大王 → 鳥海高原家族旅行村 |

### 3.1 `.gitignore` の修正が要る

```diff
-backend/worker/data/map/car/*
-backend/worker/data/map/foot/*
+backend/data/map/
+!backend/data/map/BUILD
```

- **`BUILD` は追跡する。**「今のリポジトリが想定している地図データはどれか」がコミット履歴に残る
- **併せて `.env*` の行を直す。**現状の `.env*` は **`.env.example` も無視してしまう**ので、NFR-6 が要求する「値なしの `.env.example` を追跡する」が成立しない

```diff
 .env*
+!.env.example
```

---

## 4. 検証(受け入れ条件)

```bash
docker compose up -d osrm-car osrm-foot db
python -m app.cli check-osrm             # 疎通・profile・BUILD 識別子
python -m app.cli build-geo              # static.spot_approach
python -m app.cli build-travel-times     # static.travel_times
```

| 検査 | 合格ライン | **実測(2026-08-01)** |
| --- | --- | --- |
| `check-osrm` | car / foot ともに固定 2 点の `/route` が `code: Ok` | **✅ car 56.6 km / 52 分、foot 1,248 m / 15 分** |
| `/nearest` | スナップ距離が返る(`build-geo` が使う) | **✅ あがりこ大王は 934.6 m → 直接到達不可**と正しく判定される |
| `/table` | **43 地点が 1 リクエストで返る** | **✅ car 35 ms / foot 38 ms、欠損 0 / 1,806** |
| `build-geo` | **`spot_approach` が 43 件。**「直接到達 N / 駐車場経由 M、最長徒歩 X 分」を出力する | 未実行(`app.cli` 待ち) |
| `build-travel-times` | **`car` が 1,806 行**(43 × 43 − 対角 43)で**欠損ゼロ**。`foot` は近接ペアぶん(**閾値 30 分 / 2.5 km で 46 行**) | 未実行(同上) |
| ディスク | `backend/data/map` の合計が **1 GB 未満**(全国版は 32 GB) | **✅ car 105 MB + foot 159 MB = 264 MB**(+ `_work` の元データ 315 MB) |
| 所要 | `build_osrm.sh` 全体が **10 分程度**(全国版は数時間) | **✅ 約 9 分**(うち取得 2 分) |

**切り出しの効果(実測)**: 元データ 292 MB → bbox 切り出し **25 MB** → car 105 MB / foot 159 MB。**全国版 32 GB に対して 0.8%。**

**`build-geo` が失敗したら、それはデータの問題である**([geo.md §2.3](../30_design/geo.md))。フォールバック座標は持たないので、`access_points.geojson` に駐車場を足すか、bbox を広げる。

---

## 5. データを更新するとき

```bash
./scripts/build_osrm.sh                        # BUILD が変わる
docker compose restart osrm-car osrm-foot
python -m app.cli build-geo --force
python -m app.cli build-travel-times --force
```

| 対象 | 更新のされ方 |
| --- | --- |
| `app.routes` | **自動。**`params.osrm_build` が変わるので、次から新しい経路が引かれる(古い行は残るが参照されない) |
| `static.travel_times` | **明示的な `--force` が要る。**シードとソルバーの前提が黙って変わるのを避ける([geo.md §4.2](../30_design/geo.md)) |
| `static.spot_approach` | 同上 |
| 既存のパック | **作り直さない。**端末が持っているものと整合を保つ([packs_pipeline.md §9](../30_design/packs_pipeline.md)) |

---

## 6. 現行からの移行手順

```bash
# 1) 新しいデータを作る（旧データはまだ消さない）        ✅ 2026-08-01 実施済み
./scripts/build_osrm.sh

# 2) compose のマウント先を切り替えて起動・検証（§3・§4）  ✅ 2026-08-01 実施済み

# 3) 検証が通ったら旧データを消す（31 GB）               ← まだ実施していない
rm -rf backend/worker/data/map
```

**旧データを先に消さない。**作り直せるとはいえ、全国版の再生成には数時間かかる。

> **状態(2026-08-01)**: 1 と 2 は完了。**3 は未実施**で、`backend/worker/data/map` に **31 GB** が残っている。新データで一通り動くことを確認してから消すこと(**消しても `build_osrm.sh` で作り直せるのは切り出し版だけ**で、全国版は作り直しに数時間かかる)。

---

## 7. トラブルシュート

| 症状 | 原因と対処 |
| --- | --- |
| `NoRoute` が返る | bbox の切り口で道が途切れている。`--strategy=complete_ways` を使っているか確認し、それでも出るなら bbox を広げて再ビルド |
| `TooBig`(`/table`) | 地点数が `--max-table-size` を超えた。compose の値を上げる |
| foot で山道が出ない | `foot.lua` は `highway=path` を含む。**切り出しの strategy が `simple` だと trail が途切れる** |
| `osrm-routed` が起動直後に落ちる | MLD と CH の成果物が混在している(§2.4)。`backend/data/map/$P` を消して作り直す |
| `osrm-extract` が OOM で落ちる | bbox が広すぎる。**この bbox なら数 GB で足りる**ので、全国版の pbf を渡していないか確認する |
| 権限エラー | docker が root で書いたファイルが残っている。`sudo chown -R "$USER" backend/data/map` |

---

## 8. 決定の記録(2026-08-01)

| # | 決定 | 根拠 |
| --- | --- | --- |
| 1 | **bbox `139.55,38.80,140.60,39.65` に切り出す** | 43 地点の外接矩形 + 20 km。32 GB → 1 GB 未満 |
| 2 | **元データは Geofabrik の `tohoku`** | 対象は秋田・山形のみ。全国版は要らない |
| 3 | **`osmium extract --strategy=complete_ways`** | `simple` だと境界をまたぐ way が切れて `NoRoute` になる |
| 4 | **`BUILD` 識別子を作り、Git で追跡する** | 経路キャッシュの無効化キー。古い道で引いた線が残らない |
| 5 | **MLD のまま。CH に変えない** | 現行と同じ。`/table` も使える |
| 6 | **`--max-table-size 200` を compose に明示** | 43 地点は既定 100 でも通るが、意図を残す |
| 7 | **healthcheck は実際の `/route` 照会** | 静的 ok を返す health を作らない |
| 8 | **`travel_times` の再計算は明示操作** | 前提が黙って変わらない |
| 9 | **`.gitignore` の `.env*` に `!.env.example` を足す** | 現状 `.env.example` も無視され、NFR-6 の受け皿が作れない |
