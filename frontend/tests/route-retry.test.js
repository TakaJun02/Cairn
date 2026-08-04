import test from 'node:test'
import assert from 'node:assert/strict'

import {
  fetchRouteWithRetry,
  partitionSettledRoutes,
  ROUTE_RETRY_ATTEMPTS,
} from '../src/lib/routeRetry.js'

function notFoundError() {
  return Object.assign(new Error('unexpected status 404'), { status: 404 })
}

// ---------------------------------------------------------------------------
// ADR-0020: 404 のときだけ短い間隔で再試行する(防御)。
// ---------------------------------------------------------------------------

test('成功時は 1 回だけ getRoute を呼ぶ', async () => {
  const calls = []
  const waits = []
  const result = await fetchRouteWithRetry('route-1', {
    getRoute: async (id) => {
      calls.push(id)
      return { route_id: id }
    },
    wait: async (ms) => { waits.push(ms) },
  })

  assert.deepEqual(result, { route_id: 'route-1' })
  assert.deepEqual(calls, ['route-1'])
  assert.deepEqual(waits, [])
})

test('404 は既定 3 回まで、300ms 間隔で再試行してから成功する', async () => {
  const calls = []
  const waits = []
  let attempt = 0
  const result = await fetchRouteWithRetry('route-1', {
    getRoute: async (id) => {
      attempt += 1
      calls.push(id)
      if (attempt < 3) throw notFoundError()
      return { route_id: id }
    },
    wait: async (ms) => { waits.push(ms) },
  })

  assert.deepEqual(result, { route_id: 'route-1' })
  assert.equal(calls.length, 3)
  assert.deepEqual(waits, [300, 300])
})

test('再試行し尽くしても 404 のままなら最後のエラーを投げる', async () => {
  const waits = []
  let attempt = 0

  await assert.rejects(
    () => fetchRouteWithRetry('route-1', {
      getRoute: async () => {
        attempt += 1
        throw notFoundError()
      },
      wait: async (ms) => { waits.push(ms) },
    }),
    (err) => err.status === 404,
  )

  assert.equal(attempt, ROUTE_RETRY_ATTEMPTS)
  assert.equal(waits.length, ROUTE_RETRY_ATTEMPTS - 1)
})

test('404 以外のエラーは再試行せずに即座に投げ直す', async () => {
  const calls = []
  const waits = []
  const serverError = Object.assign(new Error('boom'), { status: 500 })

  await assert.rejects(
    () => fetchRouteWithRetry('route-1', {
      getRoute: async (id) => {
        calls.push(id)
        throw serverError
      },
      wait: async (ms) => { waits.push(ms) },
    }),
    (err) => err === serverError,
  )

  assert.deepEqual(calls, ['route-1'])
  assert.deepEqual(waits, [])
})

test('attempts/delayMs を上書きできる', async () => {
  const waits = []
  let attempt = 0

  await assert.rejects(
    () => fetchRouteWithRetry('route-1', {
      getRoute: async () => {
        attempt += 1
        throw notFoundError()
      },
      attempts: 2,
      delayMs: 50,
      wait: async (ms) => { waits.push(ms) },
    }),
  )

  assert.equal(attempt, 2)
  assert.deepEqual(waits, [50])
})

// ---------------------------------------------------------------------------
// M-5(2026-08-04 レビュー是正): Promise.allSettled で取れたレッグだけ描く。
// ---------------------------------------------------------------------------

test('全レッグ成功なら routes に全件、failures は空', () => {
  const routeIds = ['r1', 'r2']
  const settled = [
    { status: 'fulfilled', value: { route_id: 'r1' } },
    { status: 'fulfilled', value: { route_id: 'r2' } },
  ]

  const { routes, failures } = partitionSettledRoutes(routeIds, settled)

  assert.deepEqual(routes, [{ route_id: 'r1' }, { route_id: 'r2' }])
  assert.deepEqual(failures, [])
})

test('1 本の失敗が成功した他レッグを消さない', () => {
  const routeIds = ['r1', 'r2', 'r3']
  const legError = new Error('unexpected status 404')
  const settled = [
    { status: 'fulfilled', value: { route_id: 'r1' } },
    { status: 'rejected', reason: legError },
    { status: 'fulfilled', value: { route_id: 'r3' } },
  ]

  const { routes, failures } = partitionSettledRoutes(routeIds, settled)

  assert.deepEqual(routes, [{ route_id: 'r1' }, { route_id: 'r3' }])
  assert.deepEqual(failures, [{ routeId: 'r2', error: legError }])
})

test('全滅なら routes は空、failures に全件が入る', () => {
  const routeIds = ['r1', 'r2']
  const errorA = new Error('a')
  const errorB = new Error('b')
  const settled = [
    { status: 'rejected', reason: errorA },
    { status: 'rejected', reason: errorB },
  ]

  const { routes, failures } = partitionSettledRoutes(routeIds, settled)

  assert.deepEqual(routes, [])
  assert.deepEqual(failures, [
    { routeId: 'r1', error: errorA },
    { routeId: 'r2', error: errorB },
  ])
})
