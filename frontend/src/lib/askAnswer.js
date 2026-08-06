// src/lib/askAnswer.js
//
// `POST /api/v1/chat/answer` へ送る payload の組み立て(chat_sse.md §1.4)。
// `stores/chat.js` の `selectPromptOption`/`sendAnswer` から使う純粋関数だけを
// ここへ切り出す(`@/` エイリアス・Pinia・router に依存しないので、
// `npm test`(プレーンな node:test)から直接テストできる)。

export function optionLabel(option) {
  return typeof option === 'string' ? option : option?.label
}

export function optionValue(option) {
  if (typeof option === 'string') return option
  return option?.value || option?.label
}

/**
 * チップ(選択肢)を選んだときの payload。
 *
 * `kind:"clarify"` は `resolves:{surface,value}`、`kind:"ask_user"`
 * (preference)は `resolves:{slot,value}`(§1.4)。`answered_by` はサーバー側が
 * `resolves` の有無で `"chip"` と判定する。
 *
 * `ask_user` の選択肢は表示ラベルのみで届く(`AskUserState.options` は
 * `list[str]`)ため、`value` にはラベルそのものを使う — サーバーは
 * `resolves.value` を照合には使わず、`answered_by` の判定にだけ使う。
 */
export function buildChipAnswerPayload(prompt, option) {
  const label = optionLabel(option)
  if (!prompt || !label) return null
  const value = optionValue(option) || label
  const resolves = prompt.kind === 'clarify'
    ? { surface: prompt.surface, value }
    : { slot: prompt.slot, value }
  return { answer: label, resolves }
}

/** 自由入力からの payload。`resolves` を付けない(`answered_by:"free_text"`)。 */
export function buildFreeTextAnswerPayload(text) {
  const answer = String(text ?? '').trim()
  if (!answer) return null
  return { answer }
}
