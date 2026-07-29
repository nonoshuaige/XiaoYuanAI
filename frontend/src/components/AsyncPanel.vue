<script setup lang="ts">
import {
  PhArrowClockwise as ArrowClockwise,
  PhWarningCircle as WarningCircle,
} from '@phosphor-icons/vue'

withDefaults(
  defineProps<{
    state: 'loading' | 'empty' | 'error'
    title?: string
    message?: string
    retryLabel?: string
  }>(),
  {
    title: '',
    message: '',
    retryLabel: '重新加载',
  },
)

defineEmits<{
  retry: []
}>()
</script>

<template>
  <div v-if="state === 'loading'" class="async-panel skeleton-panel" aria-label="正在加载">
    <span v-for="index in 4" :key="index" class="skeleton-line"></span>
  </div>
  <div v-else class="async-panel" :class="`${state}-panel`">
    <div class="state-symbol" aria-hidden="true">
      <WarningCircle v-if="state === 'error'" :size="25" />
      <span v-else>0</span>
    </div>
    <h2>{{ title }}</h2>
    <p>{{ message }}</p>
    <button v-if="state === 'error'" class="secondary-button" type="button" @click="$emit('retry')">
      <ArrowClockwise :size="17" aria-hidden="true" />
      {{ retryLabel }}
    </button>
  </div>
</template>
