import { request } from './client'
import type {
  BookingDraft,
  BookingDraftPayload,
  ChatResponse,
  MeetingRoom,
  ModelOption,
  SessionContext,
  SessionSummary,
  CurrentUser,
} from '@/types/api'

export const api = {
  currentUser: () => request<CurrentUser>('/api/current-user'),
  resolveCurrentUser: (employeeId: string) =>
    request<CurrentUser>('/api/current-user/resolve', {
      method: 'POST',
      body: JSON.stringify({ employeeId }),
    }),
  switchCurrentUser: (employeeId: string) =>
    request<CurrentUser>('/api/current-user', {
      method: 'PUT',
      body: JSON.stringify({ employeeId }),
    }),
  sessions: () => request<SessionSummary[]>('/api/sessions'),
  session: (id: string) => request<SessionContext>(`/api/sessions/${encodeURIComponent(id)}`),
  models: () => request<ModelOption[]>('/api/models'),
  chat: (payload: {
    message: string
    model: string | null
    contextWindowTokens: number
    sessionId?: string
  }) =>
    request<ChatResponse>('/api/chat', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  renameSession: (id: string, title: string) =>
    request<SessionSummary>(`/api/sessions/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),
  deleteSession: (id: string) =>
    request<{ ok: true }>(`/api/sessions/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),
  bookingDraft: (id: string) =>
    request<BookingDraft>(`/api/meeting-room-booking-drafts/${encodeURIComponent(id)}`),
  updateBookingDraft: (id: string, payload: BookingDraftPayload) =>
    request<BookingDraft>(`/api/meeting-room-booking-drafts/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  bookingRoomOptions: (id: string, payload: BookingDraftPayload) => {
    const parameters = new URLSearchParams({
      floor: payload.floor,
      date: payload.date,
      timeRange: payload.timeRange,
      capacity: String(payload.capacity),
    })
    return request<{ rooms: MeetingRoom[] }>(
      `/api/meeting-room-booking-drafts/${encodeURIComponent(id)}/room-options?${parameters}`,
    )
  },
  confirmBookingDraft: (id: string) =>
    request<{ draft: BookingDraft }>(
      `/api/meeting-room-booking-drafts/${encodeURIComponent(id)}/confirm`,
      { method: 'POST' },
    ),
  cancelBookingDraft: (id: string) =>
    request<BookingDraft>(`/api/meeting-room-booking-drafts/${encodeURIComponent(id)}/cancel`, {
      method: 'POST',
    }),
}
