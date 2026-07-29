<script setup lang="ts">
import {
  PhArrowClockwise as ArrowClockwise,
  PhBuildings as Buildings,
  PhCalendarBlank as CalendarBlank,
  PhCaretLeft as CaretLeft,
  PhCaretRight as CaretRight,
  PhClock as Clock,
  PhUsers as Users,
} from '@phosphor-icons/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '@/api'
import AsyncPanel from '@/components/AsyncPanel.vue'
import BaseDialog from '@/components/BaseDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import type { MeetingRoom, MeetingRoomSchedule, RequestState } from '@/types/api'
import {
  apiDate,
  formatDate,
  formatObservedAt,
  localDateValue,
  shiftDate,
  weekdayName,
} from '@/utils/date'

const selectedDate = ref(localDateValue())
const schedule = ref<MeetingRoomSchedule | null>(null)
const state = ref<RequestState>('idle')
const error = ref('')
const refreshing = ref(false)
const selectedRoom = ref<MeetingRoom | null>(null)
let refreshTimer: number | undefined

const floorGroups = computed(() => {
  const groups = new Map<string, MeetingRoom[]>()
  schedule.value?.rooms.forEach((room) => {
    const items = groups.get(room.floor) ?? []
    items.push(room)
    groups.set(room.floor, items)
  })
  return [...groups.entries()].sort(([first], [second]) => floorNumber(first) - floorNumber(second))
})

const floorCount = computed(() => floorGroups.value.length)
const roomCount = computed(() => schedule.value?.rooms.length ?? 0)

function floorNumber(floor: string): number {
  return Number(floor.replace('F', ''))
}

function timelineLabels(room: MeetingRoom): string[] {
  const slots = room.timeline
  if (!slots.length) return ['18:00']
  const indexes = [0, Math.floor(slots.length / 3), Math.floor((slots.length * 2) / 3)]
  return [
    ...new Set([...indexes.map((index) => slots[index]?.start).filter(Boolean), slots.at(-1)?.end]),
  ] as string[]
}

async function loadSchedule(silent = false) {
  if (refreshing.value) return
  refreshing.value = true
  error.value = ''
  if (!silent) state.value = 'loading'
  try {
    schedule.value = await api.meetingRooms(apiDate(selectedDate.value))
    state.value = schedule.value.rooms.length ? 'success' : 'idle'
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '读取会议室日程失败'
    state.value = 'error'
  } finally {
    refreshing.value = false
  }
}

function changeDay(amount: number) {
  selectedDate.value = shiftDate(selectedDate.value, amount)
  void loadSchedule()
}

function goToday() {
  selectedDate.value = localDateValue()
  void loadSchedule()
}

function handleVisibility() {
  if (document.visibilityState === 'visible' && !selectedRoom.value) void loadSchedule(true)
}

onMounted(() => {
  void loadSchedule()
  document.addEventListener('visibilitychange', handleVisibility)
  refreshTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible' && !selectedRoom.value) void loadSchedule(true)
  }, 30_000)
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', handleVisibility)
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer)
})
</script>

<template>
  <div class="page-shell">
    <PageHeader label="会议室只读沙箱" />
    <main class="workspace meeting-workspace">
      <section class="page-intro meeting-intro">
        <div>
          <span class="eyebrow">MEETING ROOM SCHEDULE</span>
          <h1>选择一天，<br />查看所有会议室。</h1>
          <p>按楼层浏览 09:00–18:00 半小时时段。这里仅查看真实预约状态，不直接创建预约。</p>
        </div>
        <div class="date-navigator" aria-label="选择日程日期">
          <button type="button" aria-label="前一天" @click="changeDay(-1)">
            <CaretLeft :size="18" weight="bold" aria-hidden="true" />
          </button>
          <label>
            <span>日程日期</span>
            <input v-model="selectedDate" type="date" @change="loadSchedule()" />
          </label>
          <button class="today-button" type="button" @click="goToday">今天</button>
          <button type="button" aria-label="后一天" @click="changeDay(1)">
            <CaretRight :size="18" weight="bold" aria-hidden="true" />
          </button>
        </div>
      </section>

      <section class="schedule-summary" aria-live="polite">
        <div class="date-summary">
          <CalendarBlank :size="20" weight="duotone" aria-hidden="true" />
          <span>
            <strong>{{ formatDate(selectedDate) }}</strong>
            <small>
              {{ weekdayName(selectedDate) }}
              <template v-if="schedule">
                · {{ formatObservedAt(schedule.observedAt) }} 更新</template
              >
            </small>
          </span>
        </div>
        <div class="summary-metric">
          <Buildings :size="19" aria-hidden="true" />
          <span
            ><strong>{{ floorCount }}</strong> 层</span
          >
        </div>
        <div class="summary-metric">
          <Users :size="19" aria-hidden="true" />
          <span
            ><strong>{{ roomCount }}</strong> 间</span
          >
        </div>
        <div class="summary-metric">
          <Clock :size="19" aria-hidden="true" />
          <span>{{ schedule?.displayWindow?.replace('-', '–') ?? '—' }}</span>
        </div>
        <button
          class="refresh-button"
          type="button"
          :disabled="refreshing"
          @click="loadSchedule(true)"
        >
          <ArrowClockwise :size="18" :class="{ spinning: refreshing }" aria-hidden="true" />
          刷新
        </button>
      </section>

      <AsyncPanel v-if="state === 'loading'" state="loading" />
      <AsyncPanel
        v-else-if="state === 'error'"
        state="error"
        title="日程读取失败"
        :message="error"
        @retry="loadSchedule()"
      />
      <AsyncPanel
        v-else-if="!schedule?.rooms.length"
        state="empty"
        title="当天没有会议室数据"
        message="请换一个日期后再查看。"
      />
      <section v-else class="floor-list" aria-label="各楼层会议室">
        <section v-for="[floor, rooms] in floorGroups" :key="floor" class="floor-section">
          <header class="floor-heading">
            <div>
              <span>{{ floorNumber(floor) }}</span>
              <div>
                <h2>{{ floor }}</h2>
                <p>会议室</p>
              </div>
            </div>
            <small>{{ rooms.length }} 间</small>
          </header>
          <div class="room-list">
            <button
              v-for="room in rooms"
              :key="room.roomId"
              class="room-row"
              type="button"
              :aria-label="`查看 ${room.roomName} 在 ${formatDate(selectedDate)} 的日程`"
              @click="selectedRoom = room"
            >
              <span class="room-identity">
                <span>
                  <strong>{{ room.roomName }}</strong>
                  <em>{{ room.capacity }}人</em>
                </span>
                <small>{{ room.equipment.join(' · ') }}</small>
              </span>
              <span class="mini-schedule" aria-hidden="true">
                <span class="mini-track">
                  <i
                    v-for="slot in room.timeline"
                    :key="slot.timeRange"
                    :class="{ occupied: !slot.available }"
                  ></i>
                </span>
                <span class="mini-axis">
                  <small v-for="label in timelineLabels(room)" :key="label">{{ label }}</small>
                </span>
              </span>
              <span class="room-availability">
                <strong>
                  {{ room.timeline.filter((slot) => slot.available).length * 0.5 }} 小时可用
                </strong>
                <small>
                  {{
                    room.occupied.length ? `${room.occupied.length} 条预约` : '当前展示时段无预约'
                  }}
                </small>
              </span>
              <CaretRight :size="18" aria-hidden="true" />
            </button>
          </div>
        </section>
      </section>
    </main>
  </div>

  <BaseDialog
    :open="Boolean(selectedRoom)"
    :title="selectedRoom?.roomName ?? '会议室日程'"
    :description="
      selectedRoom
        ? `${selectedRoom.floor} · 容纳 ${selectedRoom.capacity} 人 · ${selectedRoom.equipment.join(' / ')}`
        : ''
    "
    width="large"
    @close="selectedRoom = null"
  >
    <div class="dialog-date-strip">
      <CalendarBlank :size="20" aria-hidden="true" />
      <span>
        <small>当前日程</small>
        <strong>{{ formatDate(selectedDate) }} · {{ weekdayName(selectedDate) }}</strong>
      </span>
      <span class="schedule-legend"> <i></i>可用 <i class="occupied"></i>已预约 </span>
    </div>
    <div class="timeline">
      <div v-for="slot in selectedRoom?.timeline" :key="slot.timeRange" class="timeline-row">
        <time>{{ slot.timeRange }}</time>
        <div :class="{ occupied: !slot.available }">
          <strong>{{ slot.available ? '可用' : slot.booking?.theme || '不可用' }}</strong>
          <span>{{ slot.available ? '暂无预约' : slot.booking?.bookedBy || '已预约' }}</span>
        </div>
      </div>
    </div>
  </BaseDialog>
</template>
