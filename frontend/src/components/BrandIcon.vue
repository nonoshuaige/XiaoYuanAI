<script setup lang="ts">
withDefaults(
  defineProps<{
    size?: number
  }>(),
  {
    size: 42,
  },
)
</script>

<template>
  <svg
    class="brand-icon"
    :width="size"
    :height="size"
    viewBox="0 0 64 64"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <rect width="64" height="64" rx="18" fill="#176B5B" />
    <path
      d="M17 17.5H47C51.1421 17.5 54.5 20.8579 54.5 25V37C54.5 41.1421 51.1421 44.5 47 44.5H33L22 53V44.5H17C12.8579 44.5 9.5 41.1421 9.5 37V25C9.5 20.8579 12.8579 17.5 17 17.5Z"
      fill="#F9FCFA"
    />
    <path
      d="M32 21.5C32.8284 27.6797 36.3203 31.1716 42.5 32C36.3203 32.8284 32.8284 36.3203 32 42.5C31.1716 36.3203 27.6797 32.8284 21.5 32C27.6797 31.1716 31.1716 27.6797 32 21.5Z"
      fill="#176B5B"
    />
    <circle cx="46.5" cy="21.5" r="3.5" fill="#9FD8C1" />
  </svg>
</template>
