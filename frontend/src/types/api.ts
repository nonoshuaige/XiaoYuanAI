export interface SessionSummary {
  sessionId: string
  title: string
  rounds: number
  created_at: string
  updated_at: string
}

export interface CurrentUser {
  employeeId: string
  name: string
}

export interface ChatMessage {
  round: number
  role: 'user' | 'assistant'
  content: string
  created_at: string
  status: 'pending' | 'completed' | 'failed'
  error: string | null
  quickReplies: string[]
  contextWindowTokens?: number | null
  contextEstimatedTokens?: number | null
  contextTruncated?: boolean
  contextDroppedRounds?: number
  inputTokens?: number | null
  outputTokens?: number | null
  totalTokens?: number | null
  tokenUsageEstimated?: boolean
}

export interface ContextWindowOption {
  value: number
  label: string
  description: string
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
  contextWindowTokens: number
  modelCallUrl: string
  eventsUrl: string
  artifacts: BookingDraft[]
  quickReplies: string[]
}

export interface BookingDraftPayload {
  roomId: string
  floor: string
  date: string
  timeRange: string
  capacity: number
  theme: string | null
}
