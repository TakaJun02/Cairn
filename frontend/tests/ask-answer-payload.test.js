import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildChipAnswerPayload,
  buildFreeTextAnswerPayload,
  optionLabel,
  optionValue,
} from '../src/lib/askAnswer.js'

test('clarify のチップは resolves:{surface,value} を組み立てる', () => {
  const prompt = { kind: 'clarify', surface: '2番目のやつ' }
  const option = { label: '鶴間池', value: 'spot_012' }

  assert.deepEqual(buildChipAnswerPayload(prompt, option), {
    answer: '鶴間池',
    resolves: { surface: '2番目のやつ', value: 'spot_012' },
  })
})

test('ask_user(preference) のチップは resolves:{slot,value} を組み立てる', () => {
  const prompt = { kind: 'ask_user', slot: 'mobility' }
  // AskUserState.options はラベル文字列のみで届く(サーバー契約)。
  const option = '30分程度なら'

  assert.deepEqual(buildChipAnswerPayload(prompt, option), {
    answer: '30分程度なら',
    resolves: { slot: 'mobility', value: '30分程度なら' },
  })
})

test('自由入力は resolves を付けない(answered_by は free_text になる)', () => {
  assert.deepEqual(buildFreeTextAnswerPayload('  鶴間池でお願いします  '), {
    answer: '鶴間池でお願いします',
  })
  assert.equal(buildFreeTextAnswerPayload('   '), null)
  assert.equal(buildFreeTextAnswerPayload(''), null)
})

test('prompt や label が無ければチップ payload は組み立てない', () => {
  assert.equal(buildChipAnswerPayload(null, { label: '鶴間池' }), null)
  assert.equal(buildChipAnswerPayload({ kind: 'clarify' }, {}), null)
  assert.equal(buildChipAnswerPayload({ kind: 'clarify' }, ''), null)
})

test('optionLabel / optionValue は文字列とオブジェクトの両方を扱う', () => {
  assert.equal(optionLabel('あまり歩きたくない'), 'あまり歩きたくない')
  assert.equal(optionLabel({ label: '鶴間池', value: 'spot_012' }), '鶴間池')
  assert.equal(optionValue('あまり歩きたくない'), 'あまり歩きたくない')
  assert.equal(optionValue({ label: '鶴間池', value: 'spot_012' }), 'spot_012')
  assert.equal(optionValue({ label: '鶴間池' }), '鶴間池')
})
