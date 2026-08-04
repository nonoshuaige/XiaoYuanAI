<script setup lang="ts">
import {
  PhChatCircleDots as ChatCircleDots,
  PhList as List,
  PhPencilSimple as PencilSimple,
  PhPlus as Plus,
  PhTrash as Trash,
  PhX as X,
} from '@phosphor-icons/vue'
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import AppBrand from '@/components/AppBrand.vue'
import BaseDialog from '@/components/BaseDialog.vue'
import BookingDraftCard from '@/components/BookingDraftCard.vue'
import ContextWindowPicker from '@/components/ContextWindowPicker.vue'
import CurrentUserSwitcher from '@/components/CurrentUserSwitcher.vue'
import MarkdownContent from '@/components/MarkdownContent.vue'
import ModelPicker from '@/components/ModelPicker.vue'
import { CONTEXT_WINDOW_OPTIONS, useChatStore } from '@/stores/chat'
import type { UiMessage } from '@/stores/chat'
import type { SessionSummary } from '@/types/api'

const store = useChatStore()
const composer = ref('')
const messageViewport = ref<HTMLElement | null>(null)
const sidebarOpen = ref(false)
const renameId = ref<string | null>(null)
const renameValue = ref('')
const renameError = ref('')
const deleteTarget = ref<SessionSummary | null>(null)
const deleting = ref(false)
const deleteError = ref('')
const latestMessageRenderState = computed(() => {
  const message = store.messages.at(-1)
  if (!message) return ''
  return [
    message.key,
    message.content,
    message.status,
    message.activities
      .map((activity) => `${activity.id}:${activity.status}:${activity.label}`)
      .join('\u0000'),
    message.quickReplies.join('\u0000'),
    message.artifacts.map((artifact) => `${artifact.draftId}:${artifact.status}`).join('\u0000'),
  ].join('\u0001')
})

watch(latestMessageRenderState, async () => {
  await nextTick()
  if (messageViewport.value) {
    messageViewport.value.scrollTop = messageViewport.value.scrollHeight
  }
})

function openRename(session: SessionSummary) {
  renameId.value = session.sessionId
  renameValue.value = session.title
  renameError.value = ''
}

async function finishRename(save: boolean) {
  if (!renameId.value) return
  const id = renameId.value
  if (!save) {
    renameId.value = null
    return
  }
  try {
    await store.renameSession(id, renameValue.value)
    renameId.value = null
  } catch (error) {
    renameError.value = error instanceof Error ? error.message : '重命名失败'
  }
}

function openDelete(session: SessionSummary) {
  deleteTarget.value = session
  deleteError.value = ''
}

async function confirmDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  deleteError.value = ''
  try {
    await store.deleteSession(deleteTarget.value.sessionId)
    deleteTarget.value = null
  } catch (error) {
    deleteError.value = error instanceof Error ? error.message : '删除失败'
  } finally {
    deleting.value = false
  }
}

async function submit() {
  const text = composer.value.trim()
  if (!text) return
  composer.value = ''
  await store.send(text)
}

function handleComposerKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    void submit()
  }
}

function quickRepliesAreActionable(messageIndex: number) {
  return !store.messages.slice(messageIndex + 1).some((message) => message.role === 'user')
}

const tokenFormatter = new Intl.NumberFormat('zh-CN')

function formatTokens(value: number | null | undefined) {
  return value == null ? '—' : tokenFormatter.format(value)
}

function formatUsageTokens(message: UiMessage, value: number | null | undefined) {
  const formatted = formatTokens(value)
  return message.tokenUsageEstimated && value != null ? `≈${formatted}` : formatted
}

function formatContextWindow(value: number | null | undefined) {
  if (!value) return '—'
  return value >= 1_024 ? `${Math.round(value / 1_024)}K` : String(value)
}

function hasRequestMetrics(message: UiMessage) {
  return (
    message.role === 'assistant' &&
    message.status === 'completed' &&
    (message.contextEstimatedTokens != null || message.inputTokens != null)
  )
}

function requestMetrics(message: UiMessage) {
  const metrics = [
    `输入 ${formatUsageTokens(message, message.inputTokens)}`,
    `输出 ${formatUsageTokens(message, message.outputTokens)}`,
    `合计 ${formatUsageTokens(message, message.totalTokens)}`,
    `上下文估算 ${formatTokens(message.contextEstimatedTokens)}/${formatContextWindow(message.contextWindowTokens)}`,
  ]
  if (message.contextTruncated) {
    metrics.push(`已裁剪 ${message.contextDroppedRounds ?? 0} 轮`)
  }
  return metrics.join(' · ')
}

onMounted(() => void store.initialize())
</script>

<template>
  <div class="chat-app">
    <button
      class="mobile-menu-button"
      type="button"
      aria-label="打开会话列表"
      @click="sidebarOpen = true"
    >
      <List :size="21" aria-hidden="true" />
    </button>

    <aside class="chat-sidebar" :class="{ open: sidebarOpen }">
      <div class="sidebar-brand">
        <AppBrand />
        <button
          class="sidebar-close"
          type="button"
          aria-label="关闭会话列表"
          @click="sidebarOpen = false"
        >
          <X :size="19" aria-hidden="true" />
        </button>
      </div>

      <div class="session-list">
        <button
          class="new-session-button"
          :class="{ active: !store.currentSessionId }"
          type="button"
          :disabled="store.sending"
          @click="store.newConversation"
        >
          <Plus :size="18" weight="bold" aria-hidden="true" />
          <span>
            <strong>新对话</strong>
            <small>第一条消息发送后保存</small>
          </span>
        </button>

        <p v-if="store.sessions.length" class="section-label">最近对话</p>
        <article
          v-for="session in store.sessions"
          :key="session.sessionId"
          class="session-item"
          :class="{ active: session.sessionId === store.currentSessionId }"
        >
          <button
            class="session-main"
            type="button"
            :disabled="store.sending"
            @click="store.loadSession(session.sessionId)"
          >
            <template v-if="renameId === session.sessionId">
              <input
                v-model="renameValue"
                class="session-title-input"
                maxlength="80"
                aria-label="会话名称"
                @click.stop
                @keydown.enter.stop.prevent="finishRename(true)"
                @keydown.esc.stop.prevent="finishRename(false)"
                @blur="finishRename(true)"
              />
              <small v-if="renameError" class="inline-error">{{ renameError }}</small>
            </template>
            <template v-else>
              <strong>{{ session.title }}</strong>
              <small>{{ session.rounds }} 轮对话</small>
            </template>
          </button>
          <div class="session-actions">
            <button type="button" aria-label="重命名会话" @click="openRename(session)">
              <PencilSimple :size="16" aria-hidden="true" />
            </button>
            <button type="button" aria-label="删除会话" @click="openDelete(session)">
              <Trash :size="16" aria-hidden="true" />
            </button>
          </div>
        </article>
      </div>
      <CurrentUserSwitcher />
    </aside>

    <button
      v-if="sidebarOpen"
      class="sidebar-scrim"
      type="button"
      aria-label="关闭会话列表"
      @click="sidebarOpen = false"
    ></button>

    <main class="chat-main">
      <header class="chat-header">
        <div>
          <span class="eyebrow">CURRENT CONVERSATION</span>
          <h1>{{ store.title }}</h1>
        </div>
        <div class="chat-header-controls">
          <ContextWindowPicker
            :options="CONTEXT_WINDOW_OPTIONS"
            :selected="store.selectedContextWindowTokens"
            :disabled="store.sending || store.waitingForAssistant"
            @select="store.selectContextWindow"
          />
          <ModelPicker
            :models="store.models"
            :selected-id="store.selectedModelId"
            :disabled="store.sending || !store.models.length"
            @select="store.selectModel"
          />
        </div>
      </header>

      <section ref="messageViewport" class="message-viewport" aria-live="polite">
        <div v-if="store.loading" class="chat-welcome loading-welcome">
          <span class="thinking-mark" aria-hidden="true"></span>
          正在准备工作台
        </div>
        <div v-else-if="store.error" class="chat-welcome error-welcome">
          <strong>工作台加载失败</strong>
          <span>{{ store.error }}</span>
          <button class="secondary-button" type="button" @click="store.initialize">重新加载</button>
        </div>
        <div v-else-if="!store.messages.length" class="chat-welcome">
          <ChatCircleDots :size="34" weight="duotone" aria-hidden="true" />
          <strong>{{ store.currentSessionId ? '继续这段对话' : '你好，我是小原' }}</strong>
          <span>
            {{
              store.currentSessionId
                ? '上下文已从数据库恢复，可以继续输入。'
                : '发送第一条消息后，这个对话才会被保存。'
            }}
          </span>
        </div>

        <article
          v-for="(message, messageIndex) in store.messages"
          :key="message.key"
          class="message-row"
          :class="[message.role, { failed: message.status === 'failed' }]"
        >
          <div class="message-stack">
            <div class="message-bubble" :class="{ 'artifact-only': message.artifacts.length }">
              <template v-if="message.role === 'assistant'">
                <div
                  v-if="message.activities.length"
                  class="agent-activities"
                  aria-label="Agent 执行进度"
                >
                  <div
                    v-for="activity in message.activities"
                    :key="activity.id"
                    class="agent-activity"
                    :class="activity.status"
                  >
                    <span class="agent-activity-mark" aria-hidden="true"></span>
                    <span>{{ activity.label }}</span>
                  </div>
                </div>
                <MarkdownContent
                  v-if="!message.artifacts.length && message.content.trim()"
                  :content="message.content"
                />
                <p
                  v-else-if="message.content.trim().startsWith('此前还有')"
                  class="artifact-notice"
                >
                  {{ message.content }}
                </p>
                <BookingDraftCard
                  v-for="draft in message.artifacts"
                  :key="draft.draftId"
                  :draft="draft"
                  @updated="store.updateArtifact(message.key, $event)"
                />
                <div
                  v-if="
                    message.quickReplies.length &&
                    !message.artifacts.length &&
                    quickRepliesAreActionable(messageIndex)
                  "
                  class="quick-replies"
                  aria-label="快捷回答"
                >
                  <button
                    v-for="reply in message.quickReplies"
                    :key="reply"
                    type="button"
                    :disabled="store.sending"
                    @click="store.send(reply)"
                  >
                    {{ reply }}
                  </button>
                </div>
              </template>
              <template v-else>{{ message.content }}</template>
            </div>
            <small v-if="hasRequestMetrics(message)" class="message-request-metrics">
              {{ requestMetrics(message) }}
            </small>
          </div>
        </article>
      </section>

      <form class="composer" @submit.prevent="submit">
        <textarea
          v-model="composer"
          rows="1"
          maxlength="8000"
          :placeholder="
            store.waitingForAssistant ? '正在后台生成，可切换会话或稍后回来' : '给小原发消息'
          "
          aria-label="消息"
          :disabled="store.loading || store.sending || store.waitingForAssistant"
          @keydown="handleComposerKeydown"
        ></textarea>
        <button
          type="submit"
          :disabled="store.sending || store.waitingForAssistant || !composer.trim()"
        >
          {{ store.sending ? '提交中' : store.waitingForAssistant ? '生成中' : '发送' }}
        </button>
        <small>Enter 发送 · Shift + Enter 换行</small>
      </form>
    </main>
  </div>

  <BaseDialog
    :open="Boolean(deleteTarget)"
    title="删除这个会话？"
    description="删除后，对话消息、摘要和模型调试记录会一并移除。"
    width="small"
    :busy="deleting"
    @close="deleteTarget = null"
  >
    <p class="delete-summary">
      即将删除 <strong>{{ deleteTarget?.title }}</strong>
    </p>
    <p v-if="deleteError" class="form-error">{{ deleteError }}</p>
    <div class="dialog-actions">
      <button
        class="secondary-button"
        type="button"
        :disabled="deleting"
        @click="deleteTarget = null"
      >
        取消
      </button>
      <button class="danger-button" type="button" :disabled="deleting" @click="confirmDelete">
        <Trash :size="17" aria-hidden="true" />
        {{ deleting ? '删除中' : '删除会话' }}
      </button>
    </div>
  </BaseDialog>
</template>
