// frontend/src/stores/chat.js
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useUserStore } from '@/stores/user'
import { useNavStore } from '@/stores/nav'
import {
  apiUrl,
  bearerHeaders,
  getThread,
  postChatAnswer,
  undoItinerary as requestItineraryUndo,
} from '@/lib/api'
import { buildChipAnswerPayload, buildFreeTextAnswerPayload } from '@/lib/askAnswer'
import { decideSendTarget, pollUntilTurnSettles, submitAnswer } from '@/lib/chatAnswerFlow'
import { createSseParser } from '@/lib/sse'

// ask_user の回答待ち(chat_sse.md §1.4)。リロード後などライブな SSE
// ストリームが無い状態で回答したときのフォールバック用ポーリング間隔・上限。
const ANSWER_POLL_INTERVAL_MS = 1500
const ANSWER_POLL_MAX_ATTEMPTS = 40

function clientId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `message-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function emptyDiff() {
  return { added: [], removed: [], moved: [], retimed: [] }
}

function normalizeItineraryState(state) {
  return {
    ...state,
    kind: 'itinerary',
    phase: state?.phase || 'final',
    itinerary: state?.itinerary || { days: [] },
    diff: state?.diff || emptyDiff(),
    concessions: state?.concessions || state?.itinerary?.concessions || [],
    // 未確認の前提(2026-08-04 追加)。state:itinerary(final/provisional)・
    // undo/redo・GET /api/v1/itinerary はトップレベルに `assumptions` を持つ
    // (tool_adapters.py `_emit_itinerary_state` / api/routers/itinerary.py
    // `_itinerary_state`。`ItineraryState` スキーマ)。**GET /thread だけは
    // スキーマが異なる**(`CurrentItineraryResponse` はトップレベルに
    // `assumptions` を持たず、`itinerary` 本体の中にしか無い)。
    // 2026-08-04 レビュー是正(L-6): 以前のコメントは「GET /thread も同じ
    // スキーマ」と誤記していた。実際はこの後段のフォールバック
    // (`state?.itinerary?.assumptions`)が GET /thread のケースを拾う。
    assumptions: state?.assumptions || state?.itinerary?.assumptions || [],
  }
}

function normalizePending(pending) {
  if (!pending) return null
  const kind = pending.kind || (pending.surface ? 'clarify' : 'ask_user')
  if (kind === 'clarify') {
    return {
      ...pending,
      kind,
      reason: pending.reason,
      options: (pending.options || []).map((option) => ({
        label: option?.label || option?.value || String(option),
        value: option?.value || option?.resolves_to?.value || option?.label || String(option),
      })),
    }
  }
  return {
    ...pending,
    kind: 'ask_user',
    reason: pending.reason,
    options: (pending.options || []).map((option) => (
      typeof option === 'string'
        ? option
        : option?.label || option?.value || String(option)
    )),
  }
}

// data_model.md §4.4: assistant meta.presented は「このターンで recommend が
// 実行されたときだけ」順序つきで書かれる(2026-08-04、レビュー是正・裁定14)。
// 旧 candidate_spot_ids/candidate_names(移行前の行のための後方互換)にも
// フォールバックする。
function restoredCandidates(meta = {}) {
  if (Array.isArray(meta.presented) && meta.presented.length > 0) {
    const items = [...meta.presented]
      .sort((a, b) => (a?.rank ?? 0) - (b?.rank ?? 0))
      .map((item) => ({
        spot_id: item?.spot_id,
        name_ja: item?.name_ja || item?.spot_id,
        reason_materials: {},
      }))
    return { kind: 'candidates', phase: 'final', items }
  }
  const ids = Array.isArray(meta.candidate_spot_ids) ? meta.candidate_spot_ids : []
  const names = Array.isArray(meta.candidate_names) ? meta.candidate_names : []
  if (ids.length === 0) return null
  return {
    kind: 'candidates',
    phase: 'final',
    items: ids.map((spotId, index) => ({
      spot_id: spotId,
      name_ja: names[index] || spotId,
      reason_materials: {},
    })),
  }
}

export const useChatStore = defineStore('chat', () => {
  const userStore = useUserStore()
  const navStore = useNavStore()

  const messages = ref([])
  const isLoading = ref(false)
  const isSessionLoaded = ref(false)
  const profile = ref(null)
  const currentPrompt = ref(null)
  const isUndoing = ref(false)
  // POST /chat/answer 自体の送信中だけを表す(ターン全体の isLoading とは
  // 別。ask_user 待機中も isLoading は true のままなので、フォームの
  // ボタンをそれで無効化すると回答できなくなる)。
  const isAnswering = ref(false)

  let activeController = null
  // ライブな /chat SSE ストリームが読み進み中かどうか。true の間は
  // sendAnswer が POST /chat/answer するだけでよい(続きは同じストリームが
  // 運ぶ)。false ならリロード等でストリームが無いので、GET /thread の
  // ポーリングで取り直す(chat_sse.md §1.5)。
  let isStreamActive = false

  const clearPrompt = () => {
    currentPrompt.value = null
  }

  const setPrompt = (prompt) => {
    currentPrompt.value = normalizePending(prompt)
  }

  const applyState = (state, message) => {
    if (!state?.kind) return

    switch (state.kind) {
      case 'plan':
        message.plan = state
        message.statusText = '回答の手順を確認しています'
        break
      case 'candidates':
        if (!(state.phase === 'provisional' && message.candidates?.phase === 'final')) {
          message.candidates = state
          navStore.setCandidates(state)
        }
        break
      case 'itinerary': {
        const normalized = normalizeItineraryState(state)
        if (!(
          normalized.phase === 'provisional'
          && message.itinerary?.phase === 'final'
          && message.itinerary?.version === normalized.version
        )) {
          message.itinerary = normalized
          void navStore.applyItineraryState(normalized)
        }
        break
      }
      case 'ask_user':
      case 'clarify':
        setPrompt(state)
        // ローディング表示を「回答待ち」に切り替える(質問フォームは別途
        // 表示されるが、スピナーの文言もそれと分かるようにしておく)。
        message.statusText = '回答をお待ちしています'
        break
      case 'profile':
        profile.value = state.profile
        message.profile = state.profile
        break
      case 'searching':
        message.statusText = state.text
        break
      case 'step':
        // ADR-0019: state:step(started/progress/finished)。段2では
        // 実況テキストの表示だけを最小限に追加する(既存の searching と
        // 同じ statusText 経路を再利用)。
        message.statusText = state.status === 'finished' ? '' : (state.label_ja || '')
        break
      default:
        console.warn('[ChatStore] Unknown state kind:', state.kind)
    }
  }

  const applyStreamEvent = (event, data, message) => {
    switch (event) {
      case 'state':
        applyState(data, message)
        break
      case 'token':
        message.content += data?.text || ''
        break
      case 'error':
        message.notices.push(data)
        if (!data?.degraded) message.error = data?.message || '応答の生成に失敗しました。'
        break
      case 'done':
        message.done = data
        message.serverId = data?.message_id ?? null
        message.statusText = ''
        message.isPending = false
        // ターンが終われば生きた待機は無い(§7)。答えそびれた質問が
        // あっても(タイムアウト等)、ここで確実にフォームを消す。
        clearPrompt()
        break
      default:
        console.warn('[ChatStore] Unknown SSE event:', event)
    }
  }

  async function sendMessage(userInput) {
    const content = String(userInput || '').trim()

    // 質問フォーム表示中は、通常入力欄からの送信も /chat/answer に回す
    // (frontend_nav.md §2.3.1 の 5: 下の入力欄は塞がないが、回答待ちの間は
    // その送信先を切り替える)。`isLoading` の検査より**前**に判定する
    // (2026-08-04、レビュー是正・裁定15a): 質問待ち中はターンがまだ実行中
    // で `isLoading` が true のままなので、先に isLoading を見ると
    // この分岐へ絶対に到達できず、質問中の通常入力が常に無視されていた。
    const target = decideSendTarget({
      content,
      hasPendingPrompt: Boolean(currentPrompt.value),
      isLoading: isLoading.value,
    })
    if (target === 'answer') return sendAnswer(content)
    if (target !== 'send') return false

    isLoading.value = true
    messages.value.push({
      id: clientId(),
      content,
      sender: 'user',
      timestamp: new Date(),
    })

    const aiMessageSeed = {
      id: clientId(),
      content: '',
      sender: 'ai',
      timestamp: new Date(),
      isPending: true,
      statusText: '',
      notices: [],
      error: '',
      candidates: null,
      itinerary: null,
      profile: null,
    }
    messages.value.push(aiMessageSeed)
    const aiMessage = messages.value.at(-1)

    const controller = new AbortController()
    activeController = controller
    isStreamActive = true
    let receivedDone = false

    try {
      const response = await fetch(apiUrl('/chat'), {
        method: 'POST',
        signal: controller.signal,
        headers: {
          ...bearerHeaders(userStore.token),
          Accept: 'text/event-stream',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: content }),
      })

      if (!response.ok) {
        let body = null
        try {
          body = await response.json()
        } catch {
          body = null
        }
        const detail = typeof body?.detail === 'string'
          ? body.detail
          : body?.detail?.message
        const error = new Error(detail || `HTTP ${response.status}`)
        error.status = response.status
        error.body = body
        throw error
      }
      if (!response.body) throw new Error('SSE response body is unavailable')

      const decoder = new TextDecoder()
      const parser = createSseParser(({ event, data }) => {
        applyStreamEvent(event, data, aiMessage)
        if (event === 'done') receivedDone = true
      })
      const reader = response.body.getReader()

      // ask_user/clarify を受けても、このループは回答を待つあいだも
      // 回り続ける(ストリームは開いたまま。§7)。回答は sendAnswer が
      // POST /chat/answer で送り、続きのイベントは同じ reader が受ける。
      while (!receivedDone) {
        const { value, done } = await reader.read()
        if (done) break
        parser.push(decoder.decode(value, { stream: true }))
      }
      parser.push(decoder.decode())
      parser.flush()

      if (!receivedDone && !controller.signal.aborted) {
        throw new Error('応答ストリームが完了前に切断されました。')
      }
      return true
    } catch (error) {
      if (error?.name === 'AbortError') {
        aiMessage.stopped = true
        aiMessage.notices.push({
          degraded: true,
          message: '応答の生成を停止しました。',
        })
      } else {
        console.error('[ChatStore] Failed to send message:', error)
        aiMessage.error = error?.message || 'メッセージを送信できませんでした。'
        if (!aiMessage.content) aiMessage.content = 'エラーが発生しました。メッセージを送信できませんでした。'
      }
      return false
    } finally {
      aiMessage.isPending = false
      aiMessage.statusText = ''
      isStreamActive = false
      if (activeController === controller) {
        activeController = null
        isLoading.value = false
      }
    }
  }

  function stopStreaming() {
    activeController?.abort()
  }

  /**
   * `ask_user` への回答(chat_sse.md §1.4)。`POST /api/v1/chat/answer` は
   * 204 のみを返し、イベントはすべて元の SSE ストリームに流れる。
   * ライブなストリームが読み進み中ならここでは何もしなくてよい(reader が
   * 続きを運ぶ)。無ければ(リロード後等)GET /thread をポーリングして
   * 取り直す。
   *
   * POST が成功して初めてフォームを消す(2026-08-04、レビュー是正・
   * 裁定15b)。失敗時はフォームを復元し、再送できるようにする — ただし
   * 409(表示中の質問がサーバー側で既に失効)はフォームを残さない。
   */
  async function sendAnswer(answerText, resolves = null) {
    const payload = resolves
      ? { answer: String(answerText ?? '').trim(), resolves }
      : buildFreeTextAnswerPayload(answerText)
    if (!payload || isAnswering.value) return false

    const previousPrompt = currentPrompt.value
    isAnswering.value = true
    const { ok } = await submitAnswer({
      payload,
      postChatAnswer,
      onSuccess: async () => {
        messages.value.push({
          id: clientId(),
          content: payload.answer,
          sender: 'user',
          timestamp: new Date(),
        })
        // 23_ux_issues.md §6-8: 古い質問への回答送信を防ぐため、フォームは
        // 常に「いま回答を待っている質問」だけを表示する。
        clearPrompt()
        if (!isStreamActive) {
          await waitForTurnToFinishThenRefresh()
        }
      },
      onFailure: (error) => {
        console.error('[ChatStore] Failed to send answer:', error)
        // 409 はサーバー側で既に pending が失効している合図なので、
        // フォームは復元せず消したままにする。それ以外の失敗
        // (ネットワーク・5xx 等)はフォームを復元して再送できるようにする。
        currentPrompt.value = error?.status === 409 ? null : previousPrompt
        const target = messages.value.findLast((value) => value.sender === 'ai')
        if (target) {
          target.error = error?.status === 409
            ? 'この質問への回答受付は終了しました。もう一度お試しください。'
            : (error?.message || '回答を送信できませんでした。')
        }
      },
    })
    isAnswering.value = false
    return ok
  }

  /**
   * ライブな SSE ストリームが無い状態(リロード後等)で回答したときの
   * フォールバック。GET /thread を「新しいターンの決着がつくまで」
   * ポーリングし、届いたらセッション全体を取り直す(chat_sse.md §1.5)。
   * ポーリング中に新しい質問(pending)が現れたら、ターンの完了を待たずに
   * それを表示する(2026-08-04、レビュー是正・裁定15c: 例 Q1 回答 → Q2)。
   */
  async function waitForTurnToFinishThenRefresh() {
    const messageCountBefore = messages.value.filter((value) => value.serverId != null).length
    isLoading.value = true
    try {
      await pollUntilTurnSettles({
        getThread,
        messageCountBefore,
        maxAttempts: ANSWER_POLL_MAX_ATTEMPTS,
        intervalMs: ANSWER_POLL_INTERVAL_MS,
        onPending: async (session) => {
          await applySessionSnapshot(session)
        },
        onSettled: async (session) => {
          await applySessionSnapshot(session)
        },
        onPollError: async (error) => {
          console.error('[ChatStore] Failed to poll thread after answer:', error)
        },
      })
    } finally {
      isLoading.value = false
    }
  }

  async function selectPromptOption(option) {
    if (isAnswering.value) return false
    const prompt = currentPrompt.value
    if (!prompt) return false

    const payload = buildChipAnswerPayload(prompt, option)
    if (!payload) return false
    return sendAnswer(payload.answer, payload.resolves)
  }

  async function undoItinerary(messageId) {
    if (isUndoing.value) return false
    const message = messages.value.find((value) => value.id === messageId)
    const state = message?.itinerary || navStore.currentItinerary
    const version = Number(state?.version)
    if (!Number.isInteger(version) || version < 1) return false

    isUndoing.value = true
    if (message) message.undoError = ''
    try {
      const restored = normalizeItineraryState(await requestItineraryUndo(version))
      const target = message || messages.value.findLast((value) => value.sender === 'ai')
      if (target) applyState(restored, target)
      return true
    } catch (error) {
      console.error('[ChatStore] Failed to undo itinerary:', error)
      if (message) {
        message.undoError = error?.status === 409
          ? '旅程が更新されています。最新の内容を確認してください。'
          : '旅程を元に戻せませんでした。'
      }
      return false
    } finally {
      isUndoing.value = false
    }
  }

  function clearChat() {
    stopStreaming()
    messages.value = []
    profile.value = null
    currentPrompt.value = null
    isLoading.value = false
    isSessionLoaded.value = false
    navStore.reset()
  }

  /**
   * `GET /thread` の応答をそのまま画面状態へ反映する(chat_sse.md §3.1:
   * これ 1 回で画面が完全に戻る)。`rehydrateSession`(初回復元)と
   * `waitForTurnToFinishThenRefresh`(回答後、ライブなストリームが無いとき
   * の取り直し)の両方から使う共通処理。
   */
  async function applySessionSnapshot(session) {
    const restored = (session?.messages || []).map((message) => ({
      id: `server-${message.id}`,
      serverId: message.id,
      content: message.content,
      sender: message.role === 'assistant' ? 'ai' : 'user',
      timestamp: new Date(message.created_at),
      isPending: false,
      statusText: '',
      notices: [],
      error: '',
      candidates: message.role === 'assistant' ? restoredCandidates(message.meta) : null,
      itinerary: null,
      profile: null,
      meta: message.meta || {},
    }))

    profile.value = session?.profile || null

    if (session?.itinerary) {
      const itinerary = normalizeItineraryState(session.itinerary)
      const target = [...restored].reverse().find((message) => (
        message.sender === 'ai'
        && message.meta?.itinerary_version === itinerary.version
      )) || [...restored].reverse().find((message) => message.sender === 'ai')
      const itineraryMessage = target || {
        id: clientId(),
        content: '',
        sender: 'ai',
        timestamp: new Date(),
        isPending: false,
        statusText: '',
        notices: [],
        error: '',
        candidates: null,
        profile: null,
      }
      if (!target) restored.push(itineraryMessage)
      itineraryMessage.itinerary = itinerary
      await navStore.applyItineraryState(itinerary)
    }

    // `pending` が唯一の真実(§1.4)。生きた待機が無ければ null が返る。
    currentPrompt.value = normalizePending(session?.pending)
    messages.value = restored
  }

  async function rehydrateSession() {
    if (!userStore.isLoggedIn || isSessionLoaded.value) return

    isLoading.value = true
    try {
      const session = await getThread()
      await applySessionSnapshot(session)
    } catch (error) {
      console.error('[ChatStore] Failed to rehydrate session:', error)
      messages.value = []
      currentPrompt.value = null
    } finally {
      isLoading.value = false
      isSessionLoaded.value = true
    }
  }

  return {
    messages,
    isLoading,
    isSessionLoaded,
    profile,
    currentPrompt,
    isUndoing,
    isAnswering,
    sendMessage,
    sendAnswer,
    stopStreaming,
    selectPromptOption,
    undoItinerary,
    clearChat,
    rehydrateSession,
  }
})
