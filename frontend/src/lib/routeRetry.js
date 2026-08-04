// src/lib/routeRetry.js
//
// ADR-0020(routes 早期 commit): `state:itinerary`(final)送出の時点で
// route は commit 済みであることがサーバー側の契約になった。フロントの
// 再試行はあくまで防御であり、主修正はバックエンド側にある
// (Docs/30_design/frontend_nav.md §3 表 #6)。
//
// 404 のときだけ短い間隔で再試行する。404 以外(認証切れ・5xx 等)は
// 再試行しても直らないため、即座に投げ直す。

export const ROUTE_RETRY_ATTEMPTS = 3
export const ROUTE_RETRY_DELAY_MS = 300

const defaultWait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * `getRoute(routeId)` を呼び、404 のときだけ最大 `attempts` 回
 * (既定 3 回)、`delayMs` 間隔(既定 300ms)で再試行する。
 * 再試行し尽くしても失敗したら最後のエラーをそのまま投げる
 * (呼び出し側は現状どおりエラーログ + `error.value` 設定を行う)。
 */
export async function fetchRouteWithRetry(
  routeId,
  {
    getRoute,
    attempts = ROUTE_RETRY_ATTEMPTS,
    delayMs = ROUTE_RETRY_DELAY_MS,
    wait = defaultWait,
  } = {},
) {
  let lastError = null
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await getRoute(routeId)
    } catch (err) {
      lastError = err
      if (err?.status !== 404 || attempt === attempts) throw err
      await wait(delayMs)
    }
  }
  // for 文は必ず return か throw で抜けるため到達しないが、念のため。
  throw lastError
}

/**
 * 2026-08-04(レビュー是正・M-5): レッグ復元は `Promise.all` ではなく
 * `Promise.allSettled` で行い、1 本の失敗で成功した他レッグまで消さない
 * (frontend_nav.md §3 表#6)。`settled`(`Promise.allSettled` の結果)を
 * `routeIds` と同じ順序で受け取り、取れたレッグ(`routes`)と欠けた
 * レッグ(`failures`)に分ける。欠けたレッグは経路縮退(`route_degraded`)と
 * 同じ扱いにする(呼び出し側がエラーログ + 表示用メッセージを組み立てる)。
 */
export function partitionSettledRoutes(routeIds, settled) {
  const routes = []
  const failures = []
  settled.forEach((result, index) => {
    if (result.status === 'fulfilled') {
      routes.push(result.value)
    } else {
      failures.push({ routeId: routeIds[index], error: result.reason })
    }
  })
  return { routes, failures }
}
