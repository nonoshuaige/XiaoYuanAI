<script setup lang="ts">
import { PhCheckCircle as CheckCircle, PhWarningCircle as WarningCircle } from '@phosphor-icons/vue'

defineProps<{
  message: string
  kind?: 'success' | 'error'
}>()
</script>

<template>
  <Transition name="toast">
    <div v-if="message" class="toast-notice" :class="{ error: kind === 'error' }" role="status">
      <WarningCircle v-if="kind === 'error'" :size="19" weight="fill" aria-hidden="true" />
      <CheckCircle v-else :size="19" weight="fill" aria-hidden="true" />
      {{ message }}
    </div>
  </Transition>
</template>
