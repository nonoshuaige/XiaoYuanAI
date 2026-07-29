export type RequestState = 'idle' | 'loading' | 'success' | 'error'

export interface SessionSummary {
  sessionId: string
  title: string
  rounds: number
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  round: number
  role: 'user' | 'assistant'
  content: string
  created_at: string
  status: 'pending' | 'completed' | 'failed'
  error: string | null
  quickReplies: string[]
}

export interface ModelOption {
  id: string
  label: string
  model: string
  provider: string
  providerId: string
  default: boolean
  discovered: boolean
  callable: boolean
  source: string
}

export interface Booking {
  bookingId: string
  meetingId: string
  roomId: string
  startTime: string
  endTime: string
  capacity: number
  theme: string
  bookedBy: string
  source: string
  createdAt: string
}

export interface TimelineSlot {
  start: string
  end: string
  timeRange: string
  available: boolean
  booking: Booking | null
}

export interface MeetingRoom {
  roomId: string
  roomName: string
  floor: string
  capacity: number
  equipment: string[]
  available: boolean
  occupied: Booking[]
  timeline: TimelineSlot[]
  availableTimeRanges: string[]
  suggestedTimeRanges: string[]
}

export interface MeetingRoomSchedule {
  sandbox: boolean
  observedAt: string
  date: string
  displayWindow: string
  requestedTimeRange: string | null
  rooms: MeetingRoom[]
}

export type BookingDraftStatus = 'pending' | 'confirmed' | 'cancelled' | 'expired'

export interface BookingDraft {
  type: 'meetingRoomBookingDraft'
  draftId: string
  sessionId: string | null
  round: number | null
  roomId: string
  roomName: string
  floor: string
  date: string
  timeRange: string
  capacity: number
  theme: string
  bookedBy: string
  status: BookingDraftStatus
  bookingId: string | null
  meetingId: string | null
  expiresAt: string
  createdAt: string
  updatedAt: string
}

export interface SessionContext {
  sessionId: string
  title: string
  summary: string
  summary_range: { start_round: number; end_round: number } | null
  rounds: number
  uncovered_rounds: number
  compression_pending: boolean
  compression_error: string | null
  messages: ChatMessage[]
  artifactsByRound: Record<string, BookingDraft[]>
}

export interface ChatResponse {
  reply: string
  sessionId: string
  round: number
  status: 'pending'
  title: string
  model: string
  modelCallUrl: string
  artifacts: BookingDraft[]
  quickReplies: string[]
}

export interface Person {
  employee_id: string
  name: string
  phone: string
  department: string
}

export interface PersonPayload {
  employee_id: string
  name: string
  phone: string
  department: string
}

export interface BookingDraftPayload {
  roomId: string
  floor: string
  date: string
  timeRange: string
  capacity: number
  theme: string | null
}

export interface SandboxStatus {
  sandbox: true
  database: string
  destinations: Array<{
    id: 'employees' | 'meeting-rooms'
    label: string
    href: string
  }>
}
