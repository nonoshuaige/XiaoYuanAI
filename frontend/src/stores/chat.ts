import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api } from '@/api'
import type {
  BookingDraft,
  ChatMessage,
  ModelOption,
  SessionContext,
  SessionSummary,
} from '@/types/api'

const CURRENT_SESSION_KEY = 'xiaoyuan-current-session'
const SELECTED_MODEL_KEY = 'xiaoyuan-selected-model'
const CHAT_POLL_INTERVAL_MS = 1_200

export interface UiMessage extends ChatMessage {
  key: string
  artifacts: BookingDraft[]
  transient?: boolean
}

function createSessionId() {
  return crypto.randomUUID().replaceAll('-', '').slice(0, 16)
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
      quickReplies: message.quickReplies ?? [],
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
        content:
          message.status === 'pending'
            ? '正在思考…'
            : `生成失败：${message.error || '模型请求失败'}`,
        quickReplies: [],
        created_at: message.created_at,
        status: message.status,
        error: message.error,
        artifacts: [],
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
  const title = ref('新对话')
  const loading = ref(true)
  const sending = ref(false)
  const error = ref('')
  const sandboxEnabled = ref(false)
  let pollTimer: ReturnType<typeof setTimeout> | null = null
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
      const [modelCatalog, sessionCatalog, sandbox] = await Promise.all([
        api.models(),
        api.sessions(),
        api.sandboxStatus().catch(() => null),
      ])
      models.value = modelCatalog.filter((model) => model.callable)
      sessions.value = sessionCatalog
      sandboxEnabled.value = Boolean(sandbox)

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

  function newConversation() {
    if (sending.value) return
    stopPolling()
    sessionLoadVersion += 1
    currentSessionId.value = null
    localStorage.removeItem(CURRENT_SESSION_KEY)
    title.value = '新对话'
    messages.value = []
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
        if (!messages.value.some((message) => message.status === 'pending')) {
          await refreshSessions()
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
    stopPolling()
    currentSessionId.value = id
    localStorage.setItem(CURRENT_SESSION_KEY, id)
    const context = await api.session(id)
    if (loadVersion !== sessionLoadVersion || currentSessionId.value !== id) return
    applyContext(context)
    schedulePolling(id)
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
      quickReplies: [],
      transient: true,
    })
    const pending: UiMessage = {
      key: `optimistic-assistant-${optimisticRound}`,
      round: optimisticRound,
      role: 'assistant',
      content: '正在思考…',
      created_at: new Date().toISOString(),
      status: 'pending',
      error: null,
      artifacts: [],
      quickReplies: [],
      transient: true,
    }
    messages.value.push(pending)

    try {
      const payload: { message: string; model: string | null; sessionId?: string } = {
        message: text,
        model: selectedModelId.value,
        sessionId: targetSessionId,
      }
      const result = await api.chat(payload)
      currentSessionId.value = result.sessionId
      localStorage.setItem(CURRENT_SESSION_KEY, result.sessionId)
      title.value = result.title
      pending.key = `${result.sessionId}-${result.round}-assistant`
      pending.round = result.round
      pending.content = '正在思考…'
      pending.status = 'pending'
      pending.artifacts = []
      pending.quickReplies = []
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
      schedulePolling(result.sessionId)
    } catch (caught) {
      try {
        const context = await api.session(targetSessionId)
        if (currentSessionId.value === targetSessionId) {
          applyContext(context)
          schedulePolling(targetSessionId)
        }
      } catch {
        const message = caught instanceof Error ? caught.message : '发送失败'
        pending.content = `出错了：${message}`
        pending.status = 'failed'
        pending.error = message
        pending.transient = false
      }
    } finally {
      sending.value = false
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
    selectedModel,
    waitingForAssistant,
    title,
    loading,
    sending,
    error,
    sandboxEnabled,
    initialize,
    selectModel,
    newConversation,
    loadSession,
    send,
    renameSession,
    deleteSession,
    updateArtifact,
  }
})
