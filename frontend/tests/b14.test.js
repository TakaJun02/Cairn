import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia } from 'pinia'

import { decodeDownlinkHex } from '../src/lib/loraCodec.js'
import { applyDownlinkToSpotMap } from '../src/lib/realtimeDownlink.js'
import { useNavStore } from '../src/stores/nav.js'
import {
  advanceArrivalState,
  cacheOfflinePack,
  loadStoredOfflinePack,
  playbackVariants,
  verifyOfflinePack,
} from '../src/lib/offlinePack.js'
import { tilesForBounds } from '../src/lib/tiles.js'

const manifest = {
  pack_id: 'pack-test',
  pack_epoch: 7,
  playback_rules: {
    weather: { 1: 'weather_cloudy', 2: 'weather_rain' },
    congestion: { 1: 'congestion_mid', 2: 'congestion_high' },
    order: ['base', 'weather', 'congestion'],
  },
  spots: [
    {
      spot_id: 'spot-a',
      assets: {
        base: { file: 'audio/spot-a.base.ja.mp3', text: 'base' },
        weather_rain: { file: 'audio/spot-a.weather_rain.ja.mp3', text: 'rain' },
        congestion_high: { file: 'audio/spot-a.congestion_high.ja.mp3', text: 'crowd' },
      },
    },
    { spot_id: 'spot-b', assets: {} },
  ],
  along: [],
  missing: [],
}

test('LoRa downlink はバイト列との往復で pack_epoch と codes を保つ', () => {
  const bytes = Uint8Array.from([0x01, 0x07, 0x22, 0xff])
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  assert.deepEqual(decodeDownlinkHex(hex), { packEpoch: 7, codes: [0x22, 0xff] })
})

test('未知の LoRa protocol version は捨てる', () => {
  assert.equal(decodeDownlinkHex('020722'), null)
})

test('pack_epoch 不一致はコードを適用せず stale にする', () => {
  const lastBySpot = {}
  const result = applyDownlinkToSpotMap(
    lastBySpot,
    { packEpoch: 8, codes: [0x22] },
    manifest,
    123,
  )
  assert.deepEqual(result, { applied: false, stale: true, receivedAt: null })
  assert.deepEqual(lastBySpot, {})
})

test('0xF は不明として保持し、overlay 選択から除外する', () => {
  const lastBySpot = {}
  const result = applyDownlinkToSpotMap(
    lastBySpot,
    { packEpoch: 7, codes: [0xff] },
    manifest,
    456,
  )
  assert.equal(result.stale, false)
  assert.deepEqual(lastBySpot['spot-a'], { w: 0x0f, c: 0x0f, at: 456 })
  assert.deepEqual(playbackVariants(manifest, lastBySpot['spot-a']), ['base'])
})

test('雨かつ混雑では base → weather → congestion の順になる', () => {
  assert.deepEqual(
    playbackVariants(manifest, { w: 2, c: 2 }),
    ['base', 'weather_rain', 'congestion_high'],
  )
})

test('到達後は半径の2倍を出るまで再生を再武装しない', () => {
  assert.deepEqual(advanceArrivalState(true, 100, 150), { armed: false, triggered: true })
  assert.deepEqual(advanceArrivalState(false, 200, 150), { armed: false, triggered: false })
  assert.deepEqual(advanceArrivalState(false, 301, 150), { armed: true, triggered: false })
  assert.deepEqual(advanceArrivalState(true, 100, 150), { armed: false, triggered: true })
})

test('manifest.tiles の min_zoom から max_zoom までを使う', () => {
  const tiles = tilesForBounds({ bbox: [140, 39, 140, 39], min_zoom: 10, max_zoom: 16 })
  assert.deepEqual(tiles.map((tile) => tile.z), [10, 11, 12, 13, 14, 15, 16])
})

test('自己検証は missing 指定を除外し、消した音声1本を不足として返す', async () => {
  const manifestUrl = 'https://example.test/packs/pack-test/manifest.json'
  const cachedUrls = new Set([
    'https://example.test/packs/pack-test/route.geojson',
    'https://example.test/packs/pack-test/audio/spot-a.base.ja.mp3',
  ])
  const fakeCache = {
    async match(request) {
      return cachedUrls.has(request.url) ? new Response('{}') : undefined
    },
  }
  const cacheStorage = { async open() { return fakeCache } }
  const withIgnoredMissing = {
    ...manifest,
    missing: [{ spot_id: 'spot-a', variant: 'congestion_high', reason: 'tts_failed' }],
  }

  const result = await verifyOfflinePack(withIgnoredMissing, manifestUrl, { cacheStorage })
  assert.equal(result.routeCached, true)
  assert.equal(result.expected, 2)
  assert.equal(result.cached, 1)
  assert.deepEqual(result.missing.map((item) => item.variant), ['weather_rain'])
})

test('取り込み済み pack_id / pack_epoch は保存し、HTTP なしで復元できる', async () => {
  class MemoryStorage {
    values = new Map()
    getItem(key) { return this.values.get(key) ?? null }
    setItem(key, value) { this.values.set(key, String(value)) }
  }
  class MemoryCache {
    values = new Map()
    async match(request) { return this.values.get(request.url)?.clone() }
    async put(request, response) { this.values.set(request.url, response.clone()) }
  }
  const storage = new MemoryStorage()
  const cacheNames = new Map()
  const cacheStorage = {
    async open(name) {
      if (!cacheNames.has(name)) cacheNames.set(name, new MemoryCache())
      return cacheNames.get(name)
    },
  }
  const minimalManifest = {
    pack_id: 'pack-restorable',
    pack_epoch: 23,
    state: 'ready',
    spots: [],
    along: [],
    missing: [],
    tiles: null,
  }
  const route = { type: 'FeatureCollection', features: [] }
  let fetchCount = 0
  await cacheOfflinePack({
    manifest: minimalManifest,
    manifestUrl: 'https://example.test/packs/pack-restorable/manifest.json',
    route,
    storage,
    cacheStorage,
    fetchImpl: async () => {
      fetchCount += 1
      throw new Error('unexpected fetch')
    },
  })
  const restored = await loadStoredOfflinePack(null, { storage, cacheStorage })

  assert.equal(fetchCount, 0)
  assert.equal(restored.record.pack_id, 'pack-restorable')
  assert.equal(restored.record.pack_epoch, 23)
  assert.deepEqual(restored.route, route)
})

test('spots は未認証で送信せず、force と完了後の Promise 管理が競合しない', async () => {
  const values = new Map()
  globalThis.sessionStorage = {
    getItem(key) { return values.get(key) ?? null },
    setItem(key, value) { values.set(key, String(value)) },
    removeItem(key) { values.delete(key) },
  }
  globalThis.localStorage = globalThis.sessionStorage

  const pending = []
  const calls = []
  const originalFetch = globalThis.fetch
  const originalConsoleError = console.error
  const consoleErrors = []
  console.error = (...args) => { consoleErrors.push(args) }
  globalThis.fetch = (url, init) => {
    calls.push({ url: String(url), authorization: init.headers.get('Authorization') })
    let resolve
    const promise = new Promise((done) => { resolve = done })
    pending.push({ resolve })
    return promise
  }

  try {
    const nav = useNavStore(createPinia())

    assert.deepEqual(await nav.fetchSpots(), [])
    assert.equal(calls.length, 0)

    sessionStorage.setItem('user', JSON.stringify({ token: 'token-after-login' }))
    const oldRequest = nav.fetchSpots()
    const forcedRequest = nav.fetchSpots({ force: true })
    assert.equal(calls.length, 2)
    assert.deepEqual(calls.map((call) => call.authorization), [
      'Bearer token-after-login',
      'Bearer token-after-login',
    ])

    pending[0].resolve(new Response(JSON.stringify([{ spot_id: 'old' }]), {
      status: 200,
      headers: { 'Content-Type': 'application/json', ETag: 'old' },
    }))
    await oldRequest
    const sharedForcedRequest = nav.fetchSpots()
    assert.equal(calls.length, 2)

    pending[1].resolve(new Response(JSON.stringify([{ spot_id: 'new' }]), {
      status: 200,
      headers: { 'Content-Type': 'application/json', ETag: 'new' },
    }))
    await Promise.all([forcedRequest, sharedForcedRequest])
    assert.deepEqual(nav.spots, [{ spot_id: 'new' }])

    const afterSuccess = nav.fetchSpots()
    assert.equal(calls.length, 3)
    pending[2].resolve(new Response(null, { status: 304, headers: { ETag: 'new' } }))
    await afterSuccess
    assert.deepEqual(consoleErrors, [])
  } finally {
    globalThis.fetch = originalFetch
    console.error = originalConsoleError
    delete globalThis.sessionStorage
    delete globalThis.localStorage
  }
})
