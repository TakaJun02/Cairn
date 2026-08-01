#!/usr/bin/env bash
# =============================================================================
#  OSRM の地図データを鳥海山エリアに切り出してビルドする
#
#  根拠: Docs/adr/0014-osrm-area-extract.md
#  手順の説明・検証・トラブルシュート: Docs/50_operations/osrm.md
#
#  使い方:
#      ./scripts/build_osrm.sh                # 全部（取得 → 切り出し → car/foot ビルド）
#      SKIP_DOWNLOAD=1 ./scripts/build_osrm.sh
#      PROFILES="car" ./scripts/build_osrm.sh # 片方だけ作り直す
#
#  出力: backend/data/map/{car,foot}/chokai.osrm*  と  backend/data/map/BUILD
# =============================================================================
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${OSRM_WORK:-$REPO/backend/data/map/_work}"
DEST_ROOT="$REPO/backend/data/map"

# 43 地点の外接矩形（lon 139.850–140.285 / lat 39.000–39.443）に各辺およそ 20 km の余裕
BBOX="${BBOX:-139.55,38.80,140.60,39.65}"
SRC_URL="${SRC_URL:-https://download.geofabrik.de/asia/japan/tohoku-latest.osm.pbf}"
SRC_PBF="$WORK/$(basename "$SRC_URL")"
CLIP_PBF="$WORK/chokai.osm.pbf"
PROFILES="${PROFILES:-car foot}"
OSRM_IMAGE="${OSRM_IMAGE:-osrm/osrm-backend}"
OSMIUM_IMAGE="${OSMIUM_IMAGE:-guidance-osmium:local}"

log() { printf '\n\033[1m[build_osrm]\033[0m %s\n' "$*"; }

mkdir -p "$WORK"

# --- 1) 元データ（東北。全国版は使わない）-----------------------------------
if [[ "${SKIP_DOWNLOAD:-0}" != "1" ]]; then
  log "元データを取得: $SRC_URL"
  # -z: 手元のファイルより新しいときだけ落とす
  curl -fL --retry 3 -z "$SRC_PBF" -o "$SRC_PBF" "$SRC_URL"
fi
[[ -s "$SRC_PBF" ]] || { echo "元データがない: $SRC_PBF" >&2; exit 1; }
log "元データ: $(du -h "$SRC_PBF" | cut -f1)"

# --- 2) bbox で切り出す ------------------------------------------------------
#  --strategy=complete_ways が重要。境界をまたぐ way を丸ごと残さないと道が途切れ、
#  OSRM が NoRoute を返すようになる（Docs/50_operations/osrm.md §7）
run_osmium() {
  if command -v osmium >/dev/null 2>&1; then
    osmium "$@"
  else
    # ローカルに osmium-tool が無ければ、その場で小さなイメージを作って使う
    if ! docker image inspect "$OSMIUM_IMAGE" >/dev/null 2>&1; then
      log "osmium-tool のイメージを作る（初回のみ）"
      docker build -q -t "$OSMIUM_IMAGE" - >/dev/null <<'DOCKERFILE'
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends osmium-tool \
 && rm -rf /var/lib/apt/lists/*
DOCKERFILE
    fi
    docker run --rm -u "$(id -u):$(id -g)" -v "$WORK:/w" -w /w "$OSMIUM_IMAGE" osmium "$@"
  fi
}

log "bbox で切り出す: $BBOX"
if command -v osmium >/dev/null 2>&1; then
  run_osmium extract -b "$BBOX" --strategy=complete_ways --overwrite -o "$CLIP_PBF" "$SRC_PBF"
else
  run_osmium extract -b "$BBOX" --strategy=complete_ways --overwrite \
    -o "/w/$(basename "$CLIP_PBF")" "/w/$(basename "$SRC_PBF")"
fi
log "切り出し後: $(du -h "$CLIP_PBF" | cut -f1)"

# --- 3) プロファイルごとに前処理（MLD: extract → partition → customize）------
#  CH（osrm-contract）と混ぜない。混在すると osrm-routed が起動直後に落ちる
osrm() { docker run --rm -u "$(id -u):$(id -g)" -v "$1:/data" "$OSRM_IMAGE" "${@:2}"; }

for P in $PROFILES; do
  DEST="$DEST_ROOT/$P"
  log "プロファイル $P をビルド"
  rm -rf "$DEST"; mkdir -p "$DEST"
  cp "$CLIP_PBF" "$DEST/chokai.osm.pbf"
  osrm "$DEST" osrm-extract   -p "/opt/$P.lua" /data/chokai.osm.pbf
  osrm "$DEST" osrm-partition /data/chokai.osrm
  osrm "$DEST" osrm-customize /data/chokai.osrm
  rm -f "$DEST/chokai.osm.pbf"
  log "$P: $(du -sh "$DEST" | cut -f1)"
done

# --- 4) ビルド識別子 ---------------------------------------------------------
#  app.routes.params.osrm_build に入り、地図を作り直すと経路キャッシュが
#  自動で無効になる（Docs/30_design/geo.md §3.2）
{
  printf '%s source=%s bbox=%s sha=%s\n' \
    "$(date -u +%Y%m%dT%H%M%SZ)" \
    "$(basename "$SRC_URL")" \
    "$BBOX" \
    "$(sha256sum "$CLIP_PBF" | cut -c1-16)"
} > "$DEST_ROOT/BUILD"

log "完了: $(cat "$DEST_ROOT/BUILD")"
log "合計: $(du -sh "$DEST_ROOT" --exclude=_work | cut -f1)"
echo
echo "次にやること:"
echo "  docker compose up -d osrm-car osrm-foot"
echo "  python -m app.cli check-osrm && python -m app.cli build-geo && python -m app.cli build-travel-times"
