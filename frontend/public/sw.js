// public/sw.js
// v2 — packs(音声)のRange対応 + 地図タイルのオフライン対応
self.addEventListener('install', (event) => {
  event.waitUntil(precacheStaticAssets());
  self.skipWaiting();
});
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });

const PACKS_PREFIX = 'packs-';
const TILES_CACHE  = 'tiles-v1';
const RUNTIME      = 'runtime';
const STATIC_ASSETS = ['/sound.mp3'];

// lib/tiles.js が使う ArcGIS World Street Map
const TILE_HOSTS = [
  'server.arcgisonline.com',
];
const TILE_PATH_PREFIX = '/ArcGIS/rest/services/World_Street_Map/MapServer/tile/';

const EMPTY_TILE_PNG = (() => {
  // 透明1x1 PNG（tileサイズとは無関係・ブラウザ側で拡大される）
  const base64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO8m2k4AAAAASUVORK5CYII=';
  return new Response(Uint8Array.from(atob(base64), c => c.charCodeAt(0)), {
    status: 200,
    headers: { 'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=31536000' }
  });
})();

async function precacheStaticAssets() {
  try {
    const cache = await caches.open(RUNTIME);
    await Promise.all(STATIC_ASSETS.map(async (asset) => {
      try {
        const request = new Request(asset, { cache: 'no-cache' });
        const response = await fetch(request);
        if (response.ok) {
          await cache.put(asset, response.clone());
        }
      } catch (err) {
        console.warn('[sw] Failed to precache asset:', asset, err);
      }
    }));
  } catch (err) {
    console.warn('[sw] precacheStaticAssets failed:', err);
  }
}

async function latestPacksCacheName() {
  const names = await caches.keys();
  const packs = names.filter(n => n.startsWith(PACKS_PREFIX)).sort();
  return packs.slice(-1)[0] || RUNTIME;
}

async function resetTilesCache() {
  try {
    await caches.delete(TILES_CACHE);
  } catch (err) {
    console.warn('[sw] failed to reset tiles cache:', err);
  }
}

function isPacksRequest(request) {
  try {
    return new URL(request.url).pathname.startsWith('/packs/');
  } catch {
    return false;
  }
}

async function resetPacksCache() {
  const cacheNames = await caches.keys();
  const packCacheNames = cacheNames.filter((name) => name.startsWith(PACKS_PREFIX));

  await Promise.all(packCacheNames.map((name) => caches.delete(name).catch(() => {})));

  const nonPackNames = cacheNames.filter((name) => !packCacheNames.includes(name));
  await Promise.all(
    nonPackNames.map(async (name) => {
      try {
        const cache = await caches.open(name);
        const keys = await cache.keys();
        const deletions = keys.filter((request) => isPacksRequest(request));
        await Promise.all(deletions.map((request) => cache.delete(request)));
      } catch (err) {
        console.warn('[sw] failed to purge packs entries from cache', name, err);
      }
    })
  );
}

async function resetCaches({ tiles = false, packs = false } = {}) {
  const tasks = [];
  if (tiles) tasks.push(resetTilesCache());
  if (packs) tasks.push(resetPacksCache());
  if (tasks.length > 0) {
    await Promise.all(tasks);
  }
}

self.addEventListener('message', (event) => {
  const data = event.data || {};
  switch (data.type) {
    case 'PRECACHE_TILES': {
      const tiles = Array.isArray(data.tiles) ? data.tiles : [];
      const clientId = event.source && 'id' in event.source ? event.source.id : null;
      event.waitUntil(precacheTiles(tiles, { clientId }));
      break;
    }
    case 'RESET_TILES_CACHE': {
      event.waitUntil(resetCaches({ tiles: true, packs: true }));
      break;
    }
    case 'RESET_PACKS_CACHE': {
      event.waitUntil(resetCaches({ packs: true }));
      break;
    }
    default:
      break;
  }
});

async function handleStaticAsset(request) {
  const cache = await caches.open(RUNTIME);
  const cached = await cache.match(request, { ignoreSearch: true });
  if (cached) {
    fetch(request).then(res => {
      if (res && res.ok) {
        cache.put(request, res.clone()).catch(() => {});
      }
    }).catch(() => {});
    return cached;
  }

  try {
    const response = await fetch(request);
    if (response && response.ok) {
      await cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    return cached || new Response('Offline and asset not cached', { status: 504 });
  }
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // 静的アセット（sound.mp3など）— オフライン対応
  if (req.method === 'GET'
      && url.origin === self.location.origin
      && STATIC_ASSETS.includes(url.pathname)) {
    event.respondWith(handleStaticAsset(req));
    return;
  }

  // 音声（/packs/…）— Range対応
  if (url.origin === self.location.origin && url.pathname.startsWith('/packs/')) {
    event.respondWith(handlePacks(req));
    return;
  }

  // ArcGISタイル（クロスオリジン）— キャッシュ or 透過タイルでフォールバック
  if (req.method === 'GET'
      && req.destination === 'image'
      && TILE_HOSTS.includes(url.host)
      && url.pathname.startsWith(TILE_PATH_PREFIX)) {
    event.respondWith(handleTiles(req));
    return;
  }

  // それ以外は素通し
});

async function handlePacks(request) {
  const cache = await caches.open(await latestPacksCacheName());
  let res = await cache.match(request, { ignoreSearch: true });

  if (!res) {
    try {
      res = await fetch(request);
      try { await cache.put(request, res.clone()); } catch {}
    } catch {
      return new Response('Offline and not cached', { status: 504 });
    }
  }

  // Range ヘッダが来たら部分応答を自前で生成
  const range = request.headers.get('Range');
  if (range) {
    const full = await res.clone().blob();
    const size = full.size;
    const m = /bytes=(\d+)-(\d+)?/.exec(range);
    if (m) {
      const start = Number(m[1]);
      const end = m[2] ? Number(m[2]) : size - 1;
      const sliced = full.slice(start, end + 1);
      return new Response(sliced, {
        status: 206,
        headers: {
          'Content-Type': res.headers.get('Content-Type') || 'audio/mpeg',
          'Content-Range': `bytes ${start}-${end}/${size}`,
          'Accept-Ranges': 'bytes',
          'Content-Length': String(end - start + 1),
          'Cache-Control': 'public, max-age=31536000',
        },
      });
    }
  }
  return res;
}

async function handleTiles(request) {
  const cache = await caches.open(TILES_CACHE);
  const cached = await cache.match(request, { ignoreSearch: true });
  if (cached) {
    // SWR（裏で更新）
    fetch(request).then(res => { try { cache.put(request, res.clone()); } catch {} }).catch(()=>{});
    return cached;
  }
  try {
    const res = await fetch(request, { mode: 'no-cors' }); // opaque 可
    try { await cache.put(request, res.clone()); } catch {}
    trimTilesCache(cache).catch(()=>{});
    return res;
  } catch {
    return EMPTY_TILE_PNG.clone();
  }
}

const MAX_TILES = 1500; // 約〜50MB目安（環境により増減させてOK）
async function trimTilesCache(cache) {
  const keys = await cache.keys();
  if (keys.length > MAX_TILES) {
    const del = keys.length - MAX_TILES;
    for (let i = 0; i < del; i++) { await cache.delete(keys[i]); }
  }
}

async function precacheTiles(tiles, { clientId } = {}) {
  if (!tiles.length) return;

  const cache = await caches.open(TILES_CACHE);
  let added = 0;
  let skipped = 0;
  let failed = 0;

  for (const entry of tiles) {
    const url = typeof entry === 'string' ? entry : entry?.url;
    if (!url || typeof url !== 'string') {
      failed++;
      continue;
    }

    const request = new Request(url, { mode: 'no-cors' });
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) {
      skipped++;
      continue;
    }

    try {
      const response = await fetch(request);
      if (response && (response.ok || response.type === 'opaque')) {
        await cache.put(request, response.clone());
        added++;
      } else {
        failed++;
      }
    } catch (err) {
      failed++;
    }
  }

  trimTilesCache(cache).catch(() => {});

  if (clientId) {
    try {
      const client = await self.clients.get(clientId);
      client?.postMessage({
        type: 'PRECACHE_TILES_RESULT',
        summary: { added, skipped, failed, requested: tiles.length }
      });
    } catch {
      // ignore
    }
  }
}
