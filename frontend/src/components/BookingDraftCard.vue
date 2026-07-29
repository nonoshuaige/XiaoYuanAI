<script setup lang="ts">
import {
  PhArrowsClockwise as ArrowsClockwise,
  PhCalendarBlank as CalendarBlank,
  PhCheckCircle as CheckCircle,
  PhXCircle as XCircle,
  PhWarningCircle as WarningCircle,
} from '@phosphor-icons/vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { api } from '@/api'
import type { BookingDraft, BookingDraftPayload, MeetingRoom } from '@/types/api'
import { shanghaiToday } from '@/utils/date'

const props = defineProps<{
  draft: BookingDraft
}>()

const emit = defineEmits<{
  updated: [draft: BookingDraft]
}>()

const current = ref<BookingDraft>({ ...props.draft })
const form = reactive({
  floor: props.draft.floor,
  roomId: props.draft.roomId,
  date: props.draft.date.replaceAll('/', '-'),
  start: props.draft.timeRange.split('-')[0] ?? '09:00',
  end: props.draft.timeRange.split('-')[1] ?? '09:30',
  capacity: props.draft.capacity,
  theme: props.draft.theme,
})
const roomOptions = ref<MeetingRoom[]>([])
const feedback = ref('可直接修改参数，然后保存并预约；也可以取消这张草稿。')
const feedbackError = ref(false)
const busy = ref(false)
const refreshing = ref(false)
const secondsRemaining = ref(0)
let timer: number | undefined
let expirySyncing = false

const locked = computed(() => current.value.status !== 'pending')
const statusLabel = computed(
  () =>
    ({
      pending: '等待你确认',
      confirmed: '预约成功',
      expired: '已失效',
      cancelled: '已取消',
    })[current.value.status],
)
const countdown = computed(() => {
  if (current.value.status === 'confirmed') return '已完成确认'
  if (current.value.status === 'expired') return '30 分钟确认期限已结束'
  if (current.value.status !== 'pending') return '确认期限已结束'
  if (secondsRemaining.value <= 0) return '正在同步失效状态'
  const minutes = String(Math.floor(secondsRemaining.value / 60)).padStart(2, '0')
  const seconds = String(secondsRemaining.value % 60).padStart(2, '0')
  return `剩余 ${minutes}:${seconds}`
})

const startTimes = createTimeOptions(false)
const endTimes = createTimeOptions(true)

watch(
  () => props.draft,
  (draft) => applyDraft(draft),
  { deep: true },
)

function createTimeOptions(allowEnd: boolean): string[] {
  const values: string[] = []
  const finalMinute = allowEnd ? 18 * 60 : 17 * 60 + 30
  for (let minute = 9 * 60; minute <= finalMinute; minute += 30) {
    values.push(
      `${String(Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`,
    )
  }
  return values
}

function applyDraft(draft: BookingDraft) {
  current.value = { ...draft }
  form.floor = draft.floor
  form.roomId = draft.roomId
  form.date = draft.date.replaceAll('/', '-')
  ;[form.start, form.end] = draft.timeRange.split('-') as [string, string]
  form.capacity = draft.capacity
  form.theme = draft.theme
  updateCountdown()
}

function payload(): BookingDraftPayload {
  return {
    roomId: form.roomId,
    floor: form.floor,
    date: form.date.replaceAll('-', '/'),
    timeRange: `${form.start}-${form.end}`,
    capacity: Number(form.capacity),
    theme: form.theme.trim() || null,
  }
}

function updateCountdown() {
  if (current.value.status !== 'pending') {
    secondsRemaining.value = 0
    return
  }
  const expiresAt = Date.parse(current.value.expiresAt)
  secondsRemaining.value = Number.isFinite(expiresAt)
    ? Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000))
    : 30 * 60
  if (secondsRemaining.value === 0) void syncExpiry()
}

async function syncExpiry() {
  if (expirySyncing || current.value.status !== 'pending') return
  expirySyncing = true
  try {
    const latest = await api.bookingDraft(current.value.draftId)
    if (latest.status !== 'pending') {
      applyDraft(latest)
      emit('updated', latest)
    }
  } catch (error) {
    feedbackError.value = true
    feedback.value = error instanceof Error ? error.message : '失效状态同步失败'
  } finally {
    expirySyncing = false
  }
}

async function refreshRooms() {
  feedbackError.value = false
  feedback.value = '正在查询当前条件下的可用会议室…'
  refreshing.value = true
  try {
    const result = await api.bookingRoomOptions(current.value.draftId, payload())
    roomOptions.value = result.rooms
    const selected = result.rooms.find((room) => room.roomId === form.roomId && room.available)
    const firstAvailable = result.rooms.find((room) => room.available)
    if (!selected && firstAvailable) form.roomId = firstAvailable.roomId
    feedback.value = firstAvailable
      ? '已按当前条件刷新，请选择可用会议室。'
      : '当前条件下没有可用会议室。'
  } catch (error) {
    feedbackError.value = true
    feedback.value = error instanceof Error ? error.message : '查询会议室失败'
  } finally {
    refreshing.value = false
  }
}

async function save(): Promise<BookingDraft> {
  feedbackError.value = false
  feedback.value = '正在保存并校验预约参数…'
  busy.value = true
  try {
    const updated = await api.updateBookingDraft(current.value.draftId, payload())
    applyDraft(updated)
    emit('updated', updated)
    return updated
  } catch (error) {
    feedbackError.value = true
    feedback.value = error instanceof Error ? error.message : '保存失败，请稍后重试'
    throw error
  } finally {
    busy.value = false
  }
}

async function confirm() {
  try {
    await save()
    busy.value = true
    feedback.value = '正在创建真实预约…'
    const result = await api.confirmBookingDraft(current.value.draftId)
    applyDraft(result.draft)
    emit('updated', result.draft)
    feedback.value = '预约已由服务端真实写入。'
  } catch (error) {
    if (!feedbackError.value) {
      feedbackError.value = true
      feedback.value = error instanceof Error ? error.message : '确认失败，请检查参数'
    }
  } finally {
    busy.value = false
  }
}

async function cancel() {
  feedbackError.value = false
  feedback.value = '正在取消这张预约草稿…'
  busy.value = true
  try {
    const cancelled = await api.cancelBookingDraft(current.value.draftId)
    applyDraft(cancelled)
    emit('updated', cancelled)
    feedback.value = '预约草稿已取消，不会创建真实预约。'
  } catch (error) {
    feedbackError.value = true
    feedback.value = error instanceof Error ? error.message : '取消失败，请稍后重试'
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  updateCountdown()
  timer = window.setInterval(updateCountdown, 1000)
})

onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <section
    class="booking-card"
    :class="{
      'is-confirmed': current.status === 'confirmed',
      'is-expired': current.status === 'expired',
      'is-locked': locked,
    }"
    :data-draft-id="current.draftId"
    aria-label="会议室预约确认卡片"
  >
    <header class="booking-card-head">
      <div class="booking-title">
        <CalendarBlank :size="20" weight="duotone" aria-hidden="true" />
        <span>会议室预约</span>
      </div>
      <div class="booking-state">
        <span class="booking-countdown">{{ countdown }}</span>
        <span class="status-chip" :class="current.status">{{ statusLabel }}</span>
      </div>
    </header>

    <div class="booking-card-body">
      <div class="booking-form-grid">
        <label>
          <span>楼层</span>
          <select v-model="form.floor" :disabled="locked || busy">
            <option v-for="floor in ['6', '7', '8']" :key="floor" :value="floor">
              {{ floor }}F
            </option>
          </select>
        </label>
        <label>
          <span>日期</span>
          <input
            v-model="form.date"
            type="date"
            :min="shanghaiToday()"
            :disabled="locked || busy"
          />
        </label>
        <label class="wide">
          <span>会议室</span>
          <span class="room-control">
            <select v-model="form.roomId" :disabled="locked || busy || refreshing">
              <option v-if="!roomOptions.length" :value="current.roomId">
                {{ current.roomName }}
              </option>
              <option
                v-for="room in roomOptions"
                :key="room.roomId"
                :value="room.roomId"
                :disabled="!room.available"
              >
                {{ room.roomName }} · {{ room.capacity }}人{{ room.available ? '' : ' · 不可用' }}
              </option>
            </select>
            <button
              v-if="!locked"
              class="text-button"
              type="button"
              :disabled="busy || refreshing"
              @click="refreshRooms"
            >
              <ArrowsClockwise :size="16" :class="{ spinning: refreshing }" aria-hidden="true" />
              刷新可用
            </button>
          </span>
        </label>
        <label>
          <span>开始时间</span>
          <select v-model="form.start" :disabled="locked || busy">
            <option v-for="time in startTimes" :key="time" :value="time">{{ time }}</option>
          </select>
        </label>
        <label>
          <span>结束时间</span>
          <select v-model="form.end" :disabled="locked || busy">
            <option v-for="time in endTimes" :key="time" :value="time">{{ time }}</option>
          </select>
        </label>
        <label>
          <span>参会人数</span>
          <input
            v-model.number="form.capacity"
            type="number"
            min="1"
            max="500"
            :disabled="locked || busy"
          />
        </label>
        <label>
          <span>会议主题</span>
          <input v-model="form.theme" maxlength="100" :disabled="locked || busy" />
        </label>
      </div>

      <div v-if="current.status === 'confirmed'" class="booking-proof">
        <CheckCircle :size="18" weight="fill" aria-hidden="true" />
        预约编号 {{ current.meetingId }} · {{ current.roomName }} · {{ current.date }}
        {{ current.timeRange }}
      </div>

      <p class="booking-feedback" :class="{ error: feedbackError }" aria-live="polite">
        <WarningCircle v-if="feedbackError" :size="17" aria-hidden="true" />
        {{ feedback }}
      </p>

      <div v-if="!locked" class="booking-actions">
        <button class="secondary-button" type="button" :disabled="busy" @click="cancel">
          <XCircle :size="17" aria-hidden="true" />
          取消
        </button>
        <button class="primary-button" type="button" :disabled="busy" @click="confirm">
          <CheckCircle :size="18" weight="bold" aria-hidden="true" />
          保存并预约
        </button>
      </div>
    </div>
  </section>
</template>
