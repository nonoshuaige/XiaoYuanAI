<script setup lang="ts">
import { PhX as X } from '@phosphor-icons/vue'
import { nextTick, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    description?: string
    busy?: boolean
    width?: 'small' | 'medium' | 'large'
  }>(),
  {
    description: '',
    busy: false,
    width: 'medium',
  },
)

const emit = defineEmits<{
  close: []
}>()

const closeButton = ref<HTMLButtonElement | null>(null)

watch(
  () => props.open,
  async (open) => {
    if (open) {
      await nextTick()
      closeButton.value?.focus()
    }
  },
)

function close() {
  if (!props.busy) emit('close')
}
</script>

<template>
  <Teleport to="body">
    <Transition name="dialog">
      <div
        v-if="open"
        class="dialog-backdrop"
        role="presentation"
        @mousedown.self="close"
        @keydown.esc="close"
      >
        <section
          class="dialog-card"
          :class="`dialog-${width}`"
          role="dialog"
          aria-modal="true"
          :aria-label="title"
        >
          <header class="dialog-heading">
            <div>
              <h2>{{ title }}</h2>
              <p v-if="description">{{ description }}</p>
            </div>
            <button
              ref="closeButton"
              class="icon-button"
              type="button"
              :disabled="busy"
              aria-label="关闭"
              @click="close"
            >
              <X :size="19" aria-hidden="true" />
            </button>
          </header>
          <slot />
        </section>
      </div>
    </Transition>
  </Teleport>
</template>
