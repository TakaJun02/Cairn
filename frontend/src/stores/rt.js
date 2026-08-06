// src/stores/rt.js
// Realtime 情報のPiniaストア：1分ごとにA→B→A→B…と交互に叩き、変化時のみ通知ログに積む
//
// state:
//   - lastBySpot: 最新RTDoc（スポット別）
//   - notifyLog: 変更イベントの履歴（トースト等の通知で使用）
//   - timerId: setTimeout のID（nullで停止）
//   - cursor: 現在叩くスポットのインデックス
//   - spotOrder: ポーリング対象の spot_id 配列（ナビ plan の waypoints 由来）
//
// actions:
//   - startPolling(waypoints): 60秒間隔で1スポットずつ順繰りにGET
//   - stopPolling(): ポーリング停止
//   - tick(): 実1回分のフェッチ＆差分判定
//   - reset(): クリア
//
// getters:
//   - getLatest(spotId): そのスポットの最新RTDoc or null
//
// 比較ルール（変更検知）:
//   - w, u, c は常に比較
//   - h は (u > 0) のときだけ比較対象。u=0のときは無視（undefined扱い）

import { defineStore } from 'pinia'
import { fetchSpotRT } from '@/lib/realtime' // @ は src エイリアス想定。未設定なら相対に変更: '../lib/realtime'
import { applyDownlinkToSpotMap } from '@/lib/realtimeDownlink'

const POLL_INTERVAL_MS = 60_000

/**
 * @typedef {Object} RTDoc
 * @property {string} s
 * @property {number} w
 * @property {number} u
 * @property {number=} h
 * @property {number} c
 */

/**
 * @typedef {{ spot_id: string } | string} WaypointRef
 */

export const useRtStore = defineStore('rt', {
  state: () => ({
    /** @type {Record<string, RTDoc>} */
    lastBySpot: {},
    /** @type {Array<{ spot_id: string, prev: RTDoc|null, next: RTDoc, at: number }>} */
    notifyLog: [],
    /** @type {number|null} */
    timerId: null,
    /** @type {boolean} */
    pollingActive: false,
    /** @type {number} */
    pollingGeneration: 0,
    /** @type {number} */
    cursor: 0,
    /** @type {string[]} */
    spotOrder: [],
    /** @type {boolean} */
    stale: false,
    /** @type {number|null} */
    lastReceivedAt: null,
  }),

  getters: {
    /**
     * 最新のRTDocを返す（なければ null）
     * @returns {(spotId: string) => RTDoc | null}
     */
    getLatest: (state) => (spotId) => state.lastBySpot[spotId] ?? null
  },

  actions: {
    /**
     * Waypoints配列から spot_id の順序配列をセット
     * @param {WaypointRef[]} waypoints
     */
    setSpotOrder(waypoints) {
      const order = []
      for (const wp of waypoints ?? []) {
        if (typeof wp === 'string') {
          order.push(wp)
        } else if (wp && typeof wp === 'object' && 'spot_id' in wp && wp.spot_id) {
          order.push(String(wp.spot_id))
        }
      }
      this.spotOrder = order
      // カーソルはリセット（A→B→A→B… を1から）
      this.cursor = 0
    },

    /**
     * ポーリング開始（60秒ごとに1スポットだけ叩く）
     * @param {WaypointRef[]} waypoints
     */
    startPolling(waypoints) {
      // 既に動作中なら一旦止める
      this.stopPolling()
      this.setSpotOrder(waypoints)
      if (this.spotOrder.length === 0) {
        return
      }
      this.pollingActive = true
      // すぐに1回叩いてからスケジュール
      this._tickAndSchedule(this.pollingGeneration)
    },

    /**
     * 停止
     */
    stopPolling() {
      this.pollingActive = false
      this.pollingGeneration += 1
      if (this.timerId !== null) {
        clearTimeout(this.timerId)
        this.timerId = null
      }
    },

    /**
     * 内部：tick実行後、次回を予約
     */
    _tickAndSchedule(generation = this.pollingGeneration) {
      if (!this.pollingActive || generation !== this.pollingGeneration) return
      // 実行
      this.tick().finally(() => {
        if (!this.pollingActive || generation !== this.pollingGeneration) return
        // ドリフトを避けるため setTimeout で逐次スケジュール
        this.timerId = setTimeout(() => this._tickAndSchedule(generation), POLL_INTERVAL_MS)
      })
    },

    /**
     * 1スポット分の取得＆差分判定（A→B→A→B...の1ステップ）
     */
    async tick() {
      if (this.spotOrder.length === 0) return
      // 現在のスポット
      const spotId = this.spotOrder[this.cursor]
      // 次回カーソルを先に進めておく
      this.cursor = (this.cursor + 1) % this.spotOrder.length

      // ★ 追加：前回値から etag（軽ハッシュ）を作成し、fetch に渡す
      const prev = this.lastBySpot[spotId] ?? null
      const etag = this._calcEtag(prev)

      const { status, body } = await fetchSpotRT(spotId, { etag })

      if (status === 204 || status === 304 || status === 0) {
        // 204=未到達 or 変更なし(null) / 0=失敗 → 何もしない（次周期へ）
        return
      }
      if (status !== 200 || !body) {
        return
      }

      // 差分判定
      const next = /** @type {RTDoc} */ (body)
      const changed = !this._isSame(prev, next)

      // 最新値を更新
      this.lastBySpot[spotId] = next

      // 変更があれば通知ログに積む
      if (changed) {
        this.notifyLog.push({
          spot_id: spotId,
          prev,
          next,
          at: Date.now()
        })
      }
    },

    /** LoRa ダウンリンクを manifest.spots の順に適用する。 */
    applyDownlink(payload, manifest) {
      const result = applyDownlinkToSpotMap(this.lastBySpot, payload, manifest)
      this.stale = result.stale
      if (result.applied) this.lastReceivedAt = result.receivedAt
    },

    /**
     * 変更検知（w,u,c は常に、h は u>0 のときのみ比較）
     * @param {RTDoc|null} a
     * @param {RTDoc} b
     * @returns {boolean} same?
     */
    _isSame(a, b) {
      if (!a) return false
      if (a.w !== b.w) return false
      if (a.u !== b.u) return false
      if (a.c !== b.c) return false
      // hの扱い：u>0のときだけ比較。u==0なら両方無視（undefined扱い）
      if (b.u > 0) {
        // a側もu>0でなければ差分とみなす
        if (a.u <= 0) return false
        const ah = typeof a.h === 'number' ? a.h : undefined
        const bh = typeof b.h === 'number' ? b.h : undefined
        if (ah !== bh) return false
      }
      return true
    },

    /**
     * etag（1バイト程度の軽ハッシュ）を生成
     * - 値がなければ undefined（LoRa側で downlink 省略判定には使わない）
     * - 変更検知レベルなので衝突許容、実装は簡易でOK
     * @param {RTDoc|null} doc
     * @returns {number|undefined}
     */
    _calcEtag(doc) {
      if (!doc) return undefined
      // bit パッキング: [ w(2) | u(2) | c(3) | hbit(1) ] = 8bit
      // hbit は (u>0 && hが数値) のとき 1、それ以外 0
      const w = (doc.w & 0b11) << 6
      const u = (doc.u & 0b11) << 4
      const c = (doc.c & 0b111) << 1
      const hbit = (doc.u > 0 && typeof doc.h === 'number') ? 1 : 0
      return (w | u | c | hbit) & 0xff
    },

    /**
     * 全消去（ナビ終了時など）
     */
    reset() {
      this.stopPolling()
      this.lastBySpot = {}
      this.notifyLog = []
      this.cursor = 0
      this.spotOrder = []
      this.pollingActive = false
      this.pollingGeneration += 1
      this.stale = false
      this.lastReceivedAt = null
    }
  }
})
