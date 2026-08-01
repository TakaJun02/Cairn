// frontend/src/stores/chat.js
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useUserStore } from '@/stores/user'
import { useNavStore } from '@/stores/nav'
import {
  apiUrl,
  bearerHeaders,
  getThread,
  undoItinerary as requestItineraryUndo,
} from '@/lib/api'
import { createSseParser } from '@/lib/sse'

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
  }
}

function normalizePending(pending) {
  if (!pending) return null
  const kind = pending.kind || (pending.surface ? 'clarify' : 'ask_user')
  if (kind === 'clarify') {
    return {
      ...pending,
      kind,
      options: (pending.options || []).map((option) => ({
        label: option?.label || option?.value || String(option),
        value: option?.value || option?.resolves_to?.value || option?.label || String(option),
      })),
    }
  }
  return {
    ...pending,
    kind: 'ask_user',
    options: (pending.options || []).map((option) => String(option)),
  }
}

function restoredCandidates(meta = {}) {
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

  let activeController = null

  const promptStorageKey = () => `chat-prompt:${userStore.userName || 'anonymous'}`

  const rememberPrompt = (prompt) => {
    if (typeof sessionStorage === 'undefined') return
    if (prompt) {
      sessionStorage.setItem(promptStorageKey(), JSON.stringify(prompt))
    } else {
      sessionStorage.removeItem(promptStorageKey())
    }
  }

  const storedPrompt = (restoredMessages) => {
    if (typeof sessionStorage === 'undefined') return null
    try {
      const prompt = normalizePending(JSON.parse(sessionStorage.getItem(promptStorageKey()) || 'null'))
      const lastAssistant = [...restoredMessages].reverse().find((message) => message.sender === 'ai')
      if (
        prompt?.kind === 'ask_user'
        && lastAssistant?.meta?.ask_slot === prompt.slot
      ) return prompt
      if (
        prompt?.kind === 'clarify'
        && lastAssistant?.meta?.clarify_surface === prompt.surface
      ) return prompt
    } catch {
      // 壊れた一時データは無視し、GET /thread の内容を優先する。
    }
    return null
  }

  const clearPrompt = () => {
    if (currentPrompt.value) {
      const message = messages.value.find((value) => value.id === currentPrompt.value.messageId)
      if (message) message.prompt = null
    }
    currentPrompt.value = null
    rememberPrompt(null)
  }

  const setPrompt = (message, prompt) => {
    clearPrompt()
    const normalized = normalizePending(prompt)
    message.prompt = normalized
    currentPrompt.value = normalized
      ? { messageId: message.id, prompt: normalized }
      : null
    rememberPrompt(normalized)
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
        setPrompt(message, state)
        break
      case 'profile':
        profile.value = state.profile
        message.profile = state.profile
        break
      case 'searching':
        message.statusText = state.text
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
        break
      default:
        console.warn('[ChatStore] Unknown SSE event:', event)
    }
  }

  async function sendMessage(userInput, resolves = null) {
    const content = String(userInput || '').trim()
    if (!content || isLoading.value) return false

    clearPrompt()
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
      prompt: null,
      profile: null,
    }
    messages.value.push(aiMessageSeed)
    const aiMessage = messages.value.at(-1)

    const controller = new AbortController()
    activeController = controller
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
        body: JSON.stringify({
          message: content,
          ...(resolves && { resolves }),
        }),
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
      if (activeController === controller) {
        activeController = null
        isLoading.value = false
      }
    }
  }

  function stopStreaming() {
    activeController?.abort()
  }

  async function selectPromptOption(messageId, option) {
    if (isLoading.value) return false
    const message = messages.value.find((value) => value.id === messageId)
    const prompt = message?.prompt
    if (!prompt) return false

    message.prompt = null
    currentPrompt.value = null
    const label = typeof option === 'string' ? option : option?.label
    if (!label) return false
    const resolves = prompt.kind === 'clarify'
      ? { surface: prompt.surface, value: option?.value || label }
      : null
    return sendMessage(label, resolves)
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

  async function rehydrateSession() {
    if (!userStore.isLoggedIn || isSessionLoaded.value) return

    isLoading.value = true
    try {
      const session = await getThread()
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
        prompt: null,
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
          prompt: null,
          profile: null,
        }
        if (!target) restored.push(itineraryMessage)
        itineraryMessage.itinerary = itinerary
        await navStore.applyItineraryState(itinerary)
      }

      const pending = normalizePending(session?.pending) || storedPrompt(restored)
      if (pending) {
        let target = [...restored].reverse().find((message) => message.sender === 'ai')
        if (!target) {
          target = {
            id: clientId(),
            content: '',
            sender: 'ai',
            timestamp: new Date(),
            isPending: false,
            statusText: '',
            notices: [],
            error: '',
            candidates: null,
            itinerary: null,
            profile: null,
          }
          restored.push(target)
        }
        setPrompt(target, pending)
      }

      messages.value = restored
    } catch (error) {
      console.error('[ChatStore] Failed to rehydrate session:', error)
      messages.value = []
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
    sendMessage,
    stopStreaming,
    selectPromptOption,
    undoItinerary,
    clearChat,
    rehydrateSession,
  }
})
