// src/lib/realtime.js
// Realtime (LoRa/MQTTブリッジ) API ラッパ（api.js の関数を薄くラップ）
//
// api.js の fetchRealtimeBySpotId() は { status, body } を返すため
// ここでは呼び出し側が扱いやすいよう { status, body } をそのまま流用する。

import { fetchRealtimeBySpotId } from './api' // ← apiFetch ではなく、公開関数を使用

/**
 * @typedef {Object} RTDoc
 * @property {string} s   // spot_id
 * @property {number} w   // now: 0=sun,1=cloud,2=rain
 * @property {number} c   // congestion 0..2
 */

/**
 * 指定スポットのRT情報を取得（{status, body} 形式で返却）
 * - オンライン: HTTP（従来どおり）
 * - オフライン時はエラーとして返却
 * @param {string} spotId
 * @param {{ etag?: number }=} opts
 * @returns {Promise<{status:number, body: RTDoc|null, error?:any}>}
 */
export async function fetchSpotRT(spotId, opts = {}) {
  try {
    const online = typeof navigator !== 'undefined' ? !!navigator.onLine : true

    // オフライン時はLoRaフォールバックを削除し、HTTP通信のみを行う
    if (!online) {
      // オフラインであることを示すためにエラーを返すか、あるいは status: 0 を返す
      const err = new Error('Offline');
      return { status: 0, body: null, error: err };
    }

    // ★ 従来どおり HTTP を使用
    const { status, body } = await fetchRealtimeBySpotId(spotId, opts)
    if (status === 204 || status === 304) {
      return { status, body: null }
    }
    if (status === 200) {
      return { status, body: /** @type {RTDoc} */ (body) }
    }
    return { status, body }
  } catch (err) {
    return { status: 0, body: null, error: err }
  }
}
