// src/lib/dateDisplay.js
//
// ユーザー可視のテキストに混ざる ISO 形式の日付(`YYYY-MM-DD`)を
// 「M月D日(曜)」の人の書式へ置換する純粋関数
// ([frontend_design_system.md §7.7.1](../../../Docs/30_design/frontend_design_system.md))。
// `spotDisplay.js` と同じ層・同じ考え方: Pinia・router に依存しないので
// `npm test`(プレーンな node:test)から直接テストできる。
//
// サーバー契約では旅程カードの見出し・時刻は既に人の書式へ整形しているが
// (OC_ChatMessage.vue の `formatDateHeading`/`formatMinute`)、
// `itinerary.assumptions[]`(仮の前提。例:「日付は明日(2026-08-05)と仮定」)
// は文中に生の ISO 日付を含んだままサーバーから来る。さらに旧形式で
// 永続化済みの文が undo や `GET /thread` で再浮上する経路があるため
// (`spot_id` を `maskSpotIds` で防いでいるのと同じ理由)、表示直前に
// この関数を通して機械の書式を人の書式へ揃える。

const ISO_DATE_RE = /\b(\d{4})-(\d{2})-(\d{2})\b/g
const WEEKDAY_JA = ['日', '月', '火', '水', '木', '金', '土']

// 桁数だけでなく暦として実在する日付かを確認する。JS の `Date` は
// 月・日の範囲外の値を silently に繰り上げてしまう(例:
// `new Date(2026, 12, 45)` は例外にならず翌年 1 月へ丸まる)ため、
// 構築後の年・月・日を読み戻して一致するかで実在性を確かめる
// (round-trip チェック)。
function isValidIsoDate(year, month, day) {
  if (month < 1 || month > 12 || day < 1 || day > 31) return false
  const date = new Date(year, month - 1, day)
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day
}

/**
 * text 中の `YYYY-MM-DD` を「M月D日(曜)」へ置換する。文中のどこにあっても
 * (括弧内なども)置換対象になる。暦として実在しない日付
 * (例: `9999-99-99`、`2026-13-45`)は置換せずそのまま残す
 * (壊れた整形を出さないため)。
 *
 * @param {string} text
 * @returns {string}
 */
export function humanizeIsoDates(text) {
  if (typeof text !== 'string' || !text) return text
  return text.replace(ISO_DATE_RE, (match, yearStr, monthStr, dayStr) => {
    const year = Number(yearStr)
    const month = Number(monthStr)
    const day = Number(dayStr)
    if (!isValidIsoDate(year, month, day)) return match
    const weekday = WEEKDAY_JA[new Date(year, month - 1, day).getDay()]
    return `${month}月${day}日(${weekday})`
  })
}

/**
 * 分(number)を日本語の時間長表記へ整形する
 * ([frontend_design_system.md §7.6.4 の 12](../../../Docs/30_design/frontend_design_system.md))。
 * 60 分未満は `{m}分`、60 分以上は `{h}時間{m}分`(端数が 0 分なら `{h}時間`)。
 * 旅程カードの滞在・移動はどちらもソルバーの分刻み出力なので、この関数で
 * 「175分」ではなく「2時間55分」のように人が読む長さへ揃える。
 *
 * 正の有限数でなければ `null` を返す(呼び出し側でその行ごと省く)。
 * 小数は `Math.round` で整数化してから換算する。
 *
 * @param {number} minutes
 * @returns {string|null}
 */
export function formatDurationJa(minutes) {
  const value = Number(minutes)
  if (!Number.isFinite(value) || value <= 0) return null
  const rounded = Math.round(value)
  if (rounded <= 0) return null
  const hours = Math.floor(rounded / 60)
  const mins = rounded % 60
  if (hours <= 0) return `${mins}分`
  if (mins === 0) return `${hours}時間`
  return `${hours}時間${mins}分`
}
