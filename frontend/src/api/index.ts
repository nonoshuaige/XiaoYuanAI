import { request } from './client'
import type {
  BookingDraft,
  BookingDraftPayload,
  ChatResponse,
  MeetingRoom,
  MeetingRoomSchedule,
  ModelOption,
  Person,
  PersonPayload,
  SandboxStatus,
  SessionContext,
  SessionSummary,
} from '@/types/api'

export const api = {
  sandboxStatus: () => request<SandboxStatus>('/api/sandbox/status'),
  sessions: () => request<SessionSummary[]>('/api/sessions'),
  session: (id: string) => request<SessionContext>(`/api/sessions/${encodeURIComponent(id)}`),
  models: () => request<ModelOption[]>('/api/models'),
  chat: (payload: { message: string; model: string | null; sessionId?: string }) =>
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
  people: (search = '') =>
    request<Person[]>(
      `/api/sandbox/people${search ? `?search=${encodeURIComponent(search)}` : ''}`,
    ),
  createPerson: (payload: PersonPayload) =>
    request<Person>('/api/sandbox/people', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updatePerson: (originalId: string, payload: PersonPayload) =>
    request<Person>(`/api/sandbox/people/${encodeURIComponent(originalId)}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  deletePerson: (id: string) =>
    request<void>(`/api/sandbox/people/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),
  meetingRooms: (date: string) =>
    request<MeetingRoomSchedule>(
      `/api/sandbox/meeting-rooms?${new URLSearchParams({ date }).toString()}`,
    ),
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
}
