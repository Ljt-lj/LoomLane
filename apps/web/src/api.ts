import type {
  ApiProfile,
  GenerateResponse,
  GateDecisionResponse,
  InputFileRef,
  OpenFolderResponse,
  ProjectDetail,
  ProjectSummary,
  RunDetail,
  RunStartRequest,
  RunSummary,
  SDL,
  SecretStatus,
  ValidationResponse,
} from './types'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000'

interface RunStartRequestShape {
  inputs: Record<string, unknown>
}

export class ApiError extends Error {
  status: number
  payload: Record<string, unknown>

  constructor(status: number, payload: Record<string, unknown>) {
    super(
      typeof payload.detail === 'string'
        ? payload.detail
        : `Request failed with status ${status}`,
    )
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })
  if (!response.ok) {
    let payload: Record<string, unknown> = {}
    try {
      payload = await response.json()
    } catch {
      payload = { detail: response.statusText }
    }
    throw new ApiError(response.status, payload)
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
    const response = await fetch(`${API_BASE}/api/projects/${projectId}/sdl`)
    if (!response.ok) {
      throw new ApiError(response.status, { detail: response.statusText })
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
    const response = await fetch(
      `${API_BASE}/api/projects/${projectId}/input-files?${query}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: file,
      },
    )
    if (!response.ok) {
      let payload: Record<string, unknown> = {}
      try {
        payload = await response.json()
      } catch {
        payload = { detail: response.statusText }
      }
      throw new ApiError(response.status, payload)
    }
    return response.json() as Promise<InputFileRef>
  },

  listRuns: (projectId: string) =>
    request<RunSummary[]>(`/api/projects/${projectId}/runs`),

  getRun: (runId: string) => request<RunDetail>(`/api/runs/${runId}`),

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
    `${API_BASE}/api/runs/${runId}/events?after=${after}`,
}

export type { RunStartRequest }
