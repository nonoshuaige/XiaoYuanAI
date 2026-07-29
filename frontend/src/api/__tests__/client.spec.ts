import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, request } from '../client'

describe('request', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns parsed JSON for a successful response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(request<{ ok: boolean }>('/api/example')).resolves.toEqual({ ok: true })
  })

  it('normalizes FastAPI validation details', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: [{ msg: '字段不能为空' }] }), {
          status: 422,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(request('/api/example')).rejects.toEqual(
      expect.objectContaining<ApiError>({
        status: 422,
        message: '字段不能为空',
      }),
    )
  })
})
