import { describe, expect, it } from 'vitest'

import { contextMessages } from '@/stores/chat'
import type { SessionContext } from '@/types/api'

function context(
  status: 'pending' | 'completed' | 'failed',
  withAssistant = false,
): SessionContext {
  return {
    sessionId: 'session-1',
    title: '后台生成',
    summary: '',
    summary_range: null,
    rounds: 1,
    uncovered_rounds: 1,
    compression_pending: false,
    compression_error: null,
    messages: [
      {
        round: 1,
        role: 'user',
        content: '测试消息',
        created_at: '2026-07-29T17:00:00',
        status,
        error: status === 'failed' ? '模拟失败' : null,
      },
      ...(withAssistant
        ? [
            {
              round: 1,
              role: 'assistant' as const,
              content: '后台回复',
              created_at: '2026-07-29T17:00:01',
              status,
              error: null,
              contextWindowTokens: 16_384,
              contextEstimatedTokens: 2_048,
              contextTruncated: false,
              contextDroppedRounds: 0,
              inputTokens: 2_200,
              outputTokens: 120,
              totalTokens: 2_320,
              tokenUsageEstimated: false,
              modelSteps: [
                {
                  step: 1,
                  attempt: 1,
                  phase: 'direct_answer' as const,
                  toolNames: [],
                  inputTokens: 2_200,
                  outputTokens: 120,
                  totalTokens: 2_320,
                  estimated: false,
                },
              ],
            },
          ]
        : []),
    ],
    artifactsByRound: {},
  }
}

describe('contextMessages', () => {
  it('restores a visible assistant placeholder for a durable pending turn', () => {
    const messages = contextMessages(context('pending'))

    expect(messages).toHaveLength(2)
    expect(messages[1]).toMatchObject({
      role: 'assistant',
      content: '',
      status: 'pending',
      activities: [
        {
          label: '正在连接实时进度',
          status: 'running',
        },
      ],
    })
  })

  it('uses the persisted assistant response after polling completes', () => {
    const messages = contextMessages(context('completed', true))

    expect(messages).toHaveLength(2)
    expect(messages[1]).toMatchObject({
      role: 'assistant',
      content: '后台回复',
      status: 'completed',
      inputTokens: 2_200,
      outputTokens: 120,
      contextWindowTokens: 16_384,
      modelSteps: [{ phase: 'direct_answer', totalTokens: 2_320 }],
    })
  })

  it('shows a retryable timeout instead of a permanent loading placeholder', () => {
    const timedOut = context('failed')
    timedOut.messages[0]!.error = '模型响应超时（60秒），请重试'

    const messages = contextMessages(timedOut)

    expect(messages[1]).toMatchObject({
      role: 'assistant',
      content: '模型响应超时（60秒），请重试',
      status: 'failed',
      activities: [],
    })
  })
})
