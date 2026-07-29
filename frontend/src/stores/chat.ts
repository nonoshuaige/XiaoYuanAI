import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api } from '@/api'
import type { BookingDraft, ChatMessage, ModelOption, SessionSummary } from '@/types/api'

const CURRENT_SESSION_KEY = 'xiaoyuan-current-session'
const SELECTED_MODEL_KEY = 'xiaoyuan-selected-model'

export interface UiMessage extends ChatMessage {
  key: string
  artifacts: BookingDraft[]
  transient?: boolean
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

  const selectedModel = computed(
    () => models.value.find((model) => model.id === selectedModelId.value) ?? null,
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
    currentSessionId.value = null
    localStorage.removeItem(CURRENT_SESSION_KEY)
    title.value = '新对话'
    messages.value = []
  }

  async function refreshSessions() {
    sessions.value = await api.sessions()
  }

  async function loadSession(id: string) {
    if (sending.value) return
    currentSessionId.value = id
    localStorage.setItem(CURRENT_SESSION_KEY, id)
    const context = await api.session(id)
    title.value = context.title
    messages.value = context.messages.map((message) => ({
      ...message,
      key: `${id}-${message.round}-${message.role}`,
      artifacts:
        message.role === 'assistant' ? (context.artifactsByRound[String(message.round)] ?? []) : [],
      quickReplies: message.quickReplies ?? [],
    }))
  }

  async function send(content: string) {
    const text = content.trim()
    if (!text || sending.value) return
    sending.value = true
    error.value = ''
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
      }
      if (currentSessionId.value) payload.sessionId = currentSessionId.value
      const result = await api.chat(payload)
      currentSessionId.value = result.sessionId
      localStorage.setItem(CURRENT_SESSION_KEY, result.sessionId)
      title.value = result.title
      pending.key = `${result.sessionId}-${result.round}-assistant`
      pending.round = result.round
      pending.content = result.reply
      pending.status = 'completed'
      pending.artifacts = result.artifacts ?? []
      pending.quickReplies = result.quickReplies ?? []
      pending.transient = false
      const optimisticUser = messages.value.find(
        (message) => message.key === `optimistic-user-${optimisticRound}`,
      )
      if (optimisticUser) {
        optimisticUser.key = `${result.sessionId}-${result.round}-user`
        optimisticUser.round = result.round
        optimisticUser.status = 'completed'
        optimisticUser.transient = false
      }
      await refreshSessions()
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : '发送失败'
      pending.content = `出错了：${message}`
      pending.status = 'failed'
      pending.error = message
      pending.transient = false
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
