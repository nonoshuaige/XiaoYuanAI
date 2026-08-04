<script setup lang="ts">
import {
  PhArrowClockwise as ArrowClockwise,
  PhCheckCircle as CheckCircle,
  PhIdentificationCard as IdentificationCard,
  PhMagnifyingGlass as MagnifyingGlass,
  PhUserSwitch as UserSwitch,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref, watch } from 'vue'

import { api } from '@/api'
import BaseDialog from '@/components/BaseDialog.vue'
import type { CurrentUser } from '@/types/api'

const currentUser = ref<CurrentUser | null>(null)
const loading = ref(true)
const loadError = ref('')
const dialogOpen = ref(false)
const employeeId = ref('')
const resolvedUser = ref<CurrentUser | null>(null)
const resolving = ref(false)
const switching = ref(false)
const formError = ref('')

const normalizedEmployeeId = computed(() => employeeId.value.trim())
const canConfirm = computed(
  () =>
    Boolean(resolvedUser.value) &&
    resolvedUser.value?.employeeId === normalizedEmployeeId.value &&
    !resolving.value &&
    !switching.value,
)

watch(employeeId, () => {
  if (resolvedUser.value?.employeeId !== normalizedEmployeeId.value) {
    resolvedUser.value = null
  }
  formError.value = ''
})

async function loadCurrentUser() {
  loading.value = true
  loadError.value = ''
  try {
    currentUser.value = await api.currentUser()
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '当前用户加载失败'
  } finally {
    loading.value = false
  }
}

function openDialog() {
  employeeId.value = currentUser.value?.employeeId ?? ''
  resolvedUser.value = currentUser.value
  formError.value = ''
  dialogOpen.value = true
}

function closeDialog() {
  if (resolving.value || switching.value) return
  dialogOpen.value = false
}

async function resolveEmployee() {
  if (!normalizedEmployeeId.value) {
    formError.value = '请输入工号'
    return
  }
  resolving.value = true
  formError.value = ''
  resolvedUser.value = null
  try {
    resolvedUser.value = await api.resolveCurrentUser(normalizedEmployeeId.value)
  } catch (error) {
    formError.value = error instanceof Error ? error.message : '工号查询失败'
  } finally {
    resolving.value = false
  }
}

async function confirmSwitch() {
  if (!canConfirm.value) return
  switching.value = true
  formError.value = ''
  try {
    currentUser.value = await api.switchCurrentUser(normalizedEmployeeId.value)
    dialogOpen.value = false
  } catch (error) {
    resolvedUser.value = null
    formError.value = error instanceof Error ? error.message : '用户切换失败'
  } finally {
    switching.value = false
  }
}

onMounted(() => void loadCurrentUser())
</script>

<template>
  <section class="current-user-panel" aria-live="polite">
    <div v-if="loading" class="current-user-loading" aria-label="正在加载当前用户">
      <span></span>
      <span></span>
    </div>

    <div v-else-if="loadError" class="current-user-error">
      <span>{{ loadError }}</span>
      <button type="button" aria-label="重新加载当前用户" @click="loadCurrentUser">
        <ArrowClockwise :size="17" aria-hidden="true" />
      </button>
    </div>

    <template v-else-if="currentUser">
      <div class="current-user-identity">
        <span class="current-user-icon" aria-hidden="true">
          <IdentificationCard :size="20" weight="duotone" />
        </span>
        <span class="current-user-copy">
          <small>当前用户 · {{ currentUser.employeeId }}</small>
          <strong>{{ currentUser.name }}</strong>
        </span>
      </div>
      <button class="change-user-button" type="button" @click="openDialog">
        <UserSwitch :size="16" aria-hidden="true" />
        更换
      </button>
    </template>
  </section>

  <BaseDialog
    :open="dialogOpen"
    title="更换当前用户"
    description="姓名只能从员工通讯录读取，无法手动修改。"
    width="small"
    :busy="resolving || switching"
    @close="closeDialog"
  >
    <form class="current-user-form" @submit.prevent="resolveEmployee">
      <label>
        <span>工号</span>
        <span class="employee-id-control">
          <input
            v-model="employeeId"
            maxlength="32"
            autocomplete="off"
            inputmode="text"
            placeholder="输入员工工号"
            :disabled="resolving || switching"
          />
          <button
            class="secondary-button"
            type="submit"
            :disabled="resolving || switching || !normalizedEmployeeId"
          >
            <MagnifyingGlass :size="16" aria-hidden="true" />
            {{ resolving ? '查询中' : '查询姓名' }}
          </button>
        </span>
      </label>

      <label>
        <span>姓名</span>
        <span class="readonly-name" :class="{ resolved: resolvedUser }">
          <input
            :value="resolvedUser?.name ?? ''"
            readonly
            tabindex="-1"
            placeholder="查询工号后自动显示"
            aria-describedby="name-source-note"
          />
          <CheckCircle v-if="resolvedUser" :size="18" weight="fill" aria-hidden="true" />
        </span>
        <small id="name-source-note">数据来源：person 员工通讯录</small>
      </label>

      <p v-if="formError" class="current-user-form-error" role="alert">{{ formError }}</p>

      <div class="current-user-actions">
        <button class="secondary-button" type="button" :disabled="switching" @click="closeDialog">
          取消
        </button>
        <button class="primary-button" type="button" :disabled="!canConfirm" @click="confirmSwitch">
          <UserSwitch :size="16" aria-hidden="true" />
          {{ switching ? '更换中' : '确认更换' }}
        </button>
      </div>
    </form>
  </BaseDialog>
</template>

<style scoped>
.current-user-panel {
  min-height: 74px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-top: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.56);
}

.current-user-identity {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.current-user-icon {
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  border-radius: 10px;
  color: var(--accent);
  background: var(--accent-soft);
}

.current-user-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.current-user-copy small {
  overflow: hidden;
  color: var(--muted);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.current-user-copy strong {
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.change-user-button,
.current-user-error button {
  min-height: 34px;
  padding: 0 9px;
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  border: 1px solid var(--line);
  border-radius: 9px;
  color: var(--accent);
  background: var(--surface);
  font-size: 11px;
  font-weight: 720;
  transition:
    transform 180ms var(--ease-out),
    background-color 180ms ease;
}

.change-user-button:hover,
.current-user-error button:hover {
  background: var(--accent-soft);
}

.change-user-button:active,
.current-user-error button:active {
  transform: scale(0.98);
}

.current-user-loading {
  width: 100%;
  display: grid;
  gap: 7px;
}

.current-user-loading span {
  height: 10px;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--line), var(--surface), var(--line));
  background-size: 200% 100%;
  animation: user-loading 1.4s linear infinite;
}

.current-user-loading span:last-child {
  width: 58%;
}

.current-user-error {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: var(--danger);
  font-size: 10px;
}

.current-user-form {
  padding: 20px 22px 22px;
  display: grid;
  gap: 17px;
}

.current-user-form label {
  display: grid;
  gap: 7px;
  color: var(--muted-strong);
  font-size: 12px;
  font-weight: 650;
}

.current-user-form input {
  width: 100%;
  min-width: 0;
  height: 42px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  color: var(--ink);
  background: var(--surface);
  outline: 0;
  font-size: 13px;
}

.current-user-form input:focus {
  border-color: var(--accent);
}

.employee-id-control {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
}

.readonly-name {
  position: relative;
  display: block;
}

.readonly-name input {
  padding-right: 38px;
  color: var(--muted-strong);
  background: var(--surface-soft);
}

.readonly-name.resolved input {
  border-color: #a8cbbf;
  color: var(--ink);
  background: var(--accent-faint);
}

.readonly-name svg {
  position: absolute;
  top: 12px;
  right: 12px;
  color: var(--accent);
}

.current-user-form label small {
  color: var(--muted);
  font-size: 10px;
  font-weight: 400;
}

.current-user-form-error {
  margin: -3px 0 0;
  color: var(--danger);
  font-size: 12px;
  line-height: 1.5;
}

.current-user-actions {
  padding-top: 2px;
  display: flex;
  justify-content: flex-end;
  gap: 9px;
}

@keyframes user-loading {
  to {
    background-position: -200% 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .current-user-loading span {
    animation: none;
  }
}

@media (max-width: 520px) {
  .employee-id-control {
    grid-template-columns: 1fr;
  }

  .employee-id-control button {
    width: 100%;
  }
}
</style>
