import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api } from '@/api'
import type {
  BookingDraft,
  ChatMessage,
  ContextWindowOption,
  ModelOption,
  SessionContext,
  SessionSummary,
} from '@/types/api'

const CURRENT_SESSION_KEY = 'xiaoyuan-current-session'
const SELECTED_MODEL_KEY = 'xiaoyuan-selected-model'
const SELECTED_CONTEXT_WINDOW_KEY = 'xiaoyuan-context-window'
const CHAT_POLL_INTERVAL_MS = 1_200

export const CONTEXT_WINDOW_OPTIONS: ContextWindowOption[] = [
  { value: 8_192, label: '8K', description: '短对话验证' },
  { value: 16_384, label: '16K', description: '日常推荐' },
  { value: 32_768, label: '32K', description: '长对话' },
  { value: 65_536, label: '64K', description: '超长上下文' },
]
const DEFAULT_CONTEXT_WINDOW = 16_384

function storedContextWindow() {
  const value = Number(localStorage.getItem(SELECTED_CONTEXT_WINDOW_KEY))
  return CONTEXT_WINDOW_OPTIONS.some((option) => option.value === value)
    ? value
    : DEFAULT_CONTEXT_WINDOW
}

export interface UiMessage extends ChatMessage {
  key: string
  artifacts: BookingDraft[]
  activities: AgentActivity[]
  transient?: boolean
}

export interface AgentActivity {
  id: string
  name: string
  label: string
  status: 'running' | 'completed' | 'failed'
}

function createSessionId() {
  return crypto.randomUUID().replaceAll('-', '').slice(0, 16)
}

function failedAssistantContent(error: string | null) {
  if (error === '用户取消本轮并重试') return '已取消本轮，已重新提交。'
  if (error?.startsWith('模型响应超时')) return error
  return `生成失败：${error || '模型请求失败'}`
}

export function contextMessages(context: SessionContext): UiMessage[] {
  const assistantRounds = new Set(
    context.messages
      .filter((message) => message.role === 'assistant')
      .map((message) => message.round),
  )
  return context.messages.flatMap((message) => {
    const current: UiMessage = {
      ...message,
      key: `${context.sessionId}-${message.round}-${message.role}`,
      artifacts:
        message.role === 'assistant' ? (context.artifactsByRound[String(message.round)] ?? []) : [],
      activities: [],
    }
    if (
      message.role !== 'user' ||
      assistantRounds.has(message.round) ||
      !['pending', 'failed'].includes(message.status)
    ) {
      return [current]
    }
    return [
      current,
      {
        key: `${context.sessionId}-${message.round}-assistant`,
        round: message.round,
        role: 'assistant' as const,
        content: message.status === 'pending' ? '' : failedAssistantContent(message.error),
        created_at: message.created_at,
        status: message.status,
        error: message.error,
        artifacts: [],
        activities:
          message.status === 'pending'
            ? [
                {
                  id: 'agent-status',
                  name: 'agent',
                  label: '正在连接实时进度',
                  status: 'running' as const,
                },
              ]
            : [],
      },
    ]
  })
}

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<SessionSummary[]>([])
  const models = ref<ModelOption[]>([])
  const messages = ref<UiMessage[]>([])
  const currentSessionId = ref<string | null>(localStorage.getItem(CURRENT_SESSION_KEY))
  const selectedModelId = ref<string | null>(localStorage.getItem(SELECTED_MODEL_KEY))
  const selectedContextWindowTokens = ref(storedContextWindow())
  const title = ref('新对话')
  const loading = ref(true)
  const sending = ref(false)
  const error = ref('')
  const retryingRound = ref<number | null>(null)
  const retryError = ref('')
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let eventSource: EventSource | null = null
  let eventSourceKey = ''
  let streamFallbackTimer: ReturnType<typeof setTimeout> | null = null
  let sessionLoadVersion = 0

  const selectedModel = computed(
    () => models.value.find((model) => model.id === selectedModelId.value) ?? null,
  )
  const waitingForAssistant = computed(() =>
    messages.value.some((message) => message.role === 'assistant' && message.status === 'pending'),
  )

  async function initialize() {
    loading.value = true
    error.value = ''
    try {
      const [modelCatalog, sessionCatalog] = await Promise.all([api.models(), api.sessions()])
      models.value = modelCatalog.filter((model) => model.callable)
      sessions.value = sessionCatalog

      if (!models.value.length) throw new Error('没有已配置且可调用的模型')
      const preferred =
        models.value.find((model) => model.id === selectedModelId.value) ??
        models.value.find((model) => model.default) ??
        models.value[0]
      selectModel(preferred!.id)

      const savedSession = sessions.value.find(
        (session) => session.sessionId === currentSessionId.value,
      )
      if (savedSession) await loadSession(savedSession.sessionId)
      else newConversation()
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : '初始化失败'
    } finally {
      loading.value = false
    }
  }

  function selectModel(id: string) {
    selectedModelId.value = id
    localStorage.setItem(SELECTED_MODEL_KEY, id)
  }

  function selectContextWindow(tokens: number) {
    if (!CONTEXT_WINDOW_OPTIONS.some((option) => option.value === tokens)) return
    selectedContextWindowTokens.value = tokens
    localStorage.setItem(SELECTED_CONTEXT_WINDOW_KEY, String(tokens))
  }

  function newConversation() {
    if (sending.value) return
    stopStreaming()
    stopPolling()
    sessionLoadVersion += 1
    currentSessionId.value = null
    localStorage.removeItem(CURRENT_SESSION_KEY)
    title.value = '新对话'
    messages.value = []
    retryError.value = ''
  }

  async function refreshSessions() {
    sessions.value = await api.sessions()
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
  }

  function stopStreaming() {
    if (eventSource) {
      eventSource.close()
      eventSource = null
    }
    eventSourceKey = ''
    if (streamFallbackTimer !== null) {
      clearTimeout(streamFallbackTimer)
      streamFallbackTimer = null
    }
  }

  function eventPayload(event: Event): Record<string, unknown> {
    try {
      return JSON.parse((event as MessageEvent<string>).data) as Record<string, unknown>
    } catch {
      return {}
    }
  }

  function pendingAssistant(sessionId: string, round: number) {
    return messages.value.find(
      (message) =>
        message.key === `${sessionId}-${round}-assistant` &&
        message.role === 'assistant' &&
        message.status === 'pending',
    )
  }

  function upsertActivity(message: UiMessage, activity: AgentActivity) {
    const index = message.activities.findIndex((item) => item.id === activity.id)
    if (index >= 0) message.activities[index] = activity
    else message.activities.push(activity)
  }

  async function finishStream(sessionId: string) {
    if (currentSessionId.value !== sessionId) return
    try {
      const context = await api.session(sessionId)
      if (currentSessionId.value !== sessionId) return
      applyContext(context)
      await refreshSessions()
    } catch (caught) {
      if (currentSessionId.value === sessionId) {
        error.value = caught instanceof Error ? caught.message : '刷新最终回复失败'
      }
    }
  }

  function startStreaming(sessionId: string, round: number, eventsUrl?: string) {
    if (currentSessionId.value !== sessionId) return
    const key = `${sessionId}:${round}`
    if (eventSource && eventSourceKey === key) return
    stopStreaming()
    stopPolling()
    const source = new EventSource(
      eventsUrl ?? `/api/sessions/${encodeURIComponent(sessionId)}/rounds/${round}/events`,
    )
    eventSource = source
    eventSourceKey = key

    source.addEventListener('reset', (event) => {
      const message = pendingAssistant(sessionId, round)
      if (!message) return
      const payload = eventPayload(event)
      message.content = ''
      message.activities = []
      const label = typeof payload.label === 'string' ? payload.label : ''
      if (label) {
        upsertActivity(message, {
          id: 'agent-status',
          name: 'agent',
          label,
          status: 'running',
        })
      }
    })
    source.addEventListener('status', (event) => {
      const message = pendingAssistant(sessionId, round)
      if (!message) return
      const payload = eventPayload(event)
      upsertActivity(message, {
        id: 'agent-status',
        name: 'agent',
        label: typeof payload.label === 'string' ? payload.label : '正在处理',
        status: 'running',
      })
    })
    source.addEventListener('text_delta', (event) => {
      const message = pendingAssistant(sessionId, round)
      if (!message) return
      const payload = eventPayload(event)
      if (typeof payload.delta !== 'string') return
      message.activities = message.activities.filter((activity) => activity.id !== 'agent-status')
      message.content += payload.delta
    })
    source.addEventListener('tool_start', (event) => {
      const message = pendingAssistant(sessionId, round)
      if (!message) return
      const payload = eventPayload(event)
      const callId = typeof payload.callId === 'string' ? payload.callId : crypto.randomUUID()
      upsertActivity(message, {
        id: callId,
        name: typeof payload.name === 'string' ? payload.name : 'tool',
        label: typeof payload.label === 'string' ? payload.label : '正在调用工具',
        status: 'running',
      })
    })
    source.addEventListener('tool_end', (event) => {
      const message = pendingAssistant(sessionId, round)
      if (!message) return
      const payload = eventPayload(event)
      const callId = typeof payload.callId === 'string' ? payload.callId : 'tool'
      upsertActivity(message, {
        id: callId,
        name: typeof payload.name === 'string' ? payload.name : 'tool',
        label: typeof payload.label === 'string' ? payload.label : '工具调用完成',
        status: payload.status === 'failed' ? 'failed' : 'completed',
      })
    })
    source.addEventListener('completed', (event) => {
      const message = pendingAssistant(sessionId, round)
      const payload = eventPayload(event)
      if (message) {
        message.content = typeof payload.content === 'string' ? payload.content : message.content
        message.status = 'completed'
        message.activities = []
      }
      if (eventSource === source) stopStreaming()
      void finishStream(sessionId)
    })
    source.addEventListener('failed', (event) => {
      const message = pendingAssistant(sessionId, round)
      const payload = eventPayload(event)
      if (message) {
        message.content =
          typeof payload.message === 'string' ? payload.message : '生成失败，请稍后重试'
        message.status = 'failed'
        message.activities = []
      }
      if (eventSource === source) stopStreaming()
      void refreshSessions()
    })
    source.onopen = () => {
      if (eventSource !== source) return
      stopPolling()
      if (streamFallbackTimer !== null) {
        clearTimeout(streamFallbackTimer)
        streamFallbackTimer = null
      }
    }
    source.onerror = () => {
      if (eventSource !== source || streamFallbackTimer !== null) return
      streamFallbackTimer = setTimeout(() => {
        streamFallbackTimer = null
        if (
          eventSource === source &&
          source.readyState !== EventSource.OPEN &&
          currentSessionId.value === sessionId
        ) {
          schedulePolling(sessionId)
        }
      }, 3_000)
    }
  }

  function applyContext(context: SessionContext) {
    title.value = context.title
    messages.value = contextMessages(context)
  }

  function schedulePolling(sessionId: string) {
    stopPolling()
    if (
      currentSessionId.value !== sessionId ||
      !messages.value.some((message) => message.status === 'pending')
    ) {
      return
    }
    pollTimer = setTimeout(async () => {
      pollTimer = null
      if (currentSessionId.value !== sessionId) return
      try {
        const context = await api.session(sessionId)
        if (currentSessionId.value !== sessionId) return
        applyContext(context)
        const pending = messages.value.find(
          (message) => message.role === 'assistant' && message.status === 'pending',
        )
        if (!pending) {
          stopStreaming()
          await refreshSessions()
        } else if (!eventSource) {
          startStreaming(sessionId, pending.round)
        }
      } catch (caught) {
        if (currentSessionId.value === sessionId) {
          error.value = caught instanceof Error ? caught.message : '刷新生成状态失败'
        }
      } finally {
        if (currentSessionId.value === sessionId) {
          schedulePolling(sessionId)
        }
      }
    }, CHAT_POLL_INTERVAL_MS)
  }

  async function loadSession(id: string) {
    if (sending.value) return
    const loadVersion = ++sessionLoadVersion
    stopStreaming()
    stopPolling()
    currentSessionId.value = id
    retryError.value = ''
    localStorage.setItem(CURRENT_SESSION_KEY, id)
    const context = await api.session(id)
    if (loadVersion !== sessionLoadVersion || currentSessionId.value !== id) return
    applyContext(context)
    const pending = messages.value.find(
      (message) => message.role === 'assistant' && message.status === 'pending',
    )
    if (pending) startStreaming(id, pending.round)
  }

  async function send(content: string) {
    const text = content.trim()
    if (!text || sending.value || waitingForAssistant.value) return
    sending.value = true
    error.value = ''
    const targetSessionId = currentSessionId.value ?? createSessionId()
    currentSessionId.value = targetSessionId
    localStorage.setItem(CURRENT_SESSION_KEY, targetSessionId)
    const optimisticRound = Date.now()
    messages.value.push({
      key: `optimistic-user-${optimisticRound}`,
      round: optimisticRound,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
      status: 'pending',
      error: null,
      artifacts: [],
      activities: [],
      transient: true,
    })
    const pending: UiMessage = {
      key: `optimistic-assistant-${optimisticRound}`,
      round: optimisticRound,
      role: 'assistant',
      content: '',
      created_at: new Date().toISOString(),
      status: 'pending',
      error: null,
      artifacts: [],
      activities: [
        {
          id: 'agent-status',
          name: 'agent',
          label: '正在提交请求',
          status: 'running',
        },
      ],
      transient: true,
    }
    messages.value.push(pending)

    try {
      const payload: {
        message: string
        model: string | null
        contextWindowTokens: number
        sessionId?: string
      } = {
        message: text,
        model: selectedModelId.value,
        contextWindowTokens: selectedContextWindowTokens.value,
        sessionId: targetSessionId,
      }
      const result = await api.chat(payload)
      currentSessionId.value = result.sessionId
      localStorage.setItem(CURRENT_SESSION_KEY, result.sessionId)
      title.value = result.title
      pending.key = `${result.sessionId}-${result.round}-assistant`
      pending.round = result.round
      pending.content = ''
      pending.status = 'pending'
      pending.artifacts = []
      pending.activities = [
        {
          id: 'agent-status',
          name: 'agent',
          label: '请求已进入后台队列',
          status: 'running',
        },
      ]
      pending.transient = false
      const optimisticUser = messages.value.find(
        (message) => message.key === `optimistic-user-${optimisticRound}`,
      )
      if (optimisticUser) {
        optimisticUser.key = `${result.sessionId}-${result.round}-user`
        optimisticUser.round = result.round
        optimisticUser.status = 'pending'
        optimisticUser.transient = false
      }
      await refreshSessions()
      startStreaming(result.sessionId, result.round, result.eventsUrl)
    } catch (caught) {
      try {
        const context = await api.session(targetSessionId)
        if (currentSessionId.value === targetSessionId) {
          applyContext(context)
          const recovered = messages.value.find(
            (message) => message.role === 'assistant' && message.status === 'pending',
          )
          if (recovered) startStreaming(targetSessionId, recovered.round)
        }
      } catch {
        const message = caught instanceof Error ? caught.message : '发送失败'
        pending.content = `出错了：${message}`
        pending.status = 'failed'
        pending.error = message
        pending.activities = []
        pending.transient = false
      }
    } finally {
      sending.value = false
    }
  }

  async function retryRound(round: number) {
    const sessionId = currentSessionId.value
    if (!sessionId || retryingRound.value !== null) return
    retryingRound.value = round
    retryError.value = ''
    stopStreaming()
    stopPolling()
    try {
      const result = await api.retryRound(sessionId, round)
      const context = await api.session(sessionId)
      if (currentSessionId.value !== sessionId) return
      applyContext(context)
      await refreshSessions()
      startStreaming(sessionId, result.round, result.eventsUrl)
    } catch (caught) {
      retryError.value = caught instanceof Error ? caught.message : '重试失败，请稍后再试'
      try {
        const context = await api.session(sessionId)
        if (currentSessionId.value === sessionId) {
          applyContext(context)
          const pending = messages.value.find(
            (message) => message.role === 'assistant' && message.status === 'pending',
          )
          if (pending) startStreaming(sessionId, pending.round)
        }
      } catch {
        // Keep the actionable retry error visible when state refresh also fails.
      }
    } finally {
      retryingRound.value = null
    }
  }

  async function renameSession(id: string, newTitle: string) {
    const updated = await api.renameSession(id, newTitle)
    const index = sessions.value.findIndex((session) => session.sessionId === id)
    if (index >= 0) sessions.value[index] = updated
    if (currentSessionId.value === id) title.value = updated.title
  }

  async function deleteSession(id: string) {
    await api.deleteSession(id)
    if (currentSessionId.value === id) newConversation()
    await refreshSessions()
  }

  function updateArtifact(messageKey: string, updated: BookingDraft) {
    const message = messages.value.find((item) => item.key === messageKey)
    if (!message) return
    const index = message.artifacts.findIndex((artifact) => artifact.draftId === updated.draftId)
    if (index >= 0) message.artifacts[index] = updated
  }

  return {
    sessions,
    models,
    messages,
    currentSessionId,
    selectedModelId,
    selectedContextWindowTokens,
    selectedModel,
    waitingForAssistant,
    title,
    loading,
    sending,
    error,
    retryingRound,
    retryError,
    initialize,
    selectModel,
    selectContextWindow,
    newConversation,
    loadSession,
    send,
    retryRound,
    renameSession,
    deleteSession,
    updateArtifact,
  }
})
