import type {
  ApiProfile,
  GenerateResponse,
  GateDecisionResponse,
  InputFileRef,
  OpenFolderResponse,
  ProjectDetail,
  ProjectSummary,
  RunDetail,
  RunResumeOptions,
  RunStartRequest,
  RunSummary,
  SDL,
  SecretStatus,
  ValidationResponse,
} from './types'

const configuredApiBase = import.meta.env.VITE_API_BASE?.trim()
const API_BASE = configuredApiBase
  ? configuredApiBase.replace(/\/+$/, '')
  : ''
const BACKEND_TARGET =
  configuredApiBase && /^https?:\/\//i.test(configuredApiBase)
    ? configuredApiBase.replace(/\/+$/, '')
    : 'http://127.0.0.1:8000（Vite 代理目标）'

interface RunStartRequestShape {
  inputs: Record<string, unknown>
}

interface RunResumeRequestShape {
  source_run_id: string
  checkpoint_id: string
}

function endpoint(path: string) {
  return `${API_BASE}${path}`
}

function issueMessage(payload: Record<string, unknown>) {
  const issues = payload.issues
  if (!Array.isArray(issues) || issues.length === 0) return ''
  const first = issues[0]
  if (!first || typeof first !== 'object') return ''
  const issue = first as Record<string, unknown>
  const path = typeof issue.path === 'string' ? issue.path.trim() : ''
  const message =
    typeof issue.message === 'string' ? issue.message.trim() : '未知校验错误'
  return `${path ? `${path}: ` : ''}${message}`
}

function payloadDetail(payload: Record<string, unknown>) {
  const validationMessage = issueMessage(payload)
  if (validationMessage) {
    const count = Array.isArray(payload.issues) ? payload.issues.length : 1
    return `${count} 个校验问题：${validationMessage}`
  }

  const detail = payload.detail
  if (typeof detail === 'string' && detail.trim()) return detail.trim()
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0]
    if (first && typeof first === 'object') {
      const item = first as Record<string, unknown>
      const message = typeof item.msg === 'string' ? item.msg : ''
      const location = Array.isArray(item.loc) ? item.loc.join('.') : ''
      if (message) return `${location ? `${location}: ` : ''}${message}`
    }
  }
  return ''
}

export class ApiError extends Error {
  status: number
  payload: Record<string, unknown>
  method: string
  path: string

  constructor(
    status: number,
    payload: Record<string, unknown>,
    method = 'GET',
    path = '',
  ) {
    const detail = payloadDetail(payload)
    const request = `${method} ${path || '/'}`
    if (detail) {
      super(`${detail}（HTTP ${status}，${request}）`)
    } else if (status >= 500) {
      super(
        `后端请求失败（HTTP ${status}，${request}）。请确认后端已在 ${BACKEND_TARGET} 启动。`,
      )
    } else {
      super(`请求失败（HTTP ${status}，${request}）`)
    }
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
    this.method = method
    this.path = path
  }
}

export class ApiNetworkError extends Error {
  method: string
  path: string
  url: string

  constructor(method: string, path: string, url: string, cause: unknown) {
    const rawMessage =
      cause instanceof Error ? cause.message : String(cause ?? '未知网络错误')
    super(
      `无法连接后端服务（${method} ${path}）。请确认后端已在 ${BACKEND_TARGET} 启动，并检查网络或 CORS 配置。浏览器错误：${rawMessage}`,
    )
    this.name = 'ApiNetworkError'
    this.method = method
    this.path = path
    this.url = url
  }
}

async function apiFetch(
  path: string,
  init?: RequestInit,
  requestPath = path,
) {
  const method = (init?.method ?? 'GET').toUpperCase()
  const url = endpoint(path)
  try {
    return await fetch(url, init)
  } catch (error) {
    throw new ApiNetworkError(method, requestPath, url, error)
  }
}

async function errorPayload(response: Response) {
  let text = ''
  try {
    text = await response.text()
  } catch {
    // Fall back to the HTTP status text when the response body is unavailable.
  }
  if (text) {
    try {
      const value = JSON.parse(text)
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        return value as Record<string, unknown>
      }
    } catch {
      const plainText = text
        .replace(/<[^>]*>/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
      if (plainText) return { detail: plainText.slice(0, 500) }
    }
  }
  return { detail: response.statusText } as Record<string, unknown>
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase()
  const response = await apiFetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })
  if (!response.ok) {
    throw new ApiError(response.status, await errorPayload(response), method, path)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return response.json() as Promise<T>
}

export const api = {
  baseUrl: API_BASE,

  listProjects: () => request<ProjectSummary[]>('/api/projects'),

  getProject: (projectId: string) =>
    request<ProjectDetail>(`/api/projects/${projectId}`),

  createProject: (name: string) =>
    request<ProjectDetail>('/api/projects', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  updateProject: (
    projectId: string,
    sdl: SDL,
    canvas: ProjectDetail['canvas'],
    baseRevision: number,
  ) =>
    request<ProjectDetail>(`/api/projects/${projectId}`, {
      method: 'PUT',
      body: JSON.stringify({
        sdl,
        canvas,
        base_revision: baseRevision,
      }),
    }),

  deleteProject: (projectId: string) =>
    request<void>(`/api/projects/${projectId}`, { method: 'DELETE' }),

  listApiProfiles: () => request<ApiProfile[]>('/api/api-profiles'),

  createApiProfile: (profile: ApiProfile) =>
    request<ApiProfile>('/api/api-profiles', {
      method: 'POST',
      body: JSON.stringify(profile),
    }),

  updateApiProfile: (profileId: string, profile: ApiProfile) =>
    request<ApiProfile>(`/api/api-profiles/${profileId}`, {
      method: 'PUT',
      body: JSON.stringify(profile),
    }),

  deleteApiProfile: (profileId: string) =>
    request<void>(`/api/api-profiles/${profileId}`, { method: 'DELETE' }),

  apiProfileSecretStatus: (profileId: string) =>
    request<SecretStatus>(`/api/api-profiles/${profileId}/secret/status`),

  putApiProfileSecret: (
    profileId: string,
    apiKey: string,
  ) =>
    request<SecretStatus>(`/api/api-profiles/${profileId}/secret`, {
      method: 'PUT',
      body: JSON.stringify({ api_key: apiKey }),
    }),

  deleteApiProfileSecret: (profileId: string) =>
    request<void>(`/api/api-profiles/${profileId}/secret`, {
      method: 'DELETE',
    }),

  importProjectSecret: (
    libraryProfileId: string,
    projectId: string,
    projectProfileId: string,
  ) =>
    request<SecretStatus>(
      `/api/api-profiles/${libraryProfileId}/secret/import-from-project`,
      {
        method: 'POST',
        body: JSON.stringify({
          project_id: projectId,
          profile_id: projectProfileId,
        }),
      },
    ),

  validate: (projectId: string, sdl: SDL) =>
    request<ValidationResponse>(`/api/projects/${projectId}/validate`, {
      method: 'POST',
      body: JSON.stringify({ sdl }),
    }),

  importSdl: (projectId: string, text: string, baseRevision: number) =>
    request<ProjectDetail>(
      `/api/projects/${projectId}/import-sdl?base_revision=${baseRevision}`,
      {
        method: 'POST',
        body: JSON.stringify({ text }),
      },
    ),

  getSdl: async (projectId: string): Promise<string> => {
    const path = `/api/projects/${projectId}/sdl`
    const response = await apiFetch(path)
    if (!response.ok) {
      throw new ApiError(response.status, await errorPayload(response), 'GET', path)
    }
    return response.text()
  },

  generate: (projectId: string) =>
    request<GenerateResponse>(`/api/projects/${projectId}/generate`, {
      method: 'POST',
    }),

  secretStatus: (projectId: string, profileId: string) =>
    request<SecretStatus>(
      `/api/projects/${projectId}/secrets/${profileId}/status`,
    ),

  putSecret: (projectId: string, profileId: string, apiKey: string) =>
    request<SecretStatus>(
      `/api/projects/${projectId}/secrets/${profileId}`,
      {
        method: 'PUT',
        body: JSON.stringify({ api_key: apiKey }),
      },
    ),

  deleteSecret: (projectId: string, profileId: string) =>
    request<void>(`/api/projects/${projectId}/secrets/${profileId}`, {
      method: 'DELETE',
    }),

  startRun: (projectId: string, payload: RunStartRequestShape) =>
    request<RunSummary>(`/api/projects/${projectId}/runs`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  uploadInputFile: async (
    projectId: string,
    inputName: string,
    file: File,
  ): Promise<InputFileRef> => {
    const query = new URLSearchParams({
      input_name: inputName,
      file_name: file.name,
    })
    const path = `/api/projects/${projectId}/input-files?${query}`
    const response = await apiFetch(
      path,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
      },
    )
    if (!response.ok) {
      throw new ApiError(response.status, await errorPayload(response), 'POST', path)
    }
    return response.json() as Promise<InputFileRef>
  },

  listRuns: (projectId: string) =>
    request<RunSummary[]>(`/api/projects/${projectId}/runs`),

  getRun: (runId: string) => request<RunDetail>(`/api/runs/${runId}`),

  resumeOptions: (runId: string) =>
    request<RunResumeOptions>(`/api/runs/${runId}/resume-options`),

  resumeRun: (projectId: string, payload: RunResumeRequestShape) =>
    request<RunSummary>(`/api/projects/${projectId}/runs/resume`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  openRunFolder: (runId: string) =>
    request<OpenFolderResponse>(`/api/runs/${runId}/open-folder`, {
      method: 'POST',
    }),

  decideGate: (
    runId: string,
    gateId: string,
    decision: 'approve' | 'reject',
    comment?: string,
  ) =>
    request<GateDecisionResponse>(
      `/api/runs/${runId}/gates/${encodeURIComponent(gateId)}`,
      {
        method: 'POST',
        body: JSON.stringify({ decision, comment: comment || null }),
      },
    ),

  cancelRun: (runId: string) =>
    request<RunSummary>(`/api/runs/${runId}/cancel`, { method: 'POST' }),

  eventUrl: (runId: string, after = 0) =>
    endpoint(`/api/runs/${runId}/events?after=${after}`),
}

export type { RunStartRequest }
