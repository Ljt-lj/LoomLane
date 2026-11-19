import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, ApiNetworkError } from './api'
import type { SDL } from './types'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('API error reporting', () => {
  it('includes the endpoint and backend target for network failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('Failed to fetch')),
    )

    const error = await api
      .validate('project-1', {} as SDL)
      .catch((reason: unknown) => reason)

    expect(error).toBeInstanceOf(ApiNetworkError)
    expect(error).toHaveProperty(
      'message',
      expect.stringContaining('POST /api/projects/project-1/validate'),
    )
    expect(error).toHaveProperty(
      'message',
      expect.stringContaining('http://127.0.0.1:8000'),
    )
    expect(error).toHaveProperty(
      'message',
      expect.stringContaining('Failed to fetch'),
    )
  })

  it('surfaces the first structured workflow issue', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: 'Workflow validation failed',
            issues: [
              {
                code: 'missing_start',
                message: 'At least one start agent is required',
                path: 'start',
                severity: 'error',
              },
            ],
          }),
          {
            status: 422,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      ),
    )

    const error = await api
      .generate('project-1')
      .catch((reason: unknown) => reason)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toHaveProperty(
      'message',
      expect.stringContaining(
        '1 个校验问题：start: At least one start agent is required',
      ),
    )
    expect(error).toHaveProperty(
      'message',
      expect.stringContaining(
        'HTTP 422，POST /api/projects/project-1/generate',
      ),
    )
  })
})
