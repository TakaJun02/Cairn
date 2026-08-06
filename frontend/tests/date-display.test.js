import test from 'node:test'
import assert from 'node:assert/strict'

import { humanizeIsoDates, formatDurationJa } from '../src/lib/dateDisplay.js'

test('正常な ISO 日付は「M月D日(曜)」へ整形される', () => {
  // 2026-08-05 は水曜日
  assert.equal(humanizeIsoDates('2026-08-05'), '8月5日(水)')
})

test('文中の括弧内にある ISO 日付も整形される', () => {
  assert.equal(
    humanizeIsoDates('⚠ 日付は明日(2026-08-05)と仮定'),
    '⚠ 日付は明日(8月5日(水))と仮定',
  )
})

test('文中の複数箇所の ISO 日付がそれぞれ整形される', () => {
  // 2026-08-05 は水曜日、2026-08-06 は木曜日
  assert.equal(
    humanizeIsoDates('起点は 2026-08-05 、終点は 2026-08-06 と仮定'),
    '起点は 8月5日(水) 、終点は 8月6日(木) と仮定',
  )
})

test('暦として実在しない日付(月が範囲外)はそのまま残す', () => {
  const text = '日付は 2026-13-45 と仮定'
  assert.equal(humanizeIsoDates(text), text)
})

test('暦として実在しない日付(9999-99-99)はそのまま残す', () => {
  const text = '不正な日付は 9999-99-99 のまま'
  assert.equal(humanizeIsoDates(text), text)
})

test('日を跨いだ繰り上がりで実在しなくなる日付(2月30日)もそのまま残す', () => {
  const text = '2026-02-30 は存在しない'
  assert.equal(humanizeIsoDates(text), text)
})

test('ISO 日付を含まない文はそのまま返す', () => {
  const text = '一部の希望条件を調整しました。'
  assert.equal(humanizeIsoDates(text), text)
})

test('文字列でない・空文字は加工せず返す', () => {
  assert.equal(humanizeIsoDates(''), '')
  assert.equal(humanizeIsoDates(null), null)
  assert.equal(humanizeIsoDates(undefined), undefined)
})

test('数字が連続する非日付トークンは誤って部分一致させない', () => {
  const text = '注文番号は 12026-08-051 です'
  assert.equal(humanizeIsoDates(text), text)
})

test('formatDurationJa: 60分未満は「n分」', () => {
  assert.equal(formatDurationJa(40), '40分')
})

test('formatDurationJa: ちょうど60分は「1時間」(端数0分は省く)', () => {
  assert.equal(formatDurationJa(60), '1時間')
})

test('formatDurationJa: 60分超は「n時間m分」', () => {
  assert.equal(formatDurationJa(90), '1時間30分')
  assert.equal(formatDurationJa(175), '2時間55分')
})

test('formatDurationJa: 0 は null', () => {
  assert.equal(formatDurationJa(0), null)
})

test('formatDurationJa: 負の値は null', () => {
  assert.equal(formatDurationJa(-10), null)
})

test('formatDurationJa: NaN は null', () => {
  assert.equal(formatDurationJa(NaN), null)
})

test('formatDurationJa: 非数(文字列・undefined・null)は null', () => {
  assert.equal(formatDurationJa('abc'), null)
  assert.equal(formatDurationJa(undefined), null)
  assert.equal(formatDurationJa(null), null)
})

test('formatDurationJa: 小数は Math.round で整数化してから換算する', () => {
  assert.equal(formatDurationJa(59.6), '1時間')
  assert.equal(formatDurationJa(40.4), '40分')
})
