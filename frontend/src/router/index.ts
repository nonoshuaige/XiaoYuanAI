import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'chat',
      component: () => import('@/views/ChatView.vue'),
      meta: { title: '小原 AI 助手' },
    },
    {
      path: '/employee-sandbox',
      name: 'employees',
      component: () => import('@/views/EmployeeSandboxView.vue'),
      meta: { title: '员工沙箱 · 小原 AI' },
    },
    {
      path: '/meeting-room-sandbox',
      name: 'meeting-rooms',
      component: () => import('@/views/MeetingRoomSandboxView.vue'),
      meta: { title: '会议室沙箱 · 小原 AI' },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

router.afterEach((to) => {
  document.title = String(to.meta.title ?? '小原 AI 助手')
})

export default router
