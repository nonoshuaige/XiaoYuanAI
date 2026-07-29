<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string
    subtitle?: string
    linked?: boolean
  }>(),
  {
    title: '小原 AI 助手',
    subtitle: 'XIAOYUAN ASSISTANT',
    linked: false,
  },
)
</script>

<template>
  <RouterLink v-if="linked" class="app-brand" to="/" aria-label="返回小原 AI 对话">
    <span class="brand-mark" aria-hidden="true">原</span>
    <span class="brand-copy">
      <strong>{{ title }}</strong>
      <small>{{ subtitle }}</small>
    </span>
  </RouterLink>
  <div v-else class="app-brand">
    <span class="brand-mark" aria-hidden="true">原</span>
    <span class="brand-copy">
      <strong>{{ title }}</strong>
      <small>{{ subtitle }}</small>
    </span>
  </div>
</template>
