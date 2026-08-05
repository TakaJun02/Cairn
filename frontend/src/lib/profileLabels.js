// frontend/src/lib/profileLabels.js
//
// `party` / `mobility` / `pace` の内部 enum を日本語ラベルへ変換する対応表
// (Docs/30_design/frontend_design_system.md §7.7 / P7)。
//
// enum の定義元: Docs/30_design/recommendation_planning.md §（プロファイル）/
// Docs/30_design/agent_react_architecture.md(`Mobility` 型)。
//
// 対応表にない値が来たら、呼び出し側で「その項目を落とす」(生の値を出さない)。
// 既に `spotDisplay.js` が `spot_id` に対して同じ方針を取っている
// (直近コミット af6eb0b)ので、それに揃える。

const PARTY_LABELS = {
  family_kids: '家族(子連れ)',
  couple: 'カップル',
  solo: 'ひとり旅',
  senior: 'シニア',
  group: 'グループ',
}

const MOBILITY_LABELS = {
  avoid_walk: '歩行を避けたい',
  short_walk_ok: '軽い徒歩ならOK',
  hike_ok: '登山もOK',
}

const PACE_LABELS = {
  packed: 'しっかり周る',
  relaxed: 'のんびり',
}

/**
 * `party` の enum 値を日本語ラベルへ変換する。対応表にない値(未知の enum・
 * 空文字・null 等)は `null` を返す(呼び出し側で描画から落とす)。
 * @param {string | null | undefined} value
 * @returns {string | null}
 */
export function partyLabel(value) {
  return PARTY_LABELS[value] ?? null
}

/**
 * `mobility` の enum 値を日本語ラベルへ変換する。対応表にない値は `null`。
 * @param {string | null | undefined} value
 * @returns {string | null}
 */
export function mobilityLabel(value) {
  return MOBILITY_LABELS[value] ?? null
}

/**
 * `pace` の enum 値を日本語ラベルへ変換する。対応表にない値は `null`。
 * @param {string | null | undefined} value
 * @returns {string | null}
 */
export function paceLabel(value) {
  return PACE_LABELS[value] ?? null
}

/**
 * プロファイル `{ party, mobility, pace }` を日本語ラベルの配列にする。
 * 対応表にない値・空値の項目は配列から落とす(生の値を出さない)。
 * 全項目が落ちた場合は空配列を返す(呼び出し側は「何も描画しない」)。
 * @param {{ party?: string, mobility?: string, pace?: string } | null | undefined} profile
 * @returns {string[]}
 */
export function profileLabels(profile) {
  if (!profile) return []
  return [
    partyLabel(profile.party),
    mobilityLabel(profile.mobility),
    paceLabel(profile.pace),
  ].filter((label) => typeof label === 'string' && label.length > 0)
}
