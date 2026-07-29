<script setup lang="ts">
import { PhArrowLeft as ArrowLeft } from '@phosphor-icons/vue'

import AppBrand from './AppBrand.vue'

defineProps<{
  label: string
}>()
</script>

<template>
  <header class="page-header">
    <AppBrand linked />
    <div class="page-header-actions">
      <span class="environment-badge">
        <span class="live-dot" aria-hidden="true"></span>
        统一数据库 · {{ label }}
      </span>
      <RouterLink class="back-link" to="/">
        <ArrowLeft :size="17" weight="bold" aria-hidden="true" />
        <span>返回对话</span>
      </RouterLink>
    </div>
  </header>
</template>
