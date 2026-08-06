// src/lib/tiles.js
export const TILE_TEMPLATE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}';
export const TILE_SUBS = ['server'];

// Leaflet と同じ URL 形式を生成
export function tileUrl(z, x, y) {
  return `https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/${z}/${y}/${x}`;
}

/** manifest.tiles の bbox と zoom 範囲から全タイル URL を生成する。 */
export function tilesForBounds(tileManifest) {
  const bbox = tileManifest?.bbox
  const minZoom = Number(tileManifest?.min_zoom)
  const maxZoom = Number(tileManifest?.max_zoom)
  if (
    !Array.isArray(bbox)
    || bbox.length !== 4
    || !bbox.every((value) => Number.isFinite(Number(value)))
    || !Number.isInteger(minZoom)
    || !Number.isInteger(maxZoom)
    || minZoom < 0
    || maxZoom < minZoom
  ) return []

  const [minLon, minLat, maxLon, maxLat] = bbox.map(Number)
  const zooms = Array.from({ length: maxZoom - minZoom + 1 }, (_, index) => minZoom + index)
  return tilesForRoute(
    [[minLon, minLat], [maxLon, maxLat]],
    { zooms, marginDeg: 0, maxTiles: Number.POSITIVE_INFINITY },
  )
}

/**
 * ルート（polyline）を含むバウンディングボックスから必要なタイル一覧を返す。
 * @param {Array<Array<number>>} polyline - [[lon,lat], ...] or [[lat,lon], ...]
 * @param {object} opts
 * @param {number[]} [opts.zooms=[12,13,14,15]]
 * @param {number} [opts.marginDeg=0.05]
 * @param {number} [opts.maxTiles=800]
 * @returns {Array<{url:string,z:number,x:number,y:number}>}
 */
export function tilesForRoute(polyline, opts = {}) {
  const { zooms = [15], marginDeg = 0.05, maxTiles = 800 } = opts;
  if (!Array.isArray(polyline) || polyline.length === 0) return [];

  let minLat = 90;
  let maxLat = -90;
  let minLon = 180;
  let maxLon = -180;

  for (const coord of polyline) {
    const [lon, lat] = normalizeLonLat(coord);
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
  }

  minLat -= marginDeg;
  maxLat += marginDeg;
  minLon -= marginDeg;
  maxLon += marginDeg;

  const tiles = [];
  const seen = new Set();
  let subdomainIndex = 0;

  for (const z of zooms) {
    const scale = Math.pow(2, z);
    const xMin = clamp(Math.floor(lon2tile(minLon, z)), 0, scale - 1);
    const xMax = clamp(Math.floor(lon2tile(maxLon, z)), 0, scale - 1);
    const yMin = clamp(Math.floor(lat2tile(maxLat, z)), 0, scale - 1);
    const yMax = clamp(Math.floor(lat2tile(minLat, z)), 0, scale - 1);

    for (let x = xMin; x <= xMax; x++) {
      for (let y = yMin; y <= yMax; y++) {
        const key = `${z}/${x}/${y}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const url = tileUrl(z, x, y, subdomainIndex++);
        tiles.push({ url, z, x, y });
        if (tiles.length >= maxTiles) return tiles;
      }
    }
  }

  return tiles;
}

function normalizeLonLat(coord) {
  const [a, b] = coord;
  if (Math.abs(a) <= 90 && Math.abs(b) > 90) {
    return [b, a];
  }
  return [a, b];
}

function lon2tile(lon, z) {
  return (lon + 180) / 360 * Math.pow(2, z);
}

function lat2tile(lat, z) {
  const rad = (lat * Math.PI) / 180;
  return (1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2 * Math.pow(2, z);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}
