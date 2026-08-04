<script setup lang="ts">
import type { ContextWindowOption } from '@/types/api'

defineProps<{
  options: ContextWindowOption[]
  selected: number
  disabled?: boolean
}>()

const emit = defineEmits<{
  select: [tokens: number]
}>()

function handleChange(event: Event) {
  emit('select', Number((event.target as HTMLSelectElement).value))
}
</script>

<template>
  <label class="context-window-picker">
    <span>上下文</span>
    <select :value="selected" :disabled="disabled" @change="handleChange">
      <option v-for="option in options" :key="option.value" :value="option.value">
        {{ option.label }} · {{ option.description }}
      </option>
    </select>
  </label>
</template>
