<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed } from 'vue'

const props = defineProps<{
  content: string
}>()

marked.use({
  gfm: true,
  breaks: true,
})

const rendered = computed(() =>
  DOMPurify.sanitize(marked.parse(props.content, { async: false }) as string, {
    ADD_ATTR: ['target'],
  }),
)
</script>

<template>
  <!-- Content is sanitized before rendering. -->
  <div class="markdown-content" v-html="rendered"></div>
</template>
