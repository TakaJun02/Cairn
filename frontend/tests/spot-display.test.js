import test from 'node:test'
import assert from 'node:assert/strict'

import { maskSpotIds, resolveCandidateName } from '../src/lib/spotDisplay.js'

test('resolveName が引ける spot_id は表示名へ置換する', () => {
  const resolveName = (id) => ({ spot_014: '元滝伏流水' }[id])
  assert.equal(
    maskSpotIds('必須希望の「spot_014」を旅程に入れられませんでした', resolveName),
    '必須希望の「元滝伏流水」を旅程に入れられませんでした',
  )
})

test('resolveName が引けない spot_id は「不明な地点」へ置換する(id へフォールバックしない)', () => {
  const resolveName = () => undefined
  const masked = maskSpotIds('必須希望の「spot_999」を旅程に入れられませんでした', resolveName)
  assert.equal(masked, '必須希望の「不明な地点」を旅程に入れられませんでした')
  assert.ok(!masked.includes('spot_999'))
})

test('resolveName を渡さなくても spot_id を露出しない', () => {
  const masked = maskSpotIds('spot_014 に行きます', undefined)
  assert.ok(!masked.includes('spot_014'))
  assert.equal(masked, '不明な地点 に行きます')
})

test('spot_ を含まない文字列はそのまま返す', () => {
  const text = '神社の訪問が希望上限を1件超えています'
  assert.equal(maskSpotIds(text, () => '無視される'), text)
})

test('英数字・アンダースコアに続く spot_ は境界外としてマッチしない', () => {
  // guards._FORBIDDEN_SPOT_ID_RE と同じ境界規則: 直前が ASCII 英数字/_ なら
  // トークンの開始とみなさない(例: `myspot_014` は `spot_014` を含まない扱い)。
  const text = 'myspot_014 は対象外'
  assert.equal(maskSpotIds(text, () => '無視される'), text)
})

test('文字列でない・空文字は加工せず返す', () => {
  assert.equal(maskSpotIds('', () => 'x'), '')
  assert.equal(maskSpotIds(null, () => 'x'), null)
  assert.equal(maskSpotIds(undefined, () => 'x'), undefined)
})

test('複数の spot_id が混在しても個別に解決する', () => {
  const resolveName = (id) => ({ spot_a: '丸池様', spot_b: '元滝伏流水' }[id])
  const masked = maskSpotIds('spot_a と spot_c を経由して spot_b へ', resolveName)
  assert.equal(masked, '丸池様 と 不明な地点 を経由して 元滝伏流水 へ')
})

// F1(lookbehind 廃止): Safari 16.0〜16.3 で `(?<!...)` が SyntaxError に
// なるため、先行文字を捕獲する形へ書き換えた。境界規則が退行していないこと
// を確認する。

test('スペース区切りの連続トークンはどちらもマスクされる', () => {
  const resolveName = (id) => ({ spot_001: '元滝伏流水', spot_002: '丸池様' }[id])
  const masked = maskSpotIds('spot_001 spot_002 を訪問します', resolveName)
  assert.equal(masked, '元滝伏流水 丸池様 を訪問します')
  assert.ok(!masked.includes('spot_001') && !masked.includes('spot_002'))
})

test('カンマ区切りの連続トークンはどちらもマスクされる', () => {
  const resolveName = (id) => ({ spot_001: '元滝伏流水', spot_002: '丸池様' }[id])
  const masked = maskSpotIds('spot_001,spot_002 を訪問します', resolveName)
  assert.equal(masked, '元滝伏流水,丸池様 を訪問します')
  assert.ok(!masked.includes('spot_001') && !masked.includes('spot_002'))
})

test('英数字に続く spot_ トークンは境界外としてマッチしない(re/X いずれも)', () => {
  const resolveName = () => '無視される'
  assert.equal(maskSpotIds('respot_001 は対象外', resolveName), 'respot_001 は対象外')
  assert.equal(maskSpotIds('Xspot_001 も対象外', resolveName), 'Xspot_001 も対象外')
})

test('行頭のトークンもマスクされる', () => {
  const resolveName = (id) => ({ spot_001: '元滝伏流水' }[id])
  const masked = maskSpotIds('spot_001 に行きます', resolveName)
  assert.equal(masked, '元滝伏流水 に行きます')
})

// F7: 候補(candidate)の表示名解決。旧形式の永続 meta では presented[].
// name_ja に生の spot_id がそのまま入っていることがあり、truthy なので
// `item.name_ja || fallback` を素通りしていた。

test('resolveCandidateName: name_ja が生の spot_id のときは解決名へ置換する(復元経路)', () => {
  const resolveName = (id) => ({ spot_014: '元滝伏流水' }[id])
  const item = { spot_id: 'spot_014', name_ja: 'spot_014' }
  assert.equal(resolveCandidateName(item, resolveName), '元滝伏流水')
})

test('resolveCandidateName: name_ja が生の spot_id で解決もできないときは「不明な地点」', () => {
  const item = { spot_id: 'spot_999', name_ja: 'spot_999' }
  const result = resolveCandidateName(item, () => undefined)
  assert.equal(result, '不明な地点')
  assert.ok(!result.includes('spot_999'))
})

test('resolveCandidateName: name_ja が正常な表示名のときはそのまま使う(live SSE 経路)', () => {
  const item = { spot_id: 'spot_014', name_ja: '元滝伏流水' }
  assert.equal(resolveCandidateName(item, () => '無視される'), '元滝伏流水')
})

test('resolveCandidateName: name_ja が無いときは spot_id を resolveName で解決する', () => {
  const resolveName = (id) => ({ spot_014: '元滝伏流水' }[id])
  assert.equal(resolveCandidateName({ spot_id: 'spot_014' }, resolveName), '元滝伏流水')
  assert.equal(resolveCandidateName({ spot_id: 'spot_999' }, resolveName), '不明な地点')
})
