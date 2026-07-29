<script setup lang="ts">
import { PhCaretDown as CaretDown, PhCaretRight as CaretRight } from '@phosphor-icons/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { ModelOption } from '@/types/api'

const props = defineProps<{
  models: ModelOption[]
  selectedId: string | null
  disabled?: boolean
}>()

const emit = defineEmits<{
  select: [id: string]
}>()

const root = ref<HTMLElement | null>(null)
const open = ref(false)
const activeProviderId = ref('')

const selectedModel = computed(
  () => props.models.find((model) => model.id === props.selectedId) ?? props.models[0] ?? null,
)

const groups = computed(() => {
  const grouped = new Map<string, { id: string; label: string; models: ModelOption[] }>()
  props.models.forEach((model) => {
    const group = grouped.get(model.providerId) ?? {
      id: model.providerId,
      label: model.provider,
      models: [],
    }
    group.models.push(model)
    grouped.set(model.providerId, group)
  })
  return [...grouped.values()]
})

const activeGroup = computed(
  () =>
    groups.value.find((group) => group.id === activeProviderId.value) ??
    groups.value.find((group) => group.id === selectedModel.value?.providerId) ??
    groups.value[0] ??
    null,
)

watch(
  selectedModel,
  (model) => {
    if (model) activeProviderId.value = model.providerId
  },
  { immediate: true },
)

function toggle() {
  if (props.disabled || !props.models.length) return
  open.value = !open.value
  if (open.value && selectedModel.value) {
    activeProviderId.value = selectedModel.value.providerId
  }
}

function chooseModel(id: string) {
  emit('select', id)
  open.value = false
}

function handleDocumentPointer(event: PointerEvent) {
  if (root.value && !root.value.contains(event.target as Node)) open.value = false
}

function handleDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false
}

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointer)
  document.addEventListener('keydown', handleDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointer)
  document.removeEventListener('keydown', handleDocumentKeydown)
})
</script>

<template>
  <div ref="root" class="model-picker">
    <span class="model-picker-label">模型</span>
    <button
      class="model-picker-trigger"
      type="button"
      :disabled="disabled || !selectedModel"
      :aria-expanded="open"
      aria-haspopup="menu"
      @click="toggle"
    >
      <span>
        <small>{{ selectedModel?.provider ?? '未配置' }}</small>
        <strong>{{ selectedModel?.label ?? '没有可用模型' }}</strong>
      </span>
      <CaretDown :size="15" :class="{ rotated: open }" aria-hidden="true" />
    </button>

    <div v-if="open" class="model-picker-menu" role="menu" aria-label="选择模型">
      <div class="model-provider-list" aria-label="模型服务商">
        <button
          v-for="group in groups"
          :key="group.id"
          type="button"
          :class="{ active: group.id === activeGroup?.id }"
          @mouseenter="activeProviderId = group.id"
          @focus="activeProviderId = group.id"
          @click="activeProviderId = group.id"
        >
          <span>{{ group.label }}</span>
          <small>{{ group.models.length }}</small>
          <CaretRight :size="14" aria-hidden="true" />
        </button>
      </div>
      <div class="model-option-list" :aria-label="`${activeGroup?.label ?? ''}模型`">
        <p>{{ activeGroup?.label }}</p>
        <button
          v-for="model in activeGroup?.models ?? []"
          :key="model.id"
          type="button"
          :class="{ selected: model.id === selectedId }"
          @click="chooseModel(model.id)"
        >
          <span>{{ model.label }}</span>
          <small v-if="model.default">默认</small>
        </button>
      </div>
    </div>
  </div>
</template>
