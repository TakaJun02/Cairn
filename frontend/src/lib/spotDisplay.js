// src/lib/spotDisplay.js
//
// ユーザー可視のテキストから `spot_id` トークンを取り除くための純粋関数
// ([25 §1-7](../../../Docs/25_known_issues.md) / [frontend_nav.md §2.4]
// (../../../Docs/30_design/frontend_nav.md))。Pinia・router に依存しないので
// `npm test`(プレーンな node:test)から直接テストできる(`askAnswer.js` と
// 同じ流儀)。
//
// サーバー契約(chat_sse.md §1.2)では `concessions[].message_ja` は
// `spot_id` を含まないが、旧形式(spot_id 入り)で永続化済みの版が
// undo/GET 等で再浮上する経路への表示前フィルタとして使う。

// backend の `guards._FORBIDDEN_SPOT_ID_RE` と同じ境界規則(前後が ASCII
// 英数字・アンダースコアでないことを境界とする)の JS 版。ただし
// lookbehind (`(?<!...)`) は Safari 16.0〜16.3 で `new RegExp` 評価時に
// SyntaxError になる(Vite 7 の既定 build target `safari16.0` を esbuild が
// lookbehind 未対応のまま降格するため、モジュール読み込みだけでアプリ全体が
// 起動不能になる)。先行 1 文字(または行頭)を捕獲グループにして置換時に
// 付け戻す形で、lookbehind なしに同じ境界規則を実現する。
const SPOT_ID_TOKEN_RE = /(^|[^0-9A-Za-z_])(spot_[a-z0-9]+)/gi

/**
 * text 中の spot_id トークンを resolveName(id) の結果へ置換する。
 * 解決できなければ spot_id へフォールバックせず「不明な地点」にする。
 *
 * @param {string} text
 * @param {(spotId: string) => string | null | undefined} resolveName
 * @returns {string}
 */
export function maskSpotIds(text, resolveName) {
  if (typeof text !== 'string' || !text) return text
  return text.replace(SPOT_ID_TOKEN_RE, (_match, lead, token) => {
    const resolved = typeof resolveName === 'function' ? resolveName(token) : null
    return lead + (resolved || '不明な地点')
  })
}

/**
 * 候補(candidate)アイテムの表示名を解決する純関数
 * ([25 §1-7] レビュー是正 F7)。
 *
 * 旧形式で永続化済みの `presented[].name_ja`(assistant meta。
 * data_model.md §4.4)には、生の `spot_id`(例 "spot_014")がそのまま
 * 入っていることがある。単純な `item.name_ja || fallback` は truthy な
 * spot_id 文字列をそのまま素通りしてしまうため、`name_ja` にも
 * `maskSpotIds` を通す。`name_ja` が無い/空のときは `spot_id` を
 * `resolveName` で解決する(引けなければ「不明な地点」。id へ
 * フォールバックしない)。
 *
 * 復元経路(chat.js `restoredCandidates`)・live SSE 経路(`state:candidates`
 * イベント)のどちらの `candidates.items[]` にも同じ形(`spot_id`/
 * `name_ja`)で渡ってくるため、呼び出し側 1 箇所でこの関数を通せば
 * 両経路をカバーできる。
 *
 * @param {{ name_ja?: string, spot_id?: string }} item
 * @param {(spotId: string) => string | null | undefined} resolveName
 * @returns {string}
 */
export function resolveCandidateName(item, resolveName) {
  const name = item?.name_ja
  if (typeof name === 'string' && name) return maskSpotIds(name, resolveName)
  const spotId = item?.spot_id
  const resolved =
    typeof resolveName === 'function' && typeof spotId === 'string' ? resolveName(spotId) : null
  return resolved || '不明な地点'
}
