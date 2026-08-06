// frontend/src/lib/geoutils.js

/**
 * @fileoverview 緯度経度に関する計算を行うユーティリティモジュール
 */

/**
 * 2つの緯度経度座標間の距離をメートル単位で計算します。
 * 計算にはHaversine（ハーバーサイン）公式を使用します。
 * @param {object} pos1 - 地点1の座標 { lat: number, lng: number }
 * @param {object} pos2 - 地点2の座標 { lat: number, lng: number }
 * @returns {number} 2点間の距離 (メートル)
 */
export function calculateDistance(pos1, pos2) {
  if (!pos1 || !pos2) return Infinity;

  const R = 6371e3; // 地球の半径 (メートル)
  const phi1 = pos1.lat * Math.PI / 180; // φ, λ in radians
  const phi2 = pos2.lat * Math.PI / 180;
  const deltaPhi = (pos2.lat - pos1.lat) * Math.PI / 180;
  const deltaLambda = (pos2.lng - pos1.lng) * Math.PI / 180;

  const a = Math.sin(deltaPhi / 2) * Math.sin(deltaPhi / 2) +
            Math.cos(phi1) * Math.cos(phi2) *
            Math.sin(deltaLambda / 2) * Math.sin(deltaLambda / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));

  const d = R * c; // in metres
  return d;
}

/**
 * ユーザーの現在地が、指定されたルート上のどの移動モード（'car' or 'foot'）
 * の区間に最も近いかを判定します。
 * @param {object} currentPos - ユーザーの現在地座標 { lat: number, lng: number }
 * @param {Array} routeSegments - GeoJSON形式のルートセグメントの配列
 * 各セグメントは { mode: 'car'|'foot', geometry: { coordinates: [[lng, lat], ...] } }
 * @returns {string} 最も近い区間の移動モード ('car' または 'foot')。ルート情報がない場合は'car'をデフォルトとします。
 */

// どの形でも [{mode, geometry}, ...] に正規化
function normalizeRouteSegments(routeLike) {
  // すでに配列（バックエンド例の形）
  if (Array.isArray(routeLike)) return routeLike;

  // plan全体や { segments: [...] } を渡されてもOKにする
  if (Array.isArray(routeLike?.segments)) return routeLike.segments;

  // GeoJSON FeatureCollection / Feature でもOKにする
  if (Array.isArray(routeLike?.features)) {
    return routeLike.features.map(f => ({
      mode: (f?.properties?.mode === 'car') ? 'car' : 'foot',
      geometry: f?.geometry
    }));
  }
  if (routeLike?.type === 'Feature' && routeLike?.geometry) {
    return [{
      mode: (routeLike?.properties?.mode === 'car') ? 'car' : 'foot',
      geometry: routeLike.geometry
    }];
  }
  return []; // 想定外は空
}

// LineString / MultiLineString 双方に対応して座標を反復
function *iterateCoords(geometry) {
  if (!geometry) return;
  const coords = geometry.coordinates;
  if (geometry.type === 'LineString' && Array.isArray(coords)) {
    for (const c of coords) yield c;
  } else if (geometry.type === 'MultiLineString' && Array.isArray(coords)) {
    for (const line of coords) for (const c of line) yield c;
  }
}

export function getCurrentTravelMode(currentPos, routeSegments) {
  // ルートセグメントのデータがない、または空配列の場合はデフォルトの'car'を返す
  if (!routeSegments || routeSegments.length === 0) {
    return 'car';
  }

  // normalizeRouteSegments を一度だけ呼ぶ
  const segments = Array.isArray(routeSegments)
    ? routeSegments
    : normalizeRouteSegments(routeSegments);

  console.debug('[GEO] segments.len', segments.length);

  let closestMode = 'car'; // デフォルトの移動モード
  let minDistance = Infinity;

  // 全てのルートセグメント（'car', 'foot'）を反復処理
  for (const seg of segments) {
    if (!seg?.geometry?.coordinates) continue;
    for (const coord of iterateCoords(seg.geometry)) {
      const p = { lat: coord[1], lng: coord[0] }; // [lon,lat] → {lat,lng}
      const d = calculateDistance(currentPos, p);
      if (d < minDistance) {
        minDistance = d;
        // 'walk' 等が来ても 'foot' に寄せておく
        closestMode = (seg.mode === 'car') ? 'car' : 'foot';
      }
    }
  }

  console.debug('[GEO] closestMode, minDistance(m)', closestMode, minDistance?.toFixed?.(1));
  return closestMode;
}