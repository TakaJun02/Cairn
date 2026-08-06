import test from 'node:test'
import assert from 'node:assert/strict'

import {
  decideSendTarget,
  pollUntilTurnSettles,
  submitAnswer,
} from '../src/lib/chatAnswerFlow.js'

// ---------------------------------------------------------------------------
// decideSendTarget(裁定15a): 質問フォーム表示中は isLoading より優先する
// ---------------------------------------------------------------------------

test('質問フォーム表示中は isLoading=true でも回答経路を選ぶ', () => {
  assert.equal(
    decideSendTarget({ content: '30分程度なら', hasPendingPrompt: true, isLoading: true }),
    'answer',
  )
})

test('質問フォームが無ければ isLoading 中の送信をブロックする', () => {
  assert.equal(
    decideSendTarget({ content: 'こんにちは', hasPendingPrompt: false, isLoading: true }),
    'busy',
  )
})

test('空文字は常にブロックする(pending の有無に関わらず)', () => {
  // chat.js は呼び出し前に content を trim 済みで渡す(空白のみの入力は
  // 呼び出し元で空文字になる)。
  assert.equal(
    decideSendTarget({ content: '', hasPendingPrompt: true, isLoading: false }),
    'empty',
  )
  assert.equal(
    decideSendTarget({ content: '   '.trim(), hasPendingPrompt: false, isLoading: false }),
    'empty',
  )
})

test('通常時は send を返す', () => {
  assert.equal(
    decideSendTarget({ content: 'こんにちは', hasPendingPrompt: false, isLoading: false }),
    'send',
  )
})

// ---------------------------------------------------------------------------
// submitAnswer(裁定15b): POST 成功時だけ onSuccess、失敗時は onFailure
// ---------------------------------------------------------------------------

test('POST 成功時は onSuccess を呼び ok:true を返す', async () => {
  const calls = []
  const result = await submitAnswer({
    payload: { answer: '30分程度なら' },
    postChatAnswer: async (payload) => {
      calls.push(['post', payload])
    },
    onSuccess: async (payload) => {
      calls.push(['success', payload])
    },
    onFailure: async () => {
      calls.push(['failure'])
    },
  })

  assert.deepEqual(result, { ok: true })
  assert.deepEqual(calls, [
    ['post', { answer: '30分程度なら' }],
    ['success', { answer: '30分程度なら' }],
  ])
})

test('POST 失敗時は onFailure を呼び、例外を投げずに ok:false を返す', async () => {
  const calls = []
  const error = Object.assign(new Error('network down'), { status: 500 })
  const result = await submitAnswer({
    payload: { answer: '30分程度なら' },
    postChatAnswer: async () => {
      throw error
    },
    onSuccess: async () => {
      calls.push('success')
    },
    onFailure: async (err) => {
      calls.push(['failure', err])
    },
  })

  assert.deepEqual(result, { ok: false, error })
  assert.deepEqual(calls, [['failure', error]])
})

test('409 でも例外を投げず onFailure に error.status を渡す', async () => {
  const error = Object.assign(new Error('conflict'), { status: 409 })
  let received = null
  const result = await submitAnswer({
    payload: { answer: 'x' },
    postChatAnswer: async () => {
      throw error
    },
    onFailure: async (err) => {
      received = err
    },
  })

  assert.equal(result.ok, false)
  assert.equal(received.status, 409)
})

// ---------------------------------------------------------------------------
// pollUntilTurnSettles(裁定15c): 新しい pending を見逃さない
// ---------------------------------------------------------------------------

function immediateSleep() {
  return () => Promise.resolve()
}

test('新しい pending が現れたら即座に onPending を呼びポーリングを終える(Q1→Q2)', async () => {
  const events = []
  let call = 0
  const getThread = async () => {
    call += 1
    if (call === 1) {
      return { messages: [{ id: 1 }], pending: null }
    }
    // Q1 の回答直後、まだ assistant 側は完了していないが Q2 が現れた。
    return { messages: [{ id: 1 }, { id: 2 }], pending: { kind: 'ask_user', slot: 'party' } }
  }

  const result = await pollUntilTurnSettles({
    getThread,
    messageCountBefore: 1,
    maxAttempts: 10,
    intervalMs: 1,
    onPending: async (session) => events.push(['pending', session]),
    onSettled: async (session) => events.push(['settled', session]),
    sleep: immediateSleep(),
  })

  assert.deepEqual(result, { settled: false, pending: true })
  assert.equal(events.length, 1)
  assert.equal(events[0][0], 'pending')
  assert.equal(events[0][1].pending.slot, 'party')
})

test('pending が無くメッセージが増えたら onSettled を呼んで終える', async () => {
  const events = []
  let call = 0
  const getThread = async () => {
    call += 1
    if (call < 3) {
      return { messages: [{ id: 1 }], pending: null }
    }
    return { messages: [{ id: 1 }, { id: 2 }], pending: null }
  }

  const result = await pollUntilTurnSettles({
    getThread,
    messageCountBefore: 1,
    maxAttempts: 10,
    intervalMs: 1,
    onSettled: async (session) => events.push(['settled', session]),
    sleep: immediateSleep(),
  })

  assert.deepEqual(result, { settled: true, pending: false })
  assert.equal(events.length, 1)
  assert.equal(events[0][0], 'settled')
})

test('最大試行数に達したら timedOut を返す', async () => {
  const getThread = async () => ({ messages: [{ id: 1 }], pending: null })

  const result = await pollUntilTurnSettles({
    getThread,
    messageCountBefore: 1,
    maxAttempts: 3,
    intervalMs: 1,
    sleep: immediateSleep(),
  })

  assert.deepEqual(result, { settled: false, pending: false, timedOut: true })
})

test('GET /thread が失敗しても onPollError を呼んでポーリングを継続する', async () => {
  const errors = []
  let call = 0
  const getThread = async () => {
    call += 1
    if (call === 1) throw new Error('network error')
    return { messages: [{ id: 1 }, { id: 2 }], pending: null }
  }

  const result = await pollUntilTurnSettles({
    getThread,
    messageCountBefore: 1,
    maxAttempts: 5,
    intervalMs: 1,
    onPollError: async (error) => errors.push(error.message),
    sleep: immediateSleep(),
  })

  assert.equal(result.settled, true)
  assert.deepEqual(errors, ['network error'])
})
