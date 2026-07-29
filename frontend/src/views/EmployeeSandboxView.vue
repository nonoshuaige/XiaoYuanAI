<script setup lang="ts">
import {
  PhMagnifyingGlass as MagnifyingGlass,
  PhPencilSimple as PencilSimple,
  PhPlus as Plus,
  PhTrash as Trash,
  PhUserList as UserList,
  PhX as X,
} from '@phosphor-icons/vue'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { api } from '@/api'
import AsyncPanel from '@/components/AsyncPanel.vue'
import BaseDialog from '@/components/BaseDialog.vue'
import PageHeader from '@/components/PageHeader.vue'
import ToastNotice from '@/components/ToastNotice.vue'
import type { Person, PersonPayload, RequestState } from '@/types/api'

const people = ref<Person[]>([])
const state = ref<RequestState>('idle')
const error = ref('')
const search = ref('')
const editingId = ref<string | null>(null)
const personDialogOpen = ref(false)
const deleteTarget = ref<Person | null>(null)
const saving = ref(false)
const deleting = ref(false)
const formError = ref('')
const deleteError = ref('')
const toast = ref('')
const toastKind = ref<'success' | 'error'>('success')
let searchTimer: number | undefined
let toastTimer: number | undefined

const form = reactive<PersonPayload>({
  employee_id: '',
  name: '',
  phone: '',
  department: '',
})

const departmentCount = computed(
  () => new Set(people.value.map((person) => person.department)).size,
)
const emptyTitle = computed(() => (search.value.trim() ? '没有匹配结果' : '还没有员工'))
const emptyMessage = computed(() =>
  search.value.trim()
    ? '换一个工号、姓名、手机号或部门关键词试试。'
    : '点击“新增员工”写入第一条沙箱数据。',
)

watch(search, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(loadPeople, 220)
})

async function loadPeople() {
  state.value = 'loading'
  error.value = ''
  try {
    people.value = await api.people(search.value.trim())
    state.value = people.value.length ? 'success' : 'idle'
  } catch (caught) {
    people.value = []
    error.value = caught instanceof Error ? caught.message : '加载员工目录失败'
    state.value = 'error'
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, { employee_id: '', name: '', phone: '', department: '' })
  formError.value = ''
  personDialogOpen.value = true
}

function openEdit(person: Person) {
  editingId.value = person.employee_id
  Object.assign(form, person)
  formError.value = ''
  personDialogOpen.value = true
}

function showToast(message: string, kind: 'success' | 'error' = 'success') {
  window.clearTimeout(toastTimer)
  toast.value = message
  toastKind.value = kind
  toastTimer = window.setTimeout(() => {
    toast.value = ''
  }, 2600)
}

async function savePerson() {
  if (
    !form.employee_id.trim() ||
    !form.name.trim() ||
    !form.phone.trim() ||
    !form.department.trim()
  ) {
    formError.value = '工号、姓名、手机号和部门均不能为空'
    return
  }
  saving.value = true
  formError.value = ''
  try {
    const payload = {
      employee_id: form.employee_id.trim(),
      name: form.name.trim(),
      phone: form.phone.trim(),
      department: form.department.trim(),
    }
    if (editingId.value) await api.updatePerson(editingId.value, payload)
    else await api.createPerson(payload)
    const wasEditing = Boolean(editingId.value)
    personDialogOpen.value = false
    showToast(wasEditing ? '员工信息已更新' : '新员工已写入沙箱')
    await loadPeople()
  } catch (caught) {
    formError.value = caught instanceof Error ? caught.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function deletePerson() {
  if (!deleteTarget.value) return
  deleting.value = true
  deleteError.value = ''
  try {
    await api.deletePerson(deleteTarget.value.employee_id)
    deleteTarget.value = null
    showToast('员工记录已删除')
    await loadPeople()
  } catch (caught) {
    deleteError.value = caught instanceof Error ? caught.message : '删除失败'
  } finally {
    deleting.value = false
  }
}

onMounted(loadPeople)
onBeforeUnmount(() => {
  window.clearTimeout(searchTimer)
  window.clearTimeout(toastTimer)
})
</script>

<template>
  <div class="page-shell">
    <PageHeader label="员工沙箱" />
    <main class="workspace employee-workspace">
      <section class="page-intro">
        <div>
          <span class="eyebrow">EMPLOYEE DIRECTORY</span>
          <h1>在这里修改，<br />下一次找人立即生效。</h1>
          <p>员工沙箱直接读写统一 SQLite 数据库，Agent 的找人结果会同步反映这些变化。</p>
        </div>
        <div class="stats-strip" aria-label="通讯录统计">
          <div>
            <strong>{{ state === 'error' ? '—' : people.length }}</strong>
            <span>当前员工</span>
          </div>
          <div>
            <strong>{{ state === 'error' ? '—' : departmentCount }}</strong>
            <span>覆盖部门</span>
          </div>
        </div>
      </section>

      <section class="data-panel">
        <div class="data-toolbar">
          <label class="search-control">
            <MagnifyingGlass :size="18" aria-hidden="true" />
            <input
              v-model="search"
              type="search"
              maxlength="100"
              placeholder="搜索工号、姓名、手机号或部门"
            />
            <button v-if="search" type="button" aria-label="清空搜索" @click="search = ''">
              <X :size="16" aria-hidden="true" />
            </button>
          </label>
          <button class="primary-button" type="button" @click="openCreate">
            <Plus :size="18" weight="bold" aria-hidden="true" />
            新增员工
          </button>
        </div>

        <div class="directory-heading">
          <div>
            <UserList :size="21" weight="duotone" aria-hidden="true" />
            <strong>员工目录</strong>
          </div>
          <span>最多展示 500 条记录</span>
        </div>

        <AsyncPanel v-if="state === 'loading'" state="loading" />
        <AsyncPanel
          v-else-if="state === 'error'"
          state="error"
          title="员工目录读取失败"
          :message="error"
          @retry="loadPeople"
        />
        <AsyncPanel
          v-else-if="!people.length"
          state="empty"
          :title="emptyTitle"
          :message="emptyMessage"
        />
        <div v-else class="employee-list">
          <article v-for="person in people" :key="person.employee_id" class="employee-row">
            <div>
              <small>工号</small>
              <strong>{{ person.employee_id }}</strong>
            </div>
            <div>
              <small>姓名</small>
              <strong>{{ person.name }}</strong>
            </div>
            <div>
              <small>手机号</small>
              <span>{{ person.phone }}</span>
            </div>
            <div>
              <small>部门</small>
              <span class="department-chip">{{ person.department }}</span>
            </div>
            <div class="row-actions">
              <button type="button" :aria-label="`编辑 ${person.name}`" @click="openEdit(person)">
                <PencilSimple :size="17" aria-hidden="true" />
              </button>
              <button
                class="danger"
                type="button"
                :aria-label="`删除 ${person.name}`"
                @click="deleteTarget = person"
              >
                <Trash :size="17" aria-hidden="true" />
              </button>
            </div>
          </article>
        </div>
      </section>
    </main>
  </div>

  <BaseDialog
    :open="personDialogOpen"
    :title="editingId ? '编辑员工' : '新增员工'"
    :description="
      editingId ? `正在修改 ${editingId} 的沙箱记录。` : '填写四项信息并保存到统一数据库。'
    "
    :busy="saving"
    @close="personDialogOpen = false"
  >
    <form class="form-grid" @submit.prevent="savePerson">
      <label>
        <span>工号</span>
        <input v-model="form.employee_id" required maxlength="32" autocomplete="off" />
        <small>唯一，可在编辑时修改</small>
      </label>
      <label>
        <span>姓名</span>
        <input v-model="form.name" required maxlength="50" autocomplete="name" />
        <small>允许重名</small>
      </label>
      <label>
        <span>手机号</span>
        <input v-model="form.phone" type="tel" required maxlength="32" autocomplete="tel" />
        <small>必须唯一</small>
      </label>
      <label>
        <span>部门</span>
        <input v-model="form.department" required maxlength="80" autocomplete="organization" />
        <small>用于姓名消歧</small>
      </label>
      <p v-if="formError" class="form-error">{{ formError }}</p>
      <div class="dialog-actions form-wide">
        <button
          class="secondary-button"
          type="button"
          :disabled="saving"
          @click="personDialogOpen = false"
        >
          取消
        </button>
        <button class="primary-button" type="submit" :disabled="saving">
          {{ saving ? '保存中' : editingId ? '保存修改' : '保存员工' }}
        </button>
      </div>
    </form>
  </BaseDialog>

  <BaseDialog
    :open="Boolean(deleteTarget)"
    title="删除这位员工？"
    description="该操作会立即写入统一数据库，且无法撤销。"
    width="small"
    :busy="deleting"
    @close="deleteTarget = null"
  >
    <p class="delete-summary">
      即将删除
      <strong>{{ deleteTarget?.name }}（{{ deleteTarget?.employee_id }}）</strong>
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
      <button class="danger-button" type="button" :disabled="deleting" @click="deletePerson">
        <Trash :size="17" aria-hidden="true" />
        {{ deleting ? '删除中' : '确认删除' }}
      </button>
    </div>
  </BaseDialog>

  <ToastNotice :message="toast" :kind="toastKind" />
</template>
