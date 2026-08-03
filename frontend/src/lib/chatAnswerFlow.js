// frontend/src/lib/chatAnswerFlow.js
//
// `stores/chat.js` の非同期状態遷移のうち、Pinia/router に依存しない部分を
// 切り出したもの(2026-08-04、レビュー是正・裁定15)。`stores/chat.js` から
// 実装として呼ばれると同時に、ここを直接ユニットテストすることで壊れやすい
// 非同期の順序を固定する:
//
//   (a) `sendMessage` は質問フォーム表示中(`currentPrompt`)なら `isLoading`
//       に関係なく常に `/chat/answer` 経路へ回す(質問中の通常入力欄を塞がない)
//   (b) `sendAnswer` は POST が成功して初めてフォームを消す(失敗時は復元)
//   (c) リロード後のポーリング中に新しい質問(pending)が現れたら、
//       ターン完了を待たずにそれを表示する(Q1 回答 → Q2 表示)
//
// `@/` エイリアス・Pinia・router に依存しないため、`npm test`
// (プレーンな node:test)から直接テストできる。

/**
 * `sendMessage` が通常送信と `/chat/answer` 送信のどちらに回すかを決める。
 * 質問フォーム表示中(`hasPendingPrompt`)は、ターン実行中で `isLoading` が
 * true でも常に回答経路を優先する(裁定15a)。
 */
export function decideSendTarget({ content, hasPendingPrompt, isLoading }) {
  if (!content) return 'empty'
  if (hasPendingPrompt) return 'answer'
  if (isLoading) return 'busy'
  return 'send'
}

/**
 * `POST /chat/answer` を送り、成功して初めてフォームを消す(裁定15b)。
 * 失敗時は呼び出し元がフォームを復元できるよう、成功/失敗を明示的に返す
 * (例外は投げない — 呼び出し元の `isAnswering` 管理を崩さないため)。
 */
export async function submitAnswer({ payload, postChatAnswer, onSuccess, onFailure }) {
  try {
    await postChatAnswer(payload)
    await onSuccess?.(payload)
    return { ok: true }
  } catch (error) {
    await onFailure?.(error)
    return { ok: false, error }
  }
}

/**
 * ライブ SSE が無い間、ターンの決着(次の質問 or 完了)を `GET /thread` で
 * ポーリングする(裁定15c)。`session.pending` が立てば(= 次の質問。
 * 例: Q1 回答 → Q2)即座に `onPending` へ渡してポーリングを終える —
 * これを待たずに次の試行へ進むと、次の質問を画面に出せないまま
 * 最大試行数まで無反応になる(レビュー指摘の実バグ)。
 */
export async function pollUntilTurnSettles({
  getThread,
  messageCountBefore,
  maxAttempts,
  intervalMs,
  onPending,
  onSettled,
  onPollError,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}) {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    await sleep(intervalMs)
    let session = null
    try {
      session = await getThread()
    } catch (error) {
      await onPollError?.(error)
      continue
    }
    if (session?.pending) {
      await onPending?.(session)
      return { settled: false, pending: true }
    }
    const total = (session?.messages || []).length
    if (total > messageCountBefore) {
      await onSettled?.(session)
      return { settled: true, pending: false }
    }
  }
  return { settled: false, pending: false, timedOut: true }
}
