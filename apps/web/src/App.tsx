import {
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type NodeChange,
  type OnSelectionChangeFunc,
  type Viewport,
} from '@xyflow/react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import yaml from 'js-yaml'
import {
  Activity,
  Bot,
  Braces,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  CirclePlay,
  Code2,
  FileCode2,
  FileUp,
  Folder,
  FolderOpen,
  FolderPlus,
  GitBranch,
  KeyRound,
  LoaderCircle,
  Pause,
  PanelBottomClose,
  PanelBottomOpen,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Play,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  SkipBack,
  SkipForward,
  Square,
  Terminal,
  Trash2,
  Unplug,
  Workflow,
  X,
} from 'lucide-react'
import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { api, ApiError } from './api'
import {
  AgentNode,
  type AgentFlowNode,
  type NodeRunStatus,
} from './AgentNode'
import ConditionEditor from './ConditionEditor'
import JsonOutputEditor from './JsonOutputEditor'
import {
  dedupeAndSortRunEvents,
  nextReplaySeq,
  previousReplaySeq,
  replayRunEvents,
  type GateReplayRecord,
} from './run-replay'
import {
  applyLibrarySecretStatuses,
  mergeSettledSecretStatuses,
} from './secret-status-utils'
import type {
  Agent,
  ApiProfile,
  CanvasNode,
  ConditionNode,
  EdgeType,
  Port,
  PortType,
  ProjectDetail,
  RunDetail,
  RunEvent,
  RunSummary,
  SDL,
  SecretStatus,
  ValidationIssue,
  WorkflowEdge,
} from './types'
import {
  INSPECTOR_MAX_WIDTH,
  INSPECTOR_MIN_WIDTH,
  DRAWER_MAX_HEIGHT,
  DRAWER_MIN_HEIGHT,
  LEFT_PANEL_MAX_WIDTH,
  LEFT_PANEL_MIN_WIDTH,
  useUiStore,
  type DrawerTab,
  type InspectorTab,
} from './uiStore'
import {
  conditionPaths,
  defaultCondition,
  firstCompatibleMapping,
  removeApiProfile,
  uniqueId,
} from './workflow-utils'

const nodeTypes = { agent: AgentNode }
const PORT_TYPES: PortType[] = ['TEXT', 'DATA', 'FILE', 'MIXED']
const EDGE_TYPES: EdgeType[] = [
  'PIPE',
  'CONDITIONAL',
  'FEEDBACK',
  'LOOP',
  'FAN_OUT',
  'AGGREGATE',
  'HUMAN_GATE',
]
const EVENT_TYPES = [
  'run_started',
  'node_enter',
  'node_exit',
  'edge_traversed',
  'token_usage',
  'artifact_written',
  'human_gate_waiting',
  'human_gate_resolved',
  'error',
  'run_finished',
]
const TERMINAL_RUN_STATUSES = new Set(['succeeded', 'failed', 'cancelled'])

type Selection =
  | { kind: 'node'; id: string }
  | { kind: 'edge'; id: string }
  | null

interface Notice {
  tone: 'error' | 'success' | 'info'
  message: string
}

interface UploadedRunFile {
  name: string
  path: string
  size: number
  uploading?: boolean
}

type RunInputValue = string | UploadedRunFile

interface RunArtifact {
  name: string
  path: string
  size: number
}

function classNames(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ')
}

function initialRunInputs(
  inputs: SDL['workflow']['inputs'],
): Record<string, RunInputValue> {
  return Object.fromEntries(
    inputs
      .filter((input) => input.type !== 'FILE')
      .map((input) => [input.name, input.type === 'DATA' ? '{}' : '']),
  )
}

function isUploadedRunFile(
  value: RunInputValue | undefined,
): value is UploadedRunFile {
  return Boolean(
    value &&
      typeof value === 'object' &&
      typeof value.name === 'string' &&
      typeof value.path === 'string' &&
      typeof value.size === 'number',
  )
}

function runArtifacts(run: RunSummary | RunDetail | null): RunArtifact[] {
  if (!run || !('result' in run) || !run.result) return []
  const value = run.result.artifacts
  if (!Array.isArray(value)) return []
  return value.filter((item): item is RunArtifact => {
    if (!item || typeof item !== 'object') return false
    const artifact = item as Record<string, unknown>
    return (
      typeof artifact.name === 'string' &&
      typeof artifact.path === 'string' &&
      typeof artifact.size === 'number'
    )
  })
}

function issueFromError(error: unknown): ValidationIssue[] {
  if (!(error instanceof ApiError)) return []
  const issues = error.payload.issues
  return Array.isArray(issues) ? (issues as ValidationIssue[]) : []
}

function parseEvent(message: MessageEvent): RunEvent | null {
  try {
    const value = JSON.parse(message.data)
    if (
      value &&
      typeof value === 'object' &&
      typeof value.seq === 'number' &&
      typeof value.type === 'string'
    ) {
      return value as RunEvent
    }
  } catch {
    return null
  }
  return null
}

function makeCanvasNodes(sdl: SDL, canvas: ProjectDetail['canvas']) {
  const byId = new Map(canvas.nodes.map((node) => [node.id, node]))
  return sdl.agents.map((agent, index) => {
    const existing = byId.get(agent.id)
    return (
      existing ?? {
        id: agent.id,
        x: 80 + (index % 3) * 360,
        y: 120 + Math.floor(index / 3) * 210,
        width: 238,
        height: 132,
      }
    )
  })
}

function formatTime(value: string | null | undefined) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

function formatFileSize(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function RunFileInput({
  name,
  required,
  value,
  onSelect,
  onRemove,
}: {
  name: string
  required: boolean
  value?: UploadedRunFile
  onSelect: (file: File) => void
  onRemove: () => void
}) {
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)
  const uploading = value?.uploading === true

  function pickFile() {
    if (!uploading) inputRef.current?.click()
  }

  return (
    <div
      className={classNames(
        'file-dropzone',
        dragging && 'dragging',
        value && 'has-file',
        uploading && 'uploading',
      )}
      role="button"
      tabIndex={0}
      aria-label={`为 ${name} 选择文件`}
      onClick={pickFile}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          pickFile()
        }
      }}
      onDragEnter={(event) => {
        event.preventDefault()
        if (!uploading) setDragging(true)
      }}
      onDragOver={(event) => {
        event.preventDefault()
        if (!uploading) setDragging(true)
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setDragging(false)
        }
      }}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        if (uploading) return
        const file = event.dataTransfer.files[0]
        if (file) onSelect(file)
      }}
    >
      <input
        ref={inputRef}
        className="file-picker"
        type="file"
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) onSelect(file)
          event.target.value = ''
        }}
      />
      {value ? (
        <>
          <span className="file-dropzone-icon">
            {uploading ? (
              <LoaderCircle className="spin" size={17} />
            ) : (
              <FileCode2 size={17} />
            )}
          </span>
          <span className="file-dropzone-copy">
            <strong title={value.name}>{value.name}</strong>
            <small>{uploading ? '上传中' : formatFileSize(value.size)}</small>
          </span>
          {!uploading && (
            <button
              className="icon-button file-remove"
              type="button"
              title="移除文件"
              onClick={(event) => {
                event.stopPropagation()
                onRemove()
              }}
            >
              <X size={13} />
            </button>
          )}
        </>
      ) : (
        <>
          <span className="file-dropzone-icon">
            <FileUp size={17} />
          </span>
          <span className="file-dropzone-copy">
            <strong>拖入文件或点击选择</strong>
            <small>{required ? '必填' : '可选'}</small>
          </span>
        </>
      )}
    </div>
  )
}

function runStatusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: '排队',
    running: '运行中',
    waiting: '等待人工确认',
    succeeded: '成功',
    failed: '失败',
    cancelled: '已取消',
  }
  return labels[status] ?? status
}

function RunErrorPanel({ message }: { message: string }) {
  return (
    <div className="run-error" role="alert">
      <CircleAlert size={14} />
      <div>
        <strong>运行失败</strong>
        <span>{message}</span>
      </div>
    </div>
  )
}

function defaultHumanGate() {
  return {
    prompt: 'Approve this workflow step before it continues.',
    timeout_seconds: 300,
    on_reject: 'fail' as const,
    on_timeout: 'fail' as const,
  }
}

function JsonLiteralInput({
  value,
  onChange,
  placeholder,
}: {
  value: unknown
  onChange: (value: unknown) => void
  placeholder?: string
}) {
  const [text, setText] = useState(() => JSON.stringify(value) ?? 'null')
  const [invalid, setInvalid] = useState(false)

  useEffect(() => {
    setText(JSON.stringify(value) ?? 'null')
    setInvalid(false)
  }, [value])

  return (
    <input
      className={invalid ? 'field-invalid' : ''}
      value={text}
      placeholder={placeholder}
      onChange={(event) => {
        const next = event.target.value
        setText(next)
        try {
          onChange(JSON.parse(next))
          setInvalid(false)
        } catch {
          setInvalid(true)
        }
      }}
    />
  )
}

function SdlEditor({
  draft,
  onImported,
  onError,
}: {
  draft: ProjectDetail
  onImported: (detail: ProjectDetail) => void
  onError: (message: string) => void
}) {
  const generated = useMemo(
    () =>
      yaml.dump(draft.sdl, {
        noRefs: true,
        lineWidth: 100,
        sortKeys: false,
      }),
    [draft.sdl],
  )
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(generated)
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    if (!editing) setText(generated)
  }, [editing, generated])

  async function importSdl() {
    setImporting(true)
    try {
      const detail = await api.importSdl(draft.id, text, draft.revision)
      onImported(detail)
      setEditing(false)
    } catch (error) {
      onError(error instanceof Error ? error.message : 'SDL 导入失败')
    } finally {
      setImporting(false)
    }
  }

  function exportSdl() {
    const blob = new Blob([generated], {
      type: 'application/yaml;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'workflow.sdl.yaml'
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="sdl-editor">
      <div className="drawer-actions">
        <button
          className="small-button"
          onClick={() => setEditing((value) => !value)}
        >
          <Code2 size={14} />
          {editing ? '预览' : '编辑'}
        </button>
        {editing && (
          <button className="small-button accent" onClick={importSdl}>
            {importing ? (
              <LoaderCircle size={14} className="spin" />
            ) : (
              <Check size={14} />
            )}
            导入
          </button>
        )}
        <button className="small-button" onClick={exportSdl}>
          <FileCode2 size={14} />
          导出
        </button>
      </div>
      {editing ? (
        <textarea
          className="sdl-textarea"
          value={text}
          onChange={(event) => setText(event.target.value)}
          spellCheck={false}
        />
      ) : (
        <pre className="sdl-preview">{generated}</pre>
      )}
    </div>
  )
}

function App() {
  const queryClient = useQueryClient()
  const {
    drawerTab,
    inspectorTab,
    drawerOpen,
    drawerHeight,
    leftPanelOpen,
    leftPanelWidth,
    inspectorOpen,
    inspectorWidth,
    setDrawerTab,
    setInspectorTab,
    setDrawerOpen,
    setDrawerHeight,
    setLeftPanelOpen,
    setLeftPanelWidth,
    setInspectorOpen,
    setInspectorWidth,
  } = useUiStore()
  const [projectId, setProjectId] = useState<string | null>(null)
  const [draft, setDraft] = useState<ProjectDetail | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [selection, setSelection] = useState<Selection>(null)
  const [issues, setIssues] = useState<ValidationIssue[]>([])
  const [notice, setNotice] = useState<Notice | null>(null)
  const [newProjectOpen, setNewProjectOpen] = useState(false)
  const [newProjectName, setNewProjectName] = useState('')
  const [nodes, setNodes] = useState<AgentFlowNode[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [runInputs, setRunInputs] = useState<Record<string, RunInputValue>>({})
  const [currentRun, setCurrentRun] = useState<RunSummary | RunDetail | null>(
    null,
  )
  const [runEvents, setRunEvents] = useState<RunEvent[]>([])
  const [replaySeq, setReplaySeq] = useState<number | null>(null)
  const [replayPlaying, setReplayPlaying] = useState(false)
  const [replaySpeed, setReplaySpeed] = useState(1000)
  const [gateComments, setGateComments] = useState<Record<string, string>>({})
  const [decidingGate, setDecidingGate] = useState<string | null>(null)
  const [secretStatuses, setSecretStatuses] = useState<
    Record<string, SecretStatus>
  >({})
  const [secretDrafts, setSecretDrafts] = useState<Record<string, string>>({})
  const [keySaving, setKeySaving] = useState<string | null>(null)
  const [profileDeleting, setProfileDeleting] = useState<string | null>(null)
  const [libraryProfileDrafts, setLibraryProfileDrafts] = useState<
    Record<string, ApiProfile>
  >({})
  const [librarySecretDrafts, setLibrarySecretDrafts] = useState<
    Record<string, string>
  >({})
  const [librarySecretStatuses, setLibrarySecretStatuses] = useState<
    Record<string, SecretStatus>
  >({})
  const [libraryProfileSaving, setLibraryProfileSaving] = useState<string | null>(
    null,
  )
  const [libraryProfileDeleting, setLibraryProfileDeleting] = useState<
    string | null
  >(null)
  const [libraryKeySaving, setLibraryKeySaving] = useState<string | null>(null)
  const [libraryProfileLinking, setLibraryProfileLinking] = useState<
    string | null
  >(null)
  const [generatedFiles, setGeneratedFiles] = useState<string[]>([])
  const eventSourceRef = useRef<EventSource | null>(null)
  const fileUploadIdsRef = useRef<Record<string, number>>({})
  const noticeTimerRef = useRef<number | null>(null)
  const viewportRef = useRef<Viewport | null>(null)
  const inspectorResizeRef = useRef<{
    startX: number
    startWidth: number
  } | null>(null)
  const leftResizeRef = useRef<{
    startX: number
    startWidth: number
  } | null>(null)
  const drawerResizeRef = useRef<{
    startY: number
    startHeight: number
  } | null>(null)
  const [resizingInspector, setResizingInspector] = useState(false)
  const [resizingLeft, setResizingLeft] = useState(false)
  const [resizingDrawer, setResizingDrawer] = useState(false)
  const replayMaxSeq = runEvents.reduce(
    (maximum, event) => Math.max(maximum, event.seq),
    0,
  )
  const replayUntilSeq = replaySeq ?? replayMaxSeq
  const replayState = useMemo(
    () => replayRunEvents(runEvents, replayUntilSeq),
    [replayUntilSeq, runEvents],
  )
  const {
    nodeStatuses,
    edgeTraversals,
    tokenUsage,
  } = replayState
  const pendingGates = useMemo(
    () =>
      Object.values(replayState.gates).filter(
        (gate): gate is GateReplayRecord => gate.status === 'waiting',
      ),
    [replayState.gates],
  )
  const replayAtLive =
    replaySeq === null || replayUntilSeq >= replayMaxSeq
  const currentRunError =
    replayState.error ??
    (replayAtLive && currentRun?.status === 'failed'
      ? currentRun.error ?? '运行失败，但未记录错误详情'
      : null)

  function startInspectorResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    event.preventDefault()
    inspectorResizeRef.current = {
      startX: event.clientX,
      startWidth: inspectorWidth,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    setResizingInspector(true)
  }

  function moveInspectorResize(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = inspectorResizeRef.current
    if (!drag) return
    setInspectorWidth(drag.startWidth + drag.startX - event.clientX)
  }

  function finishInspectorResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!inspectorResizeRef.current) return
    inspectorResizeRef.current = null
    setResizingInspector(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  function resizeInspectorWithKeyboard(
    event: ReactKeyboardEvent<HTMLDivElement>,
  ) {
    const step = 12
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      setInspectorWidth(inspectorWidth + step)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      setInspectorWidth(inspectorWidth - step)
    } else if (event.key === 'Home') {
      event.preventDefault()
      setInspectorWidth(INSPECTOR_MIN_WIDTH)
    } else if (event.key === 'End') {
      event.preventDefault()
      setInspectorWidth(INSPECTOR_MAX_WIDTH)
    }
  }

  function startLeftResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    event.preventDefault()
    leftResizeRef.current = {
      startX: event.clientX,
      startWidth: leftPanelWidth,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    setResizingLeft(true)
  }

  function moveLeftResize(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = leftResizeRef.current
    if (!drag) return
    setLeftPanelWidth(drag.startWidth + event.clientX - drag.startX)
  }

  function finishLeftResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!leftResizeRef.current) return
    leftResizeRef.current = null
    setResizingLeft(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  function resizeLeftWithKeyboard(
    event: ReactKeyboardEvent<HTMLDivElement>,
  ) {
    const step = 12
    if (event.key === 'ArrowRight') {
      event.preventDefault()
      setLeftPanelWidth(leftPanelWidth + step)
    } else if (event.key === 'ArrowLeft') {
      event.preventDefault()
      setLeftPanelWidth(leftPanelWidth - step)
    } else if (event.key === 'Home') {
      event.preventDefault()
      setLeftPanelWidth(LEFT_PANEL_MIN_WIDTH)
    } else if (event.key === 'End') {
      event.preventDefault()
      setLeftPanelWidth(LEFT_PANEL_MAX_WIDTH)
    }
  }

  function startDrawerResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return
    event.preventDefault()
    drawerResizeRef.current = {
      startY: event.clientY,
      startHeight: drawerHeight,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    setResizingDrawer(true)
  }

  function moveDrawerResize(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = drawerResizeRef.current
    if (!drag) return
    setDrawerHeight(drag.startHeight + drag.startY - event.clientY)
  }

  function finishDrawerResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!drawerResizeRef.current) return
    drawerResizeRef.current = null
    setResizingDrawer(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  function resizeDrawerWithKeyboard(
    event: ReactKeyboardEvent<HTMLDivElement>,
  ) {
    const step = 12
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setDrawerHeight(drawerHeight + step)
    } else if (event.key === 'ArrowDown') {
      event.preventDefault()
      setDrawerHeight(drawerHeight - step)
    } else if (event.key === 'Home') {
      event.preventDefault()
      setDrawerHeight(DRAWER_MIN_HEIGHT)
    } else if (event.key === 'End') {
      event.preventDefault()
      setDrawerHeight(DRAWER_MAX_HEIGHT)
    }
  }

  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: api.listProjects,
  })
  const apiProfilesQuery = useQuery({
    queryKey: ['api-profiles'],
    queryFn: api.listApiProfiles,
  })
  const projectQuery = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => api.getProject(projectId!),
    enabled: Boolean(projectId),
  })
  const runsQuery = useQuery({
    queryKey: ['runs', projectId],
    queryFn: () => api.listRuns(projectId!),
    enabled: Boolean(projectId),
    refetchInterval: (query) => {
      const data = query.state.data
      return data?.some((run) =>
        ['queued', 'running', 'waiting'].includes(run.status),
      )
        ? 2_000
        : false
    },
  })

  const showNotice = useCallback(
    (tone: Notice['tone'], message: string) => {
      setNotice({ tone, message })
      if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current)
      noticeTimerRef.current = window.setTimeout(() => setNotice(null), 4_500)
    },
    [],
  )

  async function uploadRunFile(inputName: string, file: File) {
    if (!projectId) return
    const uploadId = (fileUploadIdsRef.current[inputName] ?? 0) + 1
    fileUploadIdsRef.current[inputName] = uploadId
    setRunInputs((current) => ({
      ...current,
      [inputName]: {
        name: file.name,
        path: '',
        size: file.size,
        uploading: true,
      },
    }))
    try {
      const uploaded = await api.uploadInputFile(projectId, inputName, file)
      if (fileUploadIdsRef.current[inputName] !== uploadId) return
      setRunInputs((current) => ({
        ...current,
        [inputName]: uploaded,
      }))
    } catch (error) {
      if (fileUploadIdsRef.current[inputName] !== uploadId) return
      setRunInputs((current) => {
        const next = { ...current }
        delete next[inputName]
        return next
      })
      showNotice(
        'error',
        error instanceof Error ? error.message : '文件上传失败',
      )
    }
  }

  function removeRunFile(inputName: string) {
    fileUploadIdsRef.current[inputName] =
      (fileUploadIdsRef.current[inputName] ?? 0) + 1
    setRunInputs((current) => {
      const next = { ...current }
      delete next[inputName]
      return next
    })
  }

  useEffect(() => {
    if (!projectId && projectsQuery.data?.length) {
      setProjectId(projectsQuery.data[0].id)
    }
  }, [projectId, projectsQuery.data])

  useEffect(() => {
    if (!projectId || !projectQuery.data) return
    if (draft?.id === projectQuery.data.id) return
    setDraft(projectQuery.data)
    setDirty(false)
    setSelection(null)
    setIssues([])
    setCurrentRun(null)
    setRunEvents([])
    setReplaySeq(null)
    setReplayPlaying(false)
    setGateComments({})
    setGeneratedFiles([])
    viewportRef.current = projectQuery.data.canvas.viewport
    fileUploadIdsRef.current = {}
    setRunInputs(initialRunInputs(projectQuery.data.sdl.workflow.inputs))
  }, [draft?.id, projectId, projectQuery.data])

  useEffect(() => {
    if (!projectId || !draft) return
    const profiles = draft.sdl.api_profiles
    const profileIds = profiles.map((profile) => profile.id)
    if (dirty) {
      setSecretStatuses((current) =>
        applyLibrarySecretStatuses(
          mergeSettledSecretStatuses(current, profileIds, []),
          profiles,
          librarySecretStatuses,
        ),
      )
      return
    }

    let cancelled = false
    Promise.allSettled(
      profiles.map(async (profile) => [
        profile.id,
        await api.secretStatus(projectId, profile.id),
      ] as const),
    )
      .then((results) => {
        if (cancelled) return
        setSecretStatuses((current) =>
          applyLibrarySecretStatuses(
            mergeSettledSecretStatuses(current, profileIds, results),
            profiles,
            librarySecretStatuses,
          ),
        )
      })
    return () => {
      cancelled = true
    }
  }, [
    draft?.sdl.api_profiles,
    dirty,
    librarySecretStatuses,
    projectId,
  ])

  useEffect(() => {
    const profiles = apiProfilesQuery.data
    if (!profiles) return
    setLibraryProfileDrafts((current) => {
      const next: Record<string, ApiProfile> = {}
      for (const profile of profiles) {
        next[profile.id] = current[profile.id] ?? profile
      }
      return next
    })

    let cancelled = false
    Promise.allSettled(
      profiles.map(async (profile) => [
        profile.id,
        await api.apiProfileSecretStatus(profile.id),
      ] as const),
    )
      .then((results) => {
        if (cancelled) return
        const profileIds = profiles.map((profile) => profile.id)
        setLibrarySecretStatuses((current) =>
          mergeSettledSecretStatuses(current, profileIds, results),
        )
      })
    return () => {
      cancelled = true
    }
  }, [apiProfilesQuery.data])

  useEffect(() => {
    if (!draft) {
      setNodes([])
      return
    }
    const canvasNodes = makeCanvasNodes(draft.sdl, draft.canvas)
    const nodeById = new Map(draft.sdl.agents.map((agent) => [agent.id, agent]))
    setNodes((current) => {
      const currentById = new Map(current.map((node) => [node.id, node]))
      return canvasNodes.map((node) => {
        const agent = nodeById.get(node.id)!
        const currentNode = currentById.get(node.id)
        const selected =
          selection?.kind === 'node' && selection.id === node.id
        const status = nodeStatuses[node.id] ?? 'idle'
        const data = {
          label: agent.name,
          agentId: agent.id,
          profile: agent.api_profile,
          inputs: agent.inputs.map((port) => port.name),
          outputs: agent.outputs.map((port) => port.name),
          status,
        }
        if (
          currentNode &&
          currentNode.position.x === node.x &&
          currentNode.position.y === node.y &&
          currentNode.width === node.width &&
          currentNode.height === node.height &&
          currentNode.selected === selected &&
          currentNode.data.label === data.label &&
          currentNode.data.agentId === data.agentId &&
          currentNode.data.profile === data.profile &&
          currentNode.data.status === data.status &&
          currentNode.data.inputs.join('\u0000') === data.inputs.join('\u0000') &&
          currentNode.data.outputs.join('\u0000') === data.outputs.join('\u0000')
        ) {
          return currentNode
        }
        return {
          ...currentNode,
          id: node.id,
          type: 'agent',
          position: { x: node.x, y: node.y },
          width: node.width,
          height: node.height,
          selected,
          data,
        }
      })
    })
  }, [draft, nodeStatuses, selection])

  useEffect(() => {
    if (!draft) {
      setEdges([])
      return
    }
    setEdges((current) => {
      const currentById = new Map(current.map((edge) => [edge.id, edge]))
      return draft.sdl.edges.map((workflowEdge) => {
        const currentEdge = currentById.get(workflowEdge.id)
        const traversals = edgeTraversals[workflowEdge.id] ?? 0
        const color =
          workflowEdge.type === 'FEEDBACK'
            ? '#d07932'
            : workflowEdge.type === 'LOOP'
              ? '#c14f3c'
              : workflowEdge.type === 'FAN_OUT'
                ? '#2f6f9f'
                : workflowEdge.type === 'AGGREGATE'
                  ? '#6f5b9a'
                  : workflowEdge.type === 'HUMAN_GATE'
                    ? '#a66d19'
            : workflowEdge.type === 'CONDITIONAL'
              ? '#98752f'
              : '#267f73'
        const selected =
          selection?.kind === 'edge' && selection.id === workflowEdge.id
        const animated = traversals > 0
        const strokeWidth = traversals > 0 ? 2.4 : 1.5
        const strokeDasharray =
          workflowEdge.type === 'FEEDBACK' || workflowEdge.type === 'LOOP'
            ? '7 4'
            : undefined
        if (
          currentEdge &&
          currentEdge.source === workflowEdge.from &&
          currentEdge.target === workflowEdge.to &&
          currentEdge.label === workflowEdge.type &&
          currentEdge.animated === animated &&
          currentEdge.selected === selected &&
          typeof currentEdge.markerEnd !== 'string' &&
          currentEdge.markerEnd?.type === MarkerType.ArrowClosed &&
          currentEdge.markerEnd?.color === color &&
          currentEdge.style?.stroke === color &&
          currentEdge.style?.strokeWidth === strokeWidth &&
          currentEdge.style?.strokeDasharray === strokeDasharray &&
          currentEdge.labelStyle?.fill === color
        ) {
          return currentEdge
        }
        return {
          ...currentEdge,
          id: workflowEdge.id,
          source: workflowEdge.from,
          target: workflowEdge.to,
          type: 'smoothstep',
          label: workflowEdge.type,
          animated,
          selected,
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color,
          },
          style: {
            stroke: color,
            strokeWidth,
            strokeDasharray,
          },
          labelStyle: {
            fill: color,
            fontSize: 10,
            fontWeight: 700,
          },
          labelBgStyle: {
            fill: '#fbfbf7',
            fillOpacity: 0.96,
          },
          labelBgPadding: [5, 3] as [number, number],
        }
      })
    })
  }, [draft, edgeTraversals, selection])

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close()
      if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current)
    }
  }, [])

  const selectedAgent = useMemo(
    () =>
      selection?.kind === 'node'
        ? draft?.sdl.agents.find((agent) => agent.id === selection.id) ?? null
        : null,
    [draft?.sdl.agents, selection],
  )
  const selectedEdge = useMemo(
    () =>
      selection?.kind === 'edge'
        ? draft?.sdl.edges.find((edge) => edge.id === selection.id) ?? null
        : null,
    [draft?.sdl.edges, selection],
  )
  const pathOptions = useMemo(
    () => (draft ? conditionPaths(draft.sdl) : []),
    [draft],
  )

  function updateDraft(updater: (current: ProjectDetail) => ProjectDetail) {
    setDraft((current) => {
      if (!current) return current
      setDirty(true)
      return updater(current)
    })
  }

  function updateWorkflow(
    updater: (sdl: SDL) => SDL,
  ) {
    updateDraft((current) => ({
      ...current,
      sdl: updater(current.sdl),
    }))
  }

  function updateAgent(agentId: string, updater: (agent: Agent) => Agent) {
    updateWorkflow((sdl) => ({
      ...sdl,
      agents: sdl.agents.map((agent) =>
        agent.id === agentId ? updater(agent) : agent,
      ),
    }))
  }

  function updateEdge(edgeId: string, updater: (edge: WorkflowEdge) => WorkflowEdge) {
    updateWorkflow((sdl) => ({
      ...sdl,
      edges: sdl.edges.map((edge) =>
        edge.id === edgeId ? updater(edge) : edge,
      ),
    }))
  }

  function updateProfile(
    profileId: string,
    updater: (profile: ApiProfile) => ApiProfile,
  ) {
    updateWorkflow((sdl) => ({
      ...sdl,
      api_profiles: sdl.api_profiles.map((profile) =>
        profile.id === profileId ? updater(profile) : profile,
      ),
    }))
  }

  async function saveProject(): Promise<ProjectDetail | null> {
    if (!draft) return null
    if (!dirty) return draft
    setSaving(true)
    try {
      const canvas = viewportRef.current
        ? { ...draft.canvas, viewport: viewportRef.current }
        : draft.canvas
      const updated = await api.updateProject(
        draft.id,
        draft.sdl,
        canvas,
        draft.revision,
      )
      setDraft(updated)
      viewportRef.current = updated.canvas.viewport
      setDirty(false)
      setIssues([])
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      showNotice('success', `已保存 revision ${updated.revision}`)
      return updated
    } catch (error) {
      const validationIssues = issueFromError(error)
      if (validationIssues.length) setIssues(validationIssues)
      showNotice(
        'error',
        error instanceof Error ? error.message : '保存失败',
      )
      return null
    } finally {
      setSaving(false)
    }
  }

  async function validateProject() {
    if (!draft) return false
    try {
      const result = await api.validate(draft.id, draft.sdl)
      setIssues(result.issues)
      setDrawerTab('issues')
      if (result.valid) {
        showNotice('success', 'SDL 校验通过')
      } else {
        showNotice('error', `发现 ${result.issues.length} 个校验问题`)
      }
      return result.valid
    } catch (error) {
      showNotice('error', error instanceof Error ? error.message : '校验失败')
      return false
    }
  }

  async function generateWorkflow() {
    if (!draft) return
    const saved = await saveProject()
    if (!saved) return
    try {
      const result = await api.generate(saved.id)
      setGeneratedFiles(result.files)
      setDrawerTab('result')
      showNotice(
        'success',
        result.cached ? `复用编译缓存 ${result.compiler_key}` : `已生成 ${result.compiler_key}`,
      )
      queryClient.invalidateQueries({ queryKey: ['project', saved.id] })
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    } catch (error) {
      const validationIssues = issueFromError(error)
      if (validationIssues.length) setIssues(validationIssues)
      showNotice('error', error instanceof Error ? error.message : '生成失败')
    }
  }

  const applyRunEvent = useCallback((event: RunEvent) => {
    setRunEvents((current) => dedupeAndSortRunEvents([...current, event]))
  }, [])

  const finishRun = useCallback(
    async (runId: string) => {
      try {
        const detail = await api.getRun(runId)
        setCurrentRun(detail)
        setRunEvents((current) =>
          current.length ? current : dedupeAndSortRunEvents(detail.events),
        )
      } catch {
        // The SSE event still carries the terminal state.
      }
      queryClient.invalidateQueries({ queryKey: ['runs', projectId] })
    },
    [projectId, queryClient],
  )

  const connectRunEvents = useCallback(
    (runId: string) => {
      eventSourceRef.current?.close()
      const source = new EventSource(api.eventUrl(runId))
      eventSourceRef.current = source
      for (const eventType of EVENT_TYPES) {
        source.addEventListener(eventType, (message) => {
          const event = parseEvent(message as MessageEvent)
          if (!event) return
          applyRunEvent(event)
          if (event.type === 'run_finished') {
            source.close()
            void finishRun(runId)
          }
        })
      }
      source.onerror = () => {
        void api
          .getRun(runId)
          .then((detail) => {
            if (TERMINAL_RUN_STATUSES.has(detail.status)) {
              source.close()
              setCurrentRun(detail)
              queryClient.invalidateQueries({ queryKey: ['runs', projectId] })
            }
          })
          .catch(() => undefined)
      }
    },
    [applyRunEvent, finishRun, projectId, queryClient],
  )

  async function startRun() {
    const saved = await saveProject()
    if (!saved) return
    const valid = await api
      .validate(saved.id, saved.sdl)
      .then((result) => {
        setIssues(result.issues)
        return result.valid
      })
      .catch(() => false)
    if (!valid) {
      setDrawerTab('issues')
      showNotice('error', 'SDL 未通过校验，无法启动运行')
      return
    }
    const fileInputs = saved.sdl.workflow.inputs.filter(
      (input) => input.type === 'FILE',
    )
    const pendingFiles = fileInputs.filter((input) => {
      const value = runInputs[input.name]
      return isUploadedRunFile(value) && value.uploading
    })
    if (pendingFiles.length) {
      showNotice(
        'error',
        `文件仍在处理中：${pendingFiles.map((input) => input.name).join('、')}`,
      )
      return
    }
    const missingFiles = fileInputs.filter((input) => {
      const value = runInputs[input.name]
      return (
        input.required &&
        (!isUploadedRunFile(value) || !value.path)
      )
    })
    if (missingFiles.length) {
      showNotice(
        'error',
        `请选择必填文件：${missingFiles.map((input) => input.name).join('、')}`,
      )
      return
    }

    const inputs: Record<string, unknown> = {}
    for (const input of saved.sdl.workflow.inputs) {
      const value = runInputs[input.name]
      if (input.type === 'FILE') {
        if (isUploadedRunFile(value) && value.path) {
          inputs[input.name] = {
            path: value.path,
            name: value.name,
            size: value.size,
          }
        }
      } else {
        inputs[input.name] = typeof value === 'string' ? value : ''
      }
    }

    eventSourceRef.current?.close()
    setRunEvents([])
    setReplaySeq(null)
    setReplayPlaying(false)
    setGateComments({})
    setDrawerTab('events')
    try {
      const summary = await api.startRun(saved.id, {
        inputs,
      })
      setCurrentRun(summary)
      connectRunEvents(summary.run_id)
      queryClient.invalidateQueries({ queryKey: ['runs', saved.id] })
    } catch (error) {
      showNotice('error', error instanceof Error ? error.message : '启动失败')
    }
  }

  async function cancelRun() {
    if (!currentRun) return
    try {
      const summary = await api.cancelRun(currentRun.run_id)
      setCurrentRun(summary)
      eventSourceRef.current?.close()
      showNotice('info', '运行已取消')
    } catch (error) {
      showNotice('error', error instanceof Error ? error.message : '取消失败')
    }
  }

  async function openRunFolder(run: RunSummary | RunDetail | null) {
    if (!run) return
    try {
      const result = await api.openRunFolder(run.run_id)
      showNotice('success', `已打开输出目录：${result.path}`)
    } catch (error) {
      showNotice(
        'error',
        error instanceof Error ? error.message : '无法打开输出目录',
      )
    }
  }

  async function loadRun(run: RunSummary) {
    eventSourceRef.current?.close()
    try {
      const detail = await api.getRun(run.run_id)
      setCurrentRun(detail)
      setRunEvents(detail.events)
      setReplaySeq(null)
      setReplayPlaying(false)
      setGateComments({})
      setDrawerTab('events')
      if (!TERMINAL_RUN_STATUSES.has(detail.status)) {
        connectRunEvents(detail.run_id)
      }
    } catch (error) {
      showNotice('error', error instanceof Error ? error.message : '运行记录读取失败')
    }
  }

  async function decideGate(
    gateId: string,
    decision: 'approve' | 'reject',
  ) {
    if (!currentRun) return
    setDecidingGate(gateId)
    try {
      await api.decideGate(
        currentRun.run_id,
        gateId,
        decision,
        gateComments[gateId],
      )
      const detail = await api.getRun(currentRun.run_id)
      setCurrentRun(detail)
      setGateComments((current) => {
        const next = { ...current }
        delete next[gateId]
        return next
      })
      showNotice(
        'success',
        decision === 'approve' ? '人工节点已通过' : '人工节点已拒绝',
      )
    } catch (error) {
      showNotice(
        'error',
        error instanceof Error ? error.message : '人工节点操作失败',
      )
    } finally {
      setDecidingGate(null)
    }
  }

  useEffect(() => {
    if (!replayPlaying) return
    const next = nextReplaySeq(runEvents, replayUntilSeq)
    if (next === null) {
      setReplayPlaying(false)
      return
    }
    const timer = window.setTimeout(() => setReplaySeq(next), replaySpeed)
    return () => window.clearTimeout(timer)
  }, [replayPlaying, replaySpeed, replayUntilSeq, runEvents])

  async function changeProject(nextProjectId: string) {
    if (nextProjectId === projectId) return
    if (dirty && !window.confirm('当前项目有未保存修改，仍要切换吗？')) return
    eventSourceRef.current?.close()
    setProjectId(nextProjectId)
    setDraft(null)
    setDirty(false)
    setSelection(null)
  }

  async function createProject() {
    const name = newProjectName.trim()
    if (!name) return
    try {
      const project = await api.createProject(name)
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      setNewProjectOpen(false)
      setNewProjectName('')
      setProjectId(project.id)
      setDraft(null)
      showNotice('success', '项目已创建')
    } catch (error) {
      showNotice('error', error instanceof Error ? error.message : '创建失败')
    }
  }

  async function deleteProject() {
    if (!draft) return
    if (!window.confirm(`删除项目“${draft.name}”及其运行记录？`)) return
    try {
      await api.deleteProject(draft.id)
      const remaining = projectsQuery.data?.filter((item) => item.id !== draft.id)
      setDraft(null)
      setProjectId(remaining?.[0]?.id ?? null)
      await queryClient.invalidateQueries({ queryKey: ['projects'] })
      showNotice('success', '项目已删除')
    } catch (error) {
      showNotice('error', error instanceof Error ? error.message : '删除失败')
    }
  }

  async function saveSecret(profileId: string) {
    if (!draft) return
    if (dirty) {
      showNotice('info', '请先保存 Profile 配置')
      return
    }
    const apiKey = secretDrafts[profileId]?.trim()
    if (!apiKey) return
    setKeySaving(profileId)
    try {
      const status = await api.putSecret(draft.id, profileId, apiKey)
      setSecretStatuses((current) => ({ ...current, [profileId]: status }))
      setSecretDrafts((current) => ({ ...current, [profileId]: '' }))
      showNotice('success', '密钥已安全保存')
    } catch (error) {
      showNotice('error', error instanceof Error ? error.message : '密钥保存失败')
    } finally {
      setKeySaving(null)
    }
  }

  async function deleteSecret(profileId: string) {
    if (!draft) return
    try {
      await api.deleteSecret(draft.id, profileId)
      setSecretStatuses((current) => ({
        ...current,
        [profileId]: {
          profile_id: profileId,
          configured: false,
          origin: current[profileId]?.origin ?? null,
        },
      }))
      showNotice('info', '密钥已移除')
    } catch (error) {
      showNotice('error', error instanceof Error ? error.message : '密钥删除失败')
    }
  }

  function updateLibraryProfileDraft(
    profileId: string,
    updater: (profile: ApiProfile) => ApiProfile,
  ) {
    setLibraryProfileDrafts((current) => {
      const profile = current[profileId]
      if (!profile) return current
      return {
        ...current,
        [profileId]: updater(profile),
      }
    })
  }

  async function createLibraryProfile() {
    const profileId = uniqueId(
      'profile',
      (apiProfilesQuery.data ?? []).map((profile) => profile.id),
    )
    const profile: ApiProfile = {
      id: profileId,
      library_id: null,
      name: '新常用 Profile',
      provider: 'openai_compatible',
      base_url: 'https://api.deepseek.com/v1',
      model: 'deepseek-chat',
      request: {
        timeout_seconds: 60,
        max_retries: 2,
        temperature: 0.2,
      },
      capabilities: {
        function_calling: false,
        vision: false,
        long_context: true,
        structured_output: true,
      },
    }
    setLibraryProfileSaving(profileId)
    try {
      const created = await api.createApiProfile(profile)
      setLibraryProfileDrafts((current) => ({
        ...current,
        [created.id]: created,
      }))
      await queryClient.invalidateQueries({ queryKey: ['api-profiles'] })
      showNotice('success', '常用 API Profile 已创建')
    } catch (error) {
      showNotice(
        'error',
        error instanceof Error ? error.message : '常用 Profile 创建失败',
      )
    } finally {
      setLibraryProfileSaving(null)
    }
  }

  async function saveLibraryProfile(profileId: string) {
    const profile = libraryProfileDrafts[profileId]
    if (!profile) return
    setLibraryProfileSaving(profileId)
    try {
      const saved = await api.updateApiProfile(profileId, {
        ...profile,
        library_id: null,
      })
      setLibraryProfileDrafts((current) => ({
        ...current,
        [profileId]: saved,
      }))
      await queryClient.invalidateQueries({ queryKey: ['api-profiles'] })
      showNotice('success', '常用 API Profile 已保存')
    } catch (error) {
      showNotice(
        'error',
        error instanceof Error ? error.message : '常用 Profile 保存失败',
      )
    } finally {
      setLibraryProfileSaving(null)
    }
  }

  async function deleteLibraryProfile(profileId: string) {
    const profile = libraryProfileDrafts[profileId]
    if (!profile) return
    if (
      !window.confirm(
        `从常用库删除“${profile.name || profile.id}”？已加入项目的副本不会被删除。`,
      )
    ) {
      return
    }
    setLibraryProfileDeleting(profileId)
    try {
      await api.deleteApiProfile(profileId)
      setLibraryProfileDrafts((current) => {
        const next = { ...current }
        delete next[profileId]
        return next
      })
      setLibrarySecretDrafts((current) => {
        const next = { ...current }
        delete next[profileId]
        return next
      })
      setLibrarySecretStatuses((current) => {
        const next = { ...current }
        delete next[profileId]
        return next
      })
      await queryClient.invalidateQueries({ queryKey: ['api-profiles'] })
      showNotice('success', '常用 API Profile 已删除')
    } catch (error) {
      showNotice(
        'error',
        error instanceof Error ? error.message : '常用 Profile 删除失败',
      )
    } finally {
      setLibraryProfileDeleting(null)
    }
  }

  async function saveLibrarySecret(profileId: string) {
    const apiKey = librarySecretDrafts[profileId]?.trim()
    if (!apiKey) return
    setLibraryKeySaving(profileId)
    try {
      const status = await api.putApiProfileSecret(profileId, apiKey)
      setLibrarySecretStatuses((current) => ({
        ...current,
        [profileId]: status,
      }))
      setLibrarySecretDrafts((current) => ({ ...current, [profileId]: '' }))
      showNotice('success', '常用库密钥已安全保存')
    } catch (error) {
      showNotice(
        'error',
        error instanceof Error ? error.message : '常用库密钥保存失败',
      )
    } finally {
      setLibraryKeySaving(null)
    }
  }

  async function deleteLibrarySecret(profileId: string) {
    try {
      await api.deleteApiProfileSecret(profileId)
      setLibrarySecretStatuses((current) => ({
        ...current,
        [profileId]: {
          profile_id: profileId,
          configured: false,
          origin: current[profileId]?.origin ?? null,
          scope: 'library',
        },
      }))
      showNotice('info', '常用库密钥已移除')
    } catch (error) {
      showNotice(
        'error',
        error instanceof Error ? error.message : '常用库密钥删除失败',
      )
    }
  }

  async function addLibraryProfileToProject(profileId: string) {
    if (!draft) return
    const profile = libraryProfileDrafts[profileId]
    if (!profile) return
    setLibraryProfileLinking(profileId)
    try {
      const projectProfileId = uniqueId(
        profile.id,
        draft.sdl.api_profiles.map((item) => item.id),
      )
      const linkedProfile: ApiProfile = {
        ...profile,
        id: projectProfileId,
        library_id: profile.id,
      }
      updateWorkflow((sdl) => ({
        ...sdl,
        api_profiles: [...sdl.api_profiles, linkedProfile],
      }))
      const libraryStatus = librarySecretStatuses[profile.id]
      if (libraryStatus) {
        setSecretStatuses((current) => ({
          ...current,
          [projectProfileId]: {
            ...libraryStatus,
            profile_id: projectProfileId,
            scope: 'library',
          },
        }))
      }
      const local =
        profile.base_url.includes('127.0.0.1') ||
        profile.base_url.includes('localhost')
      if (local || libraryStatus?.configured) {
        showNotice('success', '已加入当前项目，保存项目后生效')
      } else {
        showNotice(
          'info',
          '已加入当前项目；该常用库 Profile 尚未配置 API Key',
        )
      }
    } finally {
      setLibraryProfileLinking(null)
    }
  }

  async function saveProjectProfileToLibrary(profile: ApiProfile) {
    if (!draft || !libraryProfileDrafts) return
    const libraryProfiles = apiProfilesQuery.data ?? []
    const linkedLibraryProfile = profile.library_id
      ? libraryProfiles.find((item) => item.id === profile.library_id)
      : null
    const libraryProfileId =
      linkedLibraryProfile?.id ??
      uniqueId(
        profile.id,
        libraryProfiles.map((item) => item.id),
      )
    const payload: ApiProfile = {
      ...profile,
      id: libraryProfileId,
      library_id: null,
    }
    setLibraryProfileSaving(profile.id)
    try {
      const saved = linkedLibraryProfile
        ? await api.updateApiProfile(libraryProfileId, payload)
        : await api.createApiProfile(payload)
      setLibraryProfileDrafts((current) => ({
        ...current,
        [saved.id]: saved,
      }))
      updateProfile(profile.id, (current) => ({
        ...current,
        library_id: saved.id,
      }))
      await queryClient.invalidateQueries({ queryKey: ['api-profiles'] })

      const projectSecret = secretStatuses[profile.id]
      if (projectSecret?.configured && projectSecret.scope !== 'library') {
        try {
          const imported = await api.importProjectSecret(
            saved.id,
            draft.id,
            profile.id,
          )
          setLibrarySecretStatuses((current) => ({
            ...current,
            [saved.id]: imported,
          }))
        } catch {
          showNotice(
            'info',
            'Profile 已保存到常用库，但项目密钥未能导入；请在常用库中单独填写密钥',
          )
          return
        }
      }
      showNotice('success', 'Profile 已保存到常用库')
    } catch (error) {
      showNotice(
        'error',
        error instanceof Error ? error.message : '保存到常用库失败',
      )
    } finally {
      setLibraryProfileSaving(null)
    }
  }

  async function deleteApiProfile(profile: ApiProfile) {
    if (!draft) return
    const removal = removeApiProfile(draft.sdl, profile.id)
    if (removal.kind === 'blocked') {
      showNotice(
        'error',
        removal.reason === 'only-referenced-profile'
          ? `“${profile.name || profile.id}”正被 ${removal.referencedAgentIds.length} 个 Agent 使用。请先新增其他 Profile 再删除。`
          : 'Profile 不存在，请刷新项目后重试。',
      )
      return
    }

    const replacement = removal.replacementProfileId
      ? draft.sdl.api_profiles.find(
          (item) => item.id === removal.replacementProfileId,
        )
      : null
    const referenceMessage = replacement
      ? `，${removal.referencedAgentIds.length} 个 Agent 将切换到“${replacement.name || replacement.id}”`
      : ''
    const hasProjectSecret =
      secretStatuses[profile.id]?.configured &&
      secretStatuses[profile.id]?.scope !== 'library'
    const secretMessage = hasProjectSecret
      ? '，并移除已保存的密钥'
      : ''
    if (
      !window.confirm(
        `删除 Profile“${profile.name || profile.id}”${referenceMessage}${secretMessage}？`,
      )
    ) {
      return
    }

    setProfileDeleting(profile.id)
    try {
      if (hasProjectSecret) {
        await api.deleteSecret(draft.id, profile.id)
      }
      updateWorkflow((sdl) => {
        const result = removeApiProfile(sdl, profile.id)
        return result.kind === 'removed' ? result.sdl : sdl
      })
      setSecretStatuses((current) => {
        const next = { ...current }
        delete next[profile.id]
        return next
      })
      setSecretDrafts((current) => {
        const next = { ...current }
        delete next[profile.id]
        return next
      })
      showNotice('success', 'Profile 已从草稿中删除，保存项目后生效')
    } catch (error) {
      showNotice(
        'error',
        error instanceof Error ? error.message : 'Profile 删除失败',
      )
    } finally {
      setProfileDeleting(null)
    }
  }

  function onNodesChange(changes: NodeChange<AgentFlowNode>[]) {
    setNodes((current) => applyNodeChanges(changes, current))
  }

  function onEdgesChange(changes: EdgeChange<Edge>[]) {
    setEdges((current) => applyEdgeChanges(changes, current))
  }

  const onSelectionChange: OnSelectionChangeFunc<AgentFlowNode, Edge> =
    useCallback(
      ({ nodes: selectedNodes, edges: selectedEdges }) => {
        const nextSelection: Selection = selectedNodes[0]
          ? { kind: 'node', id: selectedNodes[0].id }
          : selectedEdges[0]
            ? { kind: 'edge', id: selectedEdges[0].id }
            : null

        setSelection((current) => {
          if (
            current?.kind === nextSelection?.kind &&
            current?.id === nextSelection?.id
          ) {
            return current
          }
          return nextSelection
        })
        if (nextSelection) setInspectorTab('selection')
      },
      [setInspectorTab],
    )

  function persistNodePositions(nextNodes: AgentFlowNode[]) {
    if (!draft) return
    const positions = new Map(
      nextNodes.map((node) => [
        node.id,
        {
          id: node.id,
          x: node.position.x,
          y: node.position.y,
          width: node.width ?? 238,
          height: node.height ?? 132,
        },
      ]),
    )
    updateDraft((current) => ({
      ...current,
      canvas: {
        ...current.canvas,
        nodes: current.canvas.nodes.map(
          (node) => positions.get(node.id) ?? node,
        ),
      },
    }))
  }

  function onNodeDragStop() {
    persistNodePositions(nodes)
  }

  function onMoveEnd(_: unknown, viewport: Viewport) {
    viewportRef.current = viewport
  }

  function onConnect(connection: Connection) {
    if (!draft || !connection.source || !connection.target) return
    if (connection.source === connection.target) return
    const source = draft.sdl.agents.find((agent) => agent.id === connection.source)
    const target = draft.sdl.agents.find((agent) => agent.id === connection.target)
    if (!source || !target) return
    const edgeId = uniqueId(
      `${source.id}-to-${target.id}`,
      draft.sdl.edges.map((edge) => edge.id),
    )
    updateDraft((current) => ({
      ...current,
      sdl: {
        ...current.sdl,
        edges: [
          ...current.sdl.edges,
          {
            id: edgeId,
            from: source.id,
            to: target.id,
            type: 'PIPE',
            mappings: firstCompatibleMapping(source, target),
          },
        ],
      },
      canvas: {
        ...current.canvas,
        edges: [
          ...current.canvas.edges,
          { id: edgeId, bend_points: [] },
        ],
      },
    }))
    setSelection({ kind: 'edge', id: edgeId })
    setInspectorTab('selection')
  }

  function onNodesDelete(deleted: AgentFlowNode[]) {
    if (!draft || !deleted.length) return
    const deletedIds = new Set(deleted.map((node) => node.id))
    updateDraft((current) => ({
      ...current,
      sdl: {
        ...current.sdl,
        agents: current.sdl.agents.filter((agent) => !deletedIds.has(agent.id)),
        start: current.sdl.start.filter((agentId) => !deletedIds.has(agentId)),
        edges: current.sdl.edges.filter(
          (edge) => !deletedIds.has(edge.from) && !deletedIds.has(edge.to),
        ),
        workflow: {
          ...current.sdl.workflow,
          outputs: current.sdl.workflow.outputs.filter(
            (output) => !deletedIds.has(output.agent),
          ),
        },
      },
      canvas: {
        ...current.canvas,
        nodes: current.canvas.nodes.filter(
          (node) => !deletedIds.has(node.id),
        ),
        edges: current.canvas.edges.filter((canvasEdge) => {
          const edge = current.sdl.edges.find(
            (item) => item.id === canvasEdge.id,
          )
          return edge && !deletedIds.has(edge.from) && !deletedIds.has(edge.to)
        }),
      },
    }))
    setSelection(null)
  }

  function onEdgesDelete(deleted: Edge[]) {
    if (!draft || !deleted.length) return
    const deletedIds = new Set(deleted.map((edge) => edge.id))
    updateDraft((current) => ({
      ...current,
      sdl: {
        ...current.sdl,
        edges: current.sdl.edges.filter((edge) => !deletedIds.has(edge.id)),
      },
      canvas: {
        ...current.canvas,
        edges: current.canvas.edges.filter(
          (edge) => !deletedIds.has(edge.id),
        ),
      },
    }))
    setSelection(null)
  }

  function deleteSelection() {
    if (selectedAgent) {
      if (
        !window.confirm(
          `删除 Agent “${selectedAgent.name}” 及与它相连的所有边？`,
        )
      ) {
        return
      }
      onNodesDelete(nodes.filter((node) => node.id === selectedAgent.id))
      return
    }
    if (selectedEdge) {
      if (!window.confirm(`删除连线 “${selectedEdge.id}”？`)) return
      onEdgesDelete(edges.filter((edge) => edge.id === selectedEdge.id))
    }
  }

  function addAgent() {
    if (!draft) return
    const id = uniqueId(
      'agent',
      draft.sdl.agents.map((agent) => agent.id),
    )
    const index = draft.sdl.agents.length
    const node: CanvasNode = {
      id,
      x: 100 + (index % 3) * 320,
      y: 120 + Math.floor(index / 3) * 210,
      width: 238,
      height: 132,
    }
    const agent: Agent = {
      id,
      name: '新 Agent',
      api_profile: draft.sdl.api_profiles[0]?.id ?? 'openai-main',
      system_prompt: '你是一个负责处理输入并返回结构化输出的 Agent。',
      inputs: [{ name: 'input', type: 'TEXT', required: true }],
      outputs: [{ name: 'output', type: 'TEXT' }],
      limits: { max_tokens: null, max_total_tokens: null },
      json_output: { enabled: false, template: {} },
      output_dir: null,
    }
    updateDraft((current) => ({
      ...current,
      sdl: {
        ...current.sdl,
        agents: [...current.sdl.agents, agent],
        start: current.sdl.start.length ? current.sdl.start : [id],
        workflow: {
          ...current.sdl.workflow,
          inputs:
            current.sdl.start.length > 0
              ? current.sdl.workflow.inputs
              : [
                  ...current.sdl.workflow.inputs,
                  { name: 'input', type: 'TEXT', required: true },
                ],
        },
      },
      canvas: {
        ...current.canvas,
        nodes: [...current.canvas.nodes, node],
      },
    }))
    setSelection({ kind: 'node', id })
    setInspectorTab('selection')
  }

  function setPort(agentId: string, side: 'inputs' | 'outputs', index: number, patch: Partial<Port>) {
    updateAgent(agentId, (agent) => ({
      ...agent,
      [side]: agent[side].map((port, portIndex) =>
        portIndex === index ? { ...port, ...patch } : port,
      ),
    }))
  }

  function addPort(agentId: string, side: 'inputs' | 'outputs') {
    updateAgent(agentId, (agent) => {
      const base = side === 'inputs' ? 'input' : 'output'
      const name = uniqueId(
        base,
        [...agent.inputs, ...agent.outputs].map((port) => port.name),
      ).replaceAll('-', '_')
      return {
        ...agent,
        [side]: [
          ...agent[side],
          {
            name,
            type: 'TEXT',
            ...(side === 'inputs' ? { required: true } : {}),
          },
        ],
      }
    })
  }

  function removePort(agentId: string, side: 'inputs' | 'outputs', index: number) {
    updateAgent(agentId, (agent) => ({
      ...agent,
      [side]: agent[side].filter((_, portIndex) => portIndex !== index),
    }))
  }

  function renderInspectorTabs() {
    const tabs: Array<{
      value: InspectorTab
      label: string
      icon: typeof Settings2
    }> = [
      { value: 'selection', label: '检查器', icon: Settings2 },
      { value: 'workflow', label: '工作流', icon: Workflow },
      { value: 'api', label: 'API', icon: KeyRound },
    ]
    return (
      <div className="inspector-tabs">
        {tabs.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            className={inspectorTab === value ? 'active' : ''}
            onClick={() => setInspectorTab(value)}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
        <button
          className="inspector-collapse"
          title="收起检查器"
          aria-label="收起检查器"
          aria-controls="inspector-panel"
          onClick={() => setInspectorOpen(false)}
        >
          <PanelRightClose size={15} />
        </button>
      </div>
    )
  }

  function renderPorts(agent: Agent, side: 'inputs' | 'outputs') {
    return (
      <div className="port-section">
        <div className="section-line">
          <span>{side === 'inputs' ? '输入端口' : '输出端口'}</span>
          <button className="icon-button" onClick={() => addPort(agent.id, side)}>
            <Plus size={14} />
          </button>
        </div>
        <div className="port-editor-list">
          {agent[side].map((port, index) => (
            <div className="port-editor-row" key={`${side}-${index}`}>
              <input
                value={port.name}
                onChange={(event) =>
                  setPort(agent.id, side, index, {
                    name: event.target.value
                      .toLowerCase()
                      .replaceAll(/[^a-z0-9_]/g, '_'),
                  })
                }
                aria-label="端口名称"
              />
              <select
                value={port.type}
                onChange={(event) =>
                  setPort(agent.id, side, index, {
                    type: event.target.value as PortType,
                  })
                }
              >
                {PORT_TYPES.map((type) => (
                  <option key={type}>{type}</option>
                ))}
              </select>
              {side === 'inputs' && (
                <input
                  className="port-check"
                  type="checkbox"
                  checked={port.required !== false}
                  title="必填"
                  onChange={(event) =>
                    setPort(agent.id, side, index, {
                      required: event.target.checked,
                    })
                  }
                />
              )}
              <button
                className="icon-button danger"
                onClick={() => removePort(agent.id, side, index)}
                title="删除端口"
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
      </div>
    )
  }

  function renderSelectionInspector() {
    if (selectedAgent) {
      const agent = selectedAgent
      return (
        <div className="inspector-body">
          <div className="inspector-heading">
            <div>
              <span className="eyebrow">AGENT</span>
              <h2>{agent.name}</h2>
            </div>
            <div className="inspector-heading-actions">
              <span className="id-chip">{agent.id}</span>
              <button
                className="small-button danger"
                title="删除所选 Agent"
                onClick={deleteSelection}
              >
                <Trash2 size={13} />
                删除
              </button>
            </div>
          </div>
          <label>
            名称
            <input
              value={agent.name}
              onChange={(event) =>
                updateAgent(agent.id, (current) => ({
                  ...current,
                  name: event.target.value,
                }))
              }
            />
          </label>
          <label>
            API Profile
            <select
              value={agent.api_profile}
              onChange={(event) =>
                updateAgent(agent.id, (current) => ({
                  ...current,
                  api_profile: event.target.value,
                }))
              }
            >
              {draft?.sdl.api_profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name || profile.id}
                </option>
              ))}
            </select>
          </label>
          <label>
            System Prompt
            <textarea
              rows={7}
              value={agent.system_prompt}
              onChange={(event) =>
                updateAgent(agent.id, (current) => ({
                  ...current,
                  system_prompt: event.target.value,
                }))
              }
            />
          </label>
          <label>
            单次 Max tokens
            <input
              type="number"
              min={1}
              placeholder="不限制"
              value={agent.limits.max_tokens ?? ''}
              onChange={(event) =>
                updateAgent(agent.id, (current) => ({
                  ...current,
                  limits: {
                    ...current.limits,
                    max_tokens: event.target.value
                      ? Number(event.target.value)
                      : null,
                  },
                }))
              }
            />
          </label>
          <label>
            累计 Max tokens
            <input
              type="number"
              min={1}
              placeholder="不限制"
              value={agent.limits.max_total_tokens ?? ''}
              onChange={(event) =>
                updateAgent(agent.id, (current) => ({
                  ...current,
                  limits: {
                    ...current.limits,
                    max_total_tokens: event.target.value
                      ? Number(event.target.value)
                      : null,
                  },
                }))
              }
            />
          </label>
          <label>
            产物目录
            <input
              value={agent.output_dir ?? ''}
              placeholder="artifacts"
              onChange={(event) =>
                updateAgent(agent.id, (current) => ({
                  ...current,
                  output_dir: event.target.value.trim() || null,
                }))
              }
            />
          </label>
          <JsonOutputEditor
            value={agent.json_output ?? { enabled: false, template: {} }}
            outputNames={agent.outputs.map((port) => port.name)}
            onChange={(jsonOutput) =>
              updateAgent(agent.id, (current) => ({
                ...current,
                json_output: jsonOutput,
              }))
            }
          />
          {renderPorts(agent, 'inputs')}
          {renderPorts(agent, 'outputs')}
        </div>
      )
    }

    if (selectedEdge) {
      const edge = selectedEdge
      const source = draft?.sdl.agents.find((agent) => agent.id === edge.from)
      const target = draft?.sdl.agents.find((agent) => agent.id === edge.to)
      const supportsCondition = [
        'CONDITIONAL',
        'FEEDBACK',
        'LOOP',
      ].includes(edge.type)
      const requiresCondition =
        edge.type === 'CONDITIONAL' || edge.type === 'LOOP'
      const supportsGuard = edge.type === 'FEEDBACK' || edge.type === 'LOOP'
      return (
        <div className="inspector-body">
          <div className="inspector-heading">
            <div>
              <span className="eyebrow">EDGE</span>
              <h2>{edge.id}</h2>
            </div>
            <div className="inspector-heading-actions">
              <span className="id-chip">
                {edge.from} → {edge.to}
              </span>
              <button
                className="small-button danger"
                title="删除所选连线"
                onClick={deleteSelection}
              >
                <Trash2 size={13} />
                删除
              </button>
            </div>
          </div>
          <label>
            类型
            <select
              value={edge.type}
              onChange={(event) => {
                const type = event.target.value as EdgeType
                const conditionAllowed =
                  type === 'CONDITIONAL' ||
                  type === 'FEEDBACK' ||
                  type === 'LOOP'
                const conditionRequired =
                  type === 'CONDITIONAL' || type === 'LOOP'
                const guardAllowed =
                  type === 'FEEDBACK' || type === 'LOOP'
                updateEdge(edge.id, (current) => ({
                  ...current,
                  type,
                  condition: conditionAllowed
                    ? current.condition ??
                      (conditionRequired
                        ? defaultCondition(draft!.sdl)
                        : null)
                    : null,
                  guard: guardAllowed
                    ? {
                        max_traversals:
                          current.guard?.max_traversals ??
                          (type === 'LOOP'
                            ? draft!.sdl.workflow.execution
                                .loop_limit_default
                            : draft!.sdl.workflow.execution
                                .feedback_limit_default),
                      }
                    : undefined,
                  aggregate:
                    type === 'AGGREGATE'
                      ? current.aggregate ?? { mode: 'all' }
                      : null,
                  human_gate:
                    type === 'HUMAN_GATE'
                      ? current.human_gate ?? defaultHumanGate()
                      : null,
                }))
              }}
            >
              {EDGE_TYPES.map((type) => (
                <option key={type}>{type}</option>
              ))}
            </select>
          </label>
          <div className="mapping-section">
            <div className="section-line">
              <span>数据映射</span>
              <button
                className="icon-button"
                onClick={() =>
                  updateEdge(edge.id, (current) => ({
                    ...current,
                    mappings: [
                      ...current.mappings,
                      {
                        from: source?.outputs[0]?.name ?? '',
                        to: target?.inputs[0]?.name ?? '',
                      },
                    ],
                  }))
                }
              >
                <Plus size={14} />
              </button>
            </div>
            {edge.mappings.map((mapping, index) => (
              <div className="mapping-row" key={`${edge.id}-map-${index}`}>
                <select
                  value={mapping.from}
                  onChange={(event) =>
                    updateEdge(edge.id, (current) => ({
                      ...current,
                      mappings: current.mappings.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, from: event.target.value }
                          : item,
                      ),
                    }))
                  }
                >
                  {source?.outputs.map((port) => (
                    <option key={port.name}>{port.name}</option>
                  ))}
                </select>
                <span>→</span>
                <select
                  value={mapping.to}
                  onChange={(event) =>
                    updateEdge(edge.id, (current) => ({
                      ...current,
                      mappings: current.mappings.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, to: event.target.value }
                          : item,
                      ),
                    }))
                  }
                >
                  {target?.inputs.map((port) => (
                    <option key={port.name}>{port.name}</option>
                  ))}
                </select>
                <button
                  className="icon-button danger"
                  onClick={() =>
                    updateEdge(edge.id, (current) => ({
                      ...current,
                      mappings: current.mappings.filter(
                        (_, itemIndex) => itemIndex !== index,
                      ),
                    }))
                  }
                  title="删除映射"
                >
                  <X size={13} />
                </button>
              </div>
            ))}
          </div>
          {supportsCondition && (
            <div className="condition-section">
              <div className="section-line">
                <span>触发条件</span>
                {!edge.condition && (
                  <button
                    className="small-button"
                    onClick={() =>
                      updateEdge(edge.id, (current) => ({
                        ...current,
                        condition: defaultCondition(draft!.sdl),
                      }))
                    }
                  >
                    <Plus size={13} />
                    条件
                  </button>
                )}
              </div>
              {edge.condition && (
                <ConditionEditor
                  value={edge.condition}
                  pathOptions={pathOptions}
                  createPredicate={() => defaultCondition(draft!.sdl)}
                  onChange={(condition) =>
                    updateEdge(edge.id, (current) => ({
                      ...current,
                      condition,
                    }))
                  }
                />
              )}
              <details className="advanced-json">
                <summary>高级条件 JSON</summary>
                <JsonConditionEditor
                  key={`${edge.id}-${JSON.stringify(edge.condition)}`}
                  value={edge.condition}
                  onChange={(condition) =>
                    updateEdge(edge.id, (current) => ({
                      ...current,
                      condition,
                    }))
                  }
                />
              </details>
            </div>
          )}
          {supportsGuard && (
            <label>
              {edge.type === 'LOOP' ? '最大循环次数' : '最大回注次数'}
              <input
                type="number"
                min={1}
                value={edge.guard?.max_traversals ?? ''}
                onChange={(event) =>
                  updateEdge(edge.id, (current) => ({
                    ...current,
                    guard: {
                      max_traversals: event.target.value
                        ? Number(event.target.value)
                        : null,
                    },
                  }))
                }
              />
            </label>
          )}
          {edge.type === 'AGGREGATE' && (
            <label>
              聚合模式
              <select
                value={edge.aggregate?.mode ?? 'all'}
                onChange={(event) =>
                  updateEdge(edge.id, (current) => ({
                    ...current,
                    aggregate: {
                      mode: event.target.value as 'all' | 'any',
                    },
                  }))
                }
              >
                <option value="all">全部输入到达</option>
                <option value="any">任一输入到达</option>
              </select>
            </label>
          )}
          {edge.type === 'HUMAN_GATE' && (
            <div className="gate-editor">
              <label>
                确认提示
                <textarea
                  rows={3}
                  value={edge.human_gate?.prompt ?? ''}
                  onChange={(event) =>
                    updateEdge(edge.id, (current) => ({
                      ...current,
                      human_gate: {
                        ...(current.human_gate ?? defaultHumanGate()),
                        prompt: event.target.value,
                      },
                    }))
                  }
                />
              </label>
              <div className="two-column-fields">
                <label>
                  超时秒数
                  <input
                    type="number"
                    min={1}
                    max={86400}
                    value={edge.human_gate?.timeout_seconds ?? 300}
                    onChange={(event) =>
                      updateEdge(edge.id, (current) => ({
                        ...current,
                        human_gate: {
                          ...(current.human_gate ?? defaultHumanGate()),
                          timeout_seconds: Number(event.target.value),
                        },
                      }))
                    }
                  />
                </label>
                <label>
                  拒绝行为
                  <select
                    value={edge.human_gate?.on_reject ?? 'fail'}
                    onChange={(event) =>
                      updateEdge(edge.id, (current) => ({
                        ...current,
                        human_gate: {
                          ...(current.human_gate ?? defaultHumanGate()),
                          on_reject: event.target.value as 'fail' | 'skip',
                        },
                      }))
                    }
                  >
                    <option value="fail">失败</option>
                    <option value="skip">跳过</option>
                  </select>
                </label>
              </div>
              <label>
                超时行为
                <select
                  value={edge.human_gate?.on_timeout ?? 'fail'}
                  onChange={(event) =>
                    updateEdge(edge.id, (current) => ({
                      ...current,
                      human_gate: {
                        ...(current.human_gate ?? defaultHumanGate()),
                        on_timeout: event.target.value as
                          | 'fail'
                          | 'approve'
                          | 'reject',
                      },
                    }))
                  }
                >
                  <option value="fail">失败</option>
                  <option value="approve">自动通过</option>
                  <option value="reject">自动拒绝</option>
                </select>
              </label>
            </div>
          )}
          {requiresCondition && !edge.condition && (
            <div className="inline-warning">
              此边需要条件，保存前请先补充。
            </div>
          )}
        </div>
      )
    }

    return (
      <div className="inspector-empty">
        <GitBranch size={28} />
        <span>未选择画布元素</span>
        <dl>
          <div>
            <dt>Agents</dt>
            <dd>{draft?.sdl.agents.length ?? 0}</dd>
          </div>
          <div>
            <dt>Edges</dt>
            <dd>{draft?.sdl.edges.length ?? 0}</dd>
          </div>
          <div>
            <dt>Revision</dt>
            <dd>{draft?.revision ?? '—'}</dd>
          </div>
        </dl>
      </div>
    )
  }

  function renderWorkflowInspector() {
    if (!draft) return null
    const workflowMeta = draft.sdl.workflow
    return (
      <div className="inspector-body">
        <div className="inspector-heading">
          <div>
            <span className="eyebrow">WORKFLOW</span>
            <h2>{workflowMeta.name}</h2>
          </div>
          <span className="id-chip">{workflowMeta.id}</span>
        </div>
        <label>
          名称
          <input
            value={workflowMeta.name}
            onChange={(event) =>
              updateWorkflow((sdl) => ({
                ...sdl,
                workflow: { ...sdl.workflow, name: event.target.value },
              }))
            }
          />
        </label>
        <label>
          全局背景 Prompt
          <textarea
            rows={6}
            value={workflowMeta.global_context?.text ?? ''}
            onChange={(event) =>
              updateWorkflow((sdl) => ({
                ...sdl,
                workflow: {
                  ...sdl.workflow,
                  global_context: {
                    text: event.target.value,
                    injection: sdl.workflow.global_context?.injection ?? 'prefix',
                    applies_to: sdl.workflow.global_context?.applies_to ?? 'all',
                  },
                },
              }))
            }
          />
        </label>
        <div className="two-column-fields">
          <label>
            注入位置
            <select
              value={workflowMeta.global_context?.injection ?? 'prefix'}
              onChange={(event) =>
                updateWorkflow((sdl) => ({
                  ...sdl,
                  workflow: {
                    ...sdl.workflow,
                    global_context: {
                      text: sdl.workflow.global_context?.text ?? '',
                      injection: event.target.value as 'prefix' | 'suffix',
                      applies_to: sdl.workflow.global_context?.applies_to ?? 'all',
                    },
                  },
                }))
              }
            >
              <option value="prefix">Prefix</option>
              <option value="suffix">Suffix</option>
            </select>
          </label>
          <label>
            Feedback 默认上限
            <input
              type="number"
              min={1}
              value={workflowMeta.execution.feedback_limit_default}
              onChange={(event) =>
                updateWorkflow((sdl) => ({
                  ...sdl,
                  workflow: {
                    ...sdl.workflow,
                    execution: {
                      ...sdl.workflow.execution,
                      feedback_limit_default: Number(event.target.value),
                    },
                  },
                }))
              }
            />
          </label>
          <label>
            Loop 默认上限
            <input
              type="number"
              min={1}
              value={workflowMeta.execution.loop_limit_default}
              onChange={(event) =>
                updateWorkflow((sdl) => ({
                  ...sdl,
                  workflow: {
                    ...sdl.workflow,
                    execution: {
                      ...sdl.workflow.execution,
                      loop_limit_default: Number(event.target.value),
                    },
                  },
                }))
              }
            />
          </label>
          <label>
            最大步骤
            <input
              type="number"
              min={1}
              value={workflowMeta.execution.max_steps}
              onChange={(event) =>
                updateWorkflow((sdl) => ({
                  ...sdl,
                  workflow: {
                    ...sdl.workflow,
                    execution: {
                      ...sdl.workflow.execution,
                      max_steps: Number(event.target.value),
                    },
                  },
                }))
              }
            />
          </label>
          <label>
            最大并行数
            <input
              type="number"
              min={1}
              max={64}
              value={workflowMeta.execution.max_parallel}
              onChange={(event) =>
                updateWorkflow((sdl) => ({
                  ...sdl,
                  workflow: {
                    ...sdl.workflow,
                    execution: {
                      ...sdl.workflow.execution,
                      max_parallel: Number(event.target.value),
                    },
                  },
                }))
              }
            />
          </label>
          <label>
            累计 Token 上限
            <input
              type="number"
              min={1}
              placeholder="不限制"
              value={workflowMeta.execution.max_total_tokens ?? ''}
              onChange={(event) =>
                updateWorkflow((sdl) => ({
                  ...sdl,
                  workflow: {
                    ...sdl.workflow,
                    execution: {
                      ...sdl.workflow.execution,
                      max_total_tokens: event.target.value
                        ? Number(event.target.value)
                        : null,
                    },
                  },
                }))
              }
            />
          </label>
        </div>
        <div className="port-section">
          <div className="section-line">
            <span>工作流输入</span>
            <button
              className="icon-button"
              onClick={() =>
                updateWorkflow((sdl) => ({
                  ...sdl,
                  workflow: {
                    ...sdl.workflow,
                    inputs: [
                      ...sdl.workflow.inputs,
                      {
                        name: uniqueId(
                          'input',
                          sdl.workflow.inputs.map((input) => input.name),
                        ).replaceAll('-', '_'),
                        type: 'TEXT',
                        required: true,
                      },
                    ],
                  },
                }))
              }
            >
              <Plus size={14} />
            </button>
          </div>
          {workflowMeta.inputs.map((input, index) => (
            <div className="port-editor-row" key={input.name}>
              <input
                value={input.name}
                onChange={(event) =>
                  updateWorkflow((sdl) => ({
                    ...sdl,
                    workflow: {
                      ...sdl.workflow,
                      inputs: sdl.workflow.inputs.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, name: event.target.value }
                          : item,
                      ),
                    },
                  }))
                }
              />
              <select
                value={input.type}
                onChange={(event) =>
                  updateWorkflow((sdl) => ({
                    ...sdl,
                    workflow: {
                      ...sdl.workflow,
                      inputs: sdl.workflow.inputs.map((item, itemIndex) =>
                        itemIndex === index
                          ? { ...item, type: event.target.value as PortType }
                          : item,
                      ),
                    },
                  }))
                }
              >
                {PORT_TYPES.map((type) => (
                  <option key={type}>{type}</option>
                ))}
              </select>
              <button
                className="icon-button danger"
                onClick={() =>
                  updateWorkflow((sdl) => ({
                    ...sdl,
                    workflow: {
                      ...sdl.workflow,
                      inputs: sdl.workflow.inputs.filter(
                        (_, itemIndex) => itemIndex !== index,
                      ),
                    },
                  }))
                }
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
        <div className="port-section">
          <div className="section-line">
            <span>工作流输出</span>
            <button
              className="icon-button"
              onClick={() => {
                const firstAgent = draft.sdl.agents[0]
                if (!firstAgent) return
                updateWorkflow((sdl) => ({
                  ...sdl,
                  workflow: {
                    ...sdl.workflow,
                    outputs: [
                      ...sdl.workflow.outputs,
                      {
                        name: uniqueId(
                          'output',
                          sdl.workflow.outputs.map((output) => output.name),
                        ).replaceAll('-', '_'),
                        agent: firstAgent.id,
                        port: firstAgent.outputs[0]?.name ?? '',
                      },
                    ],
                  },
                }))
              }}
            >
              <Plus size={14} />
            </button>
          </div>
          {workflowMeta.outputs.map((output, index) => {
            const agent = draft.sdl.agents.find(
              (item) => item.id === output.agent,
            )
            return (
              <div className="output-editor-row" key={`${output.name}-${index}`}>
                <input
                  value={output.name}
                  onChange={(event) =>
                    updateWorkflow((sdl) => ({
                      ...sdl,
                      workflow: {
                        ...sdl.workflow,
                        outputs: sdl.workflow.outputs.map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, name: event.target.value }
                            : item,
                        ),
                      },
                    }))
                  }
                />
                <select
                  value={output.agent}
                  onChange={(event) =>
                    updateWorkflow((sdl) => ({
                      ...sdl,
                      workflow: {
                        ...sdl.workflow,
                        outputs: sdl.workflow.outputs.map((item, itemIndex) =>
                          itemIndex === index
                            ? {
                                ...item,
                                agent: event.target.value,
                                port:
                                  sdl.agents.find(
                                    (candidate) =>
                                      candidate.id === event.target.value,
                                  )?.outputs[0]?.name ?? '',
                              }
                            : item,
                        ),
                      },
                    }))
                  }
                >
                  {draft.sdl.agents.map((item) => (
                    <option key={item.id}>{item.id}</option>
                  ))}
                </select>
                <select
                  value={output.port}
                  onChange={(event) =>
                    updateWorkflow((sdl) => ({
                      ...sdl,
                      workflow: {
                        ...sdl.workflow,
                        outputs: sdl.workflow.outputs.map((item, itemIndex) =>
                          itemIndex === index
                            ? { ...item, port: event.target.value }
                            : item,
                        ),
                      },
                    }))
                  }
                >
                  {agent?.outputs.map((port) => (
                    <option key={port.name}>{port.name}</option>
                  ))}
                </select>
                <button
                  className="icon-button danger"
                  onClick={() =>
                    updateWorkflow((sdl) => ({
                      ...sdl,
                      workflow: {
                        ...sdl.workflow,
                        outputs: sdl.workflow.outputs.filter(
                          (_, itemIndex) => itemIndex !== index,
                        ),
                      },
                    }))
                  }
                >
                  <X size={13} />
                </button>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  function renderApiInspector() {
    if (!draft) return null
    const libraryProfiles = apiProfilesQuery.data ?? []
    return (
      <div className="inspector-body">
        <div className="inspector-heading">
          <div>
            <span className="eyebrow">OPENAI COMPATIBLE</span>
            <h2>API Profiles</h2>
          </div>
          <button
            className="icon-button"
            title="刷新常用库"
            disabled={apiProfilesQuery.isFetching}
            onClick={() => queryClient.invalidateQueries({ queryKey: ['api-profiles'] })}
          >
            <RefreshCw
              size={14}
              className={apiProfilesQuery.isFetching ? 'spin' : undefined}
            />
          </button>
        </div>

        <section className="api-profile-group">
          <div className="section-line api-group-heading">
            <span>常用 API 库</span>
            <button
              className="icon-button"
              title="新增常用 Profile"
              disabled={libraryProfileSaving !== null}
              onClick={createLibraryProfile}
            >
              {libraryProfileSaving &&
              !libraryProfiles.some(
                (profile) => profile.id === libraryProfileSaving,
              ) ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <Plus size={15} />
              )}
            </button>
          </div>
          <p className="api-group-help">密钥独立保存在系统凭据库，可被多个项目复用。</p>
          {apiProfilesQuery.isLoading && (
            <div className="api-group-state">
              <LoaderCircle size={14} className="spin" />
              读取常用库
            </div>
          )}
          {apiProfilesQuery.error && (
            <div className="api-group-state error">
              <CircleAlert size={14} />
              {apiProfilesQuery.error.message}
            </div>
          )}
          {!apiProfilesQuery.isLoading &&
            !apiProfilesQuery.error &&
            libraryProfiles.length === 0 && (
              <div className="api-group-state">
                <KeyRound size={14} />
                尚无常用 Profile
              </div>
            )}
          {libraryProfiles.map((serverProfile) => {
            const profile =
              libraryProfileDrafts[serverProfile.id] ?? serverProfile
            const status = librarySecretStatuses[serverProfile.id]
            const local =
              profile.base_url.includes('127.0.0.1') ||
              profile.base_url.includes('localhost')
            const linked = draft.sdl.api_profiles.some(
              (item) => item.library_id === serverProfile.id,
            )
            const saving = libraryProfileSaving === serverProfile.id
            const deleting = libraryProfileDeleting === serverProfile.id
            return (
              <section className="profile-card library-profile-card" key={serverProfile.id}>
                <div className="profile-title">
                  <div className="profile-title-copy">
                    <strong>{profile.name || profile.id}</strong>
                    <span>{profile.id}</span>
                  </div>
                  <div className="profile-title-actions">
                    <span
                      className={classNames(
                        'credential-badge',
                        status?.configured || local ? 'ready' : 'missing',
                      )}
                    >
                      {status?.configured || local ? (
                        <CheckCircle2 size={12} />
                      ) : (
                        <CircleAlert size={12} />
                      )}
                      {local
                        ? '本地接口'
                        : status?.configured
                          ? '常用库密钥'
                          : '缺少密钥'}
                    </span>
                    <button
                      className="icon-button danger"
                      aria-label="删除常用 Profile"
                      disabled={deleting || saving}
                      onClick={() => deleteLibraryProfile(serverProfile.id)}
                      title="从常用库删除"
                    >
                      {deleting ? (
                        <LoaderCircle size={14} className="spin" />
                      ) : (
                        <Trash2 size={14} />
                      )}
                    </button>
                  </div>
                </div>
                <label>
                  显示名称
                  <input
                    value={profile.name ?? ''}
                    onChange={(event) =>
                      updateLibraryProfileDraft(serverProfile.id, (current) => ({
                        ...current,
                        name: event.target.value,
                      }))
                    }
                  />
                </label>
                <label>
                  Base URL
                  <input
                    value={profile.base_url}
                    onChange={(event) =>
                      updateLibraryProfileDraft(serverProfile.id, (current) => ({
                        ...current,
                        base_url: event.target.value,
                      }))
                    }
                  />
                </label>
                <div className="two-column-fields">
                  <label>
                    Model
                    <input
                      value={profile.model}
                      onChange={(event) =>
                        updateLibraryProfileDraft(serverProfile.id, (current) => ({
                          ...current,
                          model: event.target.value,
                        }))
                      }
                    />
                  </label>
                  <label>
                    Temperature
                    <input
                      type="number"
                      step="0.1"
                      min={0}
                      max={2}
                      value={profile.request.temperature}
                      onChange={(event) =>
                        updateLibraryProfileDraft(serverProfile.id, (current) => ({
                          ...current,
                          request: {
                            ...current.request,
                            temperature: Number(event.target.value),
                          },
                        }))
                      }
                    />
                  </label>
                </div>
                {!local && (
                  <div className="secret-row">
                    <input
                      type="password"
                      value={librarySecretDrafts[serverProfile.id] ?? ''}
                      placeholder={
                        status?.configured ? '输入新密钥以替换' : '输入 API Key'
                      }
                      onChange={(event) =>
                        setLibrarySecretDrafts((current) => ({
                          ...current,
                          [serverProfile.id]: event.target.value,
                        }))
                      }
                    />
                    <button
                      className="small-button accent"
                      disabled={
                        deleting ||
                        saving ||
                        libraryKeySaving === serverProfile.id
                      }
                      onClick={() => saveLibrarySecret(serverProfile.id)}
                      title="保存常用库密钥"
                    >
                      {libraryKeySaving === serverProfile.id ? (
                        <LoaderCircle size={13} className="spin" />
                      ) : (
                        <KeyRound size={13} />
                      )}
                      保存
                    </button>
                    {status?.configured && (
                      <button
                        className="icon-button danger"
                        disabled={deleting || saving}
                        onClick={() => deleteLibrarySecret(serverProfile.id)}
                        title="删除常用库密钥"
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                )}
                <div className="capability-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={profile.capabilities.structured_output}
                      onChange={(event) =>
                        updateLibraryProfileDraft(serverProfile.id, (current) => ({
                          ...current,
                          capabilities: {
                            ...current.capabilities,
                            structured_output: event.target.checked,
                          },
                        }))
                      }
                    />
                    Structured JSON
                  </label>
                </div>
                <div className="profile-card-actions">
                  <button
                    className="small-button"
                    disabled={deleting || saving}
                    onClick={() => saveLibraryProfile(serverProfile.id)}
                  >
                    {saving ? (
                      <LoaderCircle size={13} className="spin" />
                    ) : (
                      <Save size={13} />
                    )}
                    保存配置
                  </button>
                  <button
                    className="small-button accent"
                    disabled={
                      linked ||
                      deleting ||
                      saving ||
                      libraryProfileLinking === serverProfile.id
                    }
                    onClick={() => addLibraryProfileToProject(serverProfile.id)}
                    title={linked ? '当前项目已使用该配置' : '加入当前项目'}
                  >
                    {libraryProfileLinking === serverProfile.id ? (
                      <LoaderCircle size={13} className="spin" />
                    ) : linked ? (
                      <Check size={13} />
                    ) : (
                      <Plus size={13} />
                    )}
                    {linked ? '已加入' : '加入项目'}
                  </button>
                </div>
              </section>
            )
          })}
        </section>

        <section className="api-profile-group">
          <div className="section-line api-group-heading">
            <span>当前项目</span>
            <button
              className="icon-button"
              title="新增项目 Profile"
              onClick={() =>
                updateWorkflow((sdl) => {
                  const id = uniqueId(
                    'profile',
                    sdl.api_profiles.map((profile) => profile.id),
                  )
                  return {
                    ...sdl,
                    api_profiles: [
                      ...sdl.api_profiles,
                      {
                        id,
                        name: '新 Profile',
                        provider: 'openai_compatible',
                        base_url: 'http://127.0.0.1:8001/v1',
                        model: 'local-model',
                        request: {
                          timeout_seconds: 60,
                          max_retries: 2,
                          temperature: 0.2,
                        },
                        capabilities: {
                          function_calling: false,
                          vision: false,
                          long_context: false,
                          structured_output: true,
                        },
                      },
                    ],
                  }
                })
              }
            >
              <Plus size={15} />
            </button>
          </div>
          {draft.sdl.api_profiles.length === 0 && (
            <div className="api-group-state">
              <CircleAlert size={14} />
              当前项目尚未选择 API Profile
            </div>
          )}
          {draft.sdl.api_profiles.map((profile) => {
            const local =
              profile.base_url.includes('127.0.0.1') ||
              profile.base_url.includes('localhost')
            const status = secretStatuses[profile.id]
            const usingLibrarySecret =
              status?.configured && status.scope === 'library'
            return (
              <section className="profile-card" key={profile.id}>
                <div className="profile-title">
                  <div className="profile-title-copy">
                    <strong>{profile.name || profile.id}</strong>
                    <span>{profile.id}</span>
                    {profile.library_id && (
                      <small className="profile-library-link">
                        常用库：{profile.library_id}
                      </small>
                    )}
                  </div>
                  <div className="profile-title-actions">
                    <span
                      className={classNames(
                        'credential-badge',
                        status?.configured || local ? 'ready' : 'missing',
                      )}
                    >
                      {status?.configured || local ? (
                        <CheckCircle2 size={12} />
                      ) : (
                        <CircleAlert size={12} />
                      )}
                      {local
                        ? '本地接口'
                        : usingLibrarySecret
                          ? '常用库密钥'
                          : status?.configured
                            ? '项目密钥'
                            : '缺少密钥'}
                    </span>
                    <button
                      className="icon-button danger"
                      aria-label="删除 Profile"
                      disabled={
                        profileDeleting !== null || keySaving === profile.id
                      }
                      onClick={() => deleteApiProfile(profile)}
                      title="删除 Profile"
                    >
                      {profileDeleting === profile.id ? (
                        <LoaderCircle size={14} className="spin" />
                      ) : (
                        <Trash2 size={14} />
                      )}
                    </button>
                  </div>
                </div>
                <div className="profile-card-actions">
                  <button
                    className="small-button"
                    disabled={
                      libraryProfileSaving === profile.id ||
                      libraryProfileDeleting !== null
                    }
                    onClick={() => saveProjectProfileToLibrary(profile)}
                    title="将当前配置保存为可复用 API"
                  >
                    {libraryProfileSaving === profile.id ? (
                      <LoaderCircle size={13} className="spin" />
                    ) : (
                      <Save size={13} />
                    )}
                    {profile.library_id ? '更新常用库' : '保存到常用库'}
                  </button>
                  {profile.library_id && (
                    <span className="profile-linked-note">
                      <Check size={12} />
                      已关联
                    </span>
                  )}
                </div>
                <label>
                  显示名称
                  <input
                    value={profile.name ?? ''}
                    onChange={(event) =>
                      updateProfile(profile.id, (current) => ({
                        ...current,
                        name: event.target.value,
                      }))
                    }
                  />
                </label>
                <label>
                  Base URL
                  <input
                    value={profile.base_url}
                    onChange={(event) =>
                      updateProfile(profile.id, (current) => ({
                        ...current,
                        base_url: event.target.value,
                      }))
                    }
                  />
                </label>
                <div className="two-column-fields">
                  <label>
                    Model
                    <input
                      value={profile.model}
                      onChange={(event) =>
                        updateProfile(profile.id, (current) => ({
                          ...current,
                          model: event.target.value,
                        }))
                      }
                    />
                  </label>
                  <label>
                    Temperature
                    <input
                      type="number"
                      step="0.1"
                      min={0}
                      max={2}
                      value={profile.request.temperature}
                      onChange={(event) =>
                        updateProfile(profile.id, (current) => ({
                          ...current,
                          request: {
                            ...current.request,
                            temperature: Number(event.target.value),
                          },
                        }))
                      }
                    />
                  </label>
                </div>
                {!local && (
                  <div className="secret-row">
                    <input
                      type="password"
                      value={secretDrafts[profile.id] ?? ''}
                      placeholder={
                        status?.configured
                          ? usingLibrarySecret
                            ? '输入项目密钥以覆盖常用库密钥'
                            : '输入新密钥以替换'
                          : '输入 API Key'
                      }
                      onChange={(event) =>
                        setSecretDrafts((current) => ({
                          ...current,
                          [profile.id]: event.target.value,
                        }))
                      }
                    />
                    <button
                      className="small-button accent"
                      disabled={
                        dirty ||
                        keySaving === profile.id ||
                        profileDeleting === profile.id
                      }
                      onClick={() => saveSecret(profile.id)}
                      title={dirty ? '先保存 Profile 配置' : '保存项目密钥'}
                    >
                      {keySaving === profile.id ? (
                        <LoaderCircle size={13} className="spin" />
                      ) : (
                        <KeyRound size={13} />
                      )}
                      保存
                    </button>
                    {status?.configured && !usingLibrarySecret && (
                      <button
                        className="icon-button danger"
                        disabled={profileDeleting === profile.id}
                        onClick={() => deleteSecret(profile.id)}
                        title="删除项目密钥"
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                )}
                <div className="capability-row">
                  <label>
                    <input
                      type="checkbox"
                      checked={profile.capabilities.structured_output}
                      onChange={(event) =>
                        updateProfile(profile.id, (current) => ({
                          ...current,
                          capabilities: {
                            ...current.capabilities,
                            structured_output: event.target.checked,
                          },
                        }))
                      }
                    />
                    Structured JSON
                  </label>
                </div>
              </section>
            )
          })}
        </section>
      </div>
    )
  }

  const currentRunStatus = currentRun?.status
  const isRunActive =
    currentRunStatus === 'queued' ||
    currentRunStatus === 'running' ||
    currentRunStatus === 'waiting'
  const canRun = Boolean(draft) && !isRunActive

  if (projectsQuery.isLoading) {
    return (
      <div className="boot-screen">
        <div className="brand-mark">LL</div>
        <LoaderCircle className="spin" size={18} />
      </div>
    )
  }

  if (projectsQuery.error) {
    return (
      <div className="boot-screen">
        <CircleAlert size={22} />
        <strong>无法连接 LoomLane API</strong>
        <span>{projectsQuery.error.message}</span>
      </div>
    )
  }

  return (
    <div
      className={classNames(
        'app-shell',
        drawerOpen ? 'drawer-open' : 'drawer-closed',
        leftPanelOpen ? 'left-open' : 'left-closed',
        inspectorOpen ? 'inspector-open' : 'inspector-closed',
        resizingLeft && 'left-resizing',
        resizingDrawer && 'drawer-resizing',
        resizingInspector && 'inspector-resizing',
      )}
      style={
        {
          '--left-width': `${leftPanelWidth}px`,
          '--drawer-height': `${drawerHeight}px`,
          '--inspector-width': `${inspectorWidth}px`,
        } as CSSProperties
      }
    >
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">LL</span>
          <span>LoomLane</span>
        </div>
        <div className="workflow-title">
          <strong>{draft?.sdl.workflow.name ?? '加载中'}</strong>
          <span>
            rev {draft?.revision ?? '—'}
            {dirty ? ' · 未保存' : ''}
          </span>
        </div>
        <div className="topbar-actions">
          <button className="toolbar-button" onClick={validateProject}>
            <CheckCircle2 size={15} />
            校验
          </button>
          <button className="toolbar-button" onClick={generateWorkflow}>
            <Braces size={15} />
            生成
          </button>
          <button
            className="toolbar-button"
            disabled={!dirty || saving}
            onClick={saveProject}
          >
            {saving ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Save size={15} />
            )}
            保存
          </button>
          {isRunActive ? (
            <button className="run-button cancel" onClick={cancelRun}>
              <Square size={14} fill="currentColor" />
              停止
            </button>
          ) : (
            <button
              className="run-button"
              disabled={!canRun}
              onClick={startRun}
            >
              <Play size={14} fill="currentColor" />
              运行
            </button>
          )}
        </div>
      </header>

      {notice && (
        <div className={`notice notice-${notice.tone}`}>
          {notice.tone === 'error' ? (
            <CircleAlert size={15} />
          ) : notice.tone === 'success' ? (
            <CheckCircle2 size={15} />
          ) : (
            <Activity size={15} />
          )}
          <span>{notice.message}</span>
          <button className="icon-button" onClick={() => setNotice(null)}>
            <X size={13} />
          </button>
        </div>
      )}

      <aside
        id="left-panel"
        className={classNames('left-panel', !leftPanelOpen && 'collapsed')}
      >
        {leftPanelOpen ? (
          <>
            <div
              className="left-resize-handle"
              role="separator"
              aria-label="调整导航栏宽度"
              aria-orientation="vertical"
              aria-valuemin={LEFT_PANEL_MIN_WIDTH}
              aria-valuemax={LEFT_PANEL_MAX_WIDTH}
              aria-valuenow={leftPanelWidth}
              tabIndex={0}
              onKeyDown={resizeLeftWithKeyboard}
              onPointerDown={startLeftResize}
              onPointerMove={moveLeftResize}
              onPointerUp={finishLeftResize}
              onPointerCancel={finishLeftResize}
              onLostPointerCapture={finishLeftResize}
            />
            <section className="panel-section projects-section">
              <div className="panel-heading">
                <span>项目</span>
                <div className="panel-heading-actions">
                  <button
                    className="icon-button"
                    title="新建项目"
                    onClick={() => setNewProjectOpen(true)}
                  >
                    <FolderPlus size={15} />
                  </button>
                  <button
                    className="icon-button"
                    title="收起导航栏"
                    aria-controls="left-panel"
                    onClick={() => setLeftPanelOpen(false)}
                  >
                    <PanelLeftClose size={15} />
                  </button>
                </div>
              </div>
              <div className="project-list">
                {projectsQuery.data?.map((project) => (
                  <button
                    key={project.id}
                    className={project.id === projectId ? 'active' : ''}
                    onClick={() => changeProject(project.id)}
                  >
                    <Folder size={14} />
                    <span>
                      <strong>{project.name}</strong>
                      <small>r{project.revision}</small>
                    </span>
                  </button>
                ))}
              </div>
              {newProjectOpen && (
                <div className="inline-create">
                  <input
                    autoFocus
                    value={newProjectName}
                    placeholder="项目名称"
                    onChange={(event) => setNewProjectName(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void createProject()
                      if (event.key === 'Escape') setNewProjectOpen(false)
                    }}
                  />
                  <button className="icon-button" onClick={createProject}>
                    <Check size={14} />
                  </button>
                  <button
                    className="icon-button"
                    onClick={() => setNewProjectOpen(false)}
                  >
                    <X size={14} />
                  </button>
                </div>
              )}
            </section>

            <section className="panel-section">
              <div className="panel-heading">
                <span>节点库</span>
                <button className="icon-button" onClick={addAgent}>
                  <Plus size={15} />
                </button>
              </div>
              <button className="palette-agent" onClick={addAgent}>
                <span className="agent-icon">
                  <Bot size={15} />
                </span>
                <span>
                  <strong>Agent</strong>
                  <small>OpenAI Compatible</small>
                </span>
                <Plus size={14} />
              </button>
            </section>

            <section className="panel-section run-panel">
              <div className="panel-heading">
                <span>运行输入</span>
                <Activity size={14} />
              </div>
              <div className="run-inputs">
                {draft?.sdl.workflow.inputs.map((input) => {
                  const inputValue = runInputs[input.name]
                  return (
                    <div className="run-input-field" key={input.name}>
                      <span>
                        {input.name}
                        <small>{input.type}</small>
                      </span>
                      {input.type === 'FILE' ? (
                        <RunFileInput
                          name={input.name}
                          required={input.required}
                          value={
                            isUploadedRunFile(inputValue)
                              ? inputValue
                              : undefined
                          }
                          onSelect={(file) => {
                            void uploadRunFile(input.name, file)
                          }}
                          onRemove={() => removeRunFile(input.name)}
                        />
                      ) : input.type === 'TEXT' ||
                        input.type === 'MIXED' ? (
                        <textarea
                          rows={3}
                          aria-label={`${input.name} 输入`}
                          value={
                            typeof inputValue === 'string' ? inputValue : ''
                          }
                          onChange={(event) =>
                            setRunInputs((current) => ({
                              ...current,
                              [input.name]: event.target.value,
                            }))
                          }
                        />
                      ) : (
                        <textarea
                          rows={3}
                          aria-label={`${input.name} JSON 输入`}
                          value={
                            typeof inputValue === 'string'
                              ? inputValue
                              : '{}'
                          }
                          onChange={(event) =>
                            setRunInputs((current) => ({
                              ...current,
                              [input.name]: event.target.value,
                            }))
                          }
                        />
                      )}
                    </div>
                  )
                })}
              </div>
              <div className="run-summary">
                <span
                  className={`run-status status-${currentRun?.status ?? 'idle'}`}
                >
                  <CirclePlay size={13} />
                  {currentRun ? runStatusLabel(currentRun.status) : '未运行'}
                </span>
                <span className="run-summary-actions">
                  <span>{tokenUsage.toLocaleString()} tokens</span>
                  <button
                    className="icon-button"
                    type="button"
                    title="打开生成物目录"
                    disabled={!currentRun}
                    onClick={() => void openRunFolder(currentRun)}
                  >
                    <FolderOpen size={14} />
                  </button>
                </span>
              </div>
              {currentRun?.run_directory && (
                <button
                  className="run-output-folder"
                  type="button"
                  title={currentRun.output_directory ?? undefined}
                  onClick={() => void openRunFolder(currentRun)}
                >
                  <FolderOpen size={13} />
                  <span>{currentRun.run_directory}</span>
                </button>
              )}
              {currentRunError && <RunErrorPanel message={currentRunError} />}
              {pendingGates.length > 0 && (
                <div className="gate-list">
                  {pendingGates.map((gate) => {
                    const canDecide =
                      currentRunStatus === 'waiting' && replayAtLive
                    return (
                      <div className="gate-card" key={gate.gateId}>
                        <div className="gate-card-title">
                          <span>人工确认</span>
                          <code>{gate.edgeId ?? gate.gateId}</code>
                        </div>
                        <p>{gate.prompt}</p>
                        {gate.timeoutSeconds && (
                          <small>超时 {gate.timeoutSeconds} 秒</small>
                        )}
                        <textarea
                          rows={2}
                          placeholder="可选评论"
                          value={gateComments[gate.gateId] ?? ''}
                          disabled={!canDecide}
                          onChange={(event) =>
                            setGateComments((current) => ({
                              ...current,
                              [gate.gateId]: event.target.value,
                            }))
                          }
                        />
                        <div className="gate-actions">
                          <button
                            className="small-button"
                            disabled={
                              !canDecide ||
                              decidingGate === gate.gateId
                            }
                            onClick={() =>
                              void decideGate(gate.gateId, 'approve')
                            }
                          >
                            {decidingGate === gate.gateId ? (
                              <LoaderCircle size={13} className="spin" />
                            ) : (
                              <Check size={13} />
                            )}
                            通过
                          </button>
                          <button
                            className="small-button danger"
                            disabled={
                              !canDecide ||
                              decidingGate === gate.gateId
                            }
                            onClick={() =>
                              void decideGate(gate.gateId, 'reject')
                            }
                          >
                            <X size={13} />
                            拒绝
                          </button>
                        </div>
                        {!canDecide && (
                          <small>回放到运行末尾后可操作</small>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
              <div className="run-history">
                {runsQuery.data?.slice(0, 4).map((run) => (
                  <button
                    key={run.run_id}
                    className={currentRun?.run_id === run.run_id ? 'active' : ''}
                    onClick={() => loadRun(run)}
                  >
                    <span className={`run-dot dot-${run.status}`} />
                    <span>{formatTime(run.created_at)}</span>
                    <small>{runStatusLabel(run.status)}</small>
                  </button>
                ))}
              </div>
            </section>

            <button className="delete-project" onClick={deleteProject}>
              <Trash2 size={14} />
              删除项目
            </button>
          </>
        ) : (
          <button
            className="left-expand"
            title="展开导航栏"
            aria-controls="left-panel"
            onClick={() => setLeftPanelOpen(true)}
          >
            <PanelLeftOpen size={16} />
            <span>导航</span>
          </button>
        )}
      </aside>

      <main className="canvas-panel">
        {draft ? (
          <ReactFlow<AgentFlowNode, Edge>
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeDragStop={onNodeDragStop}
            onMoveEnd={onMoveEnd}
            onConnect={onConnect}
            onNodesDelete={onNodesDelete}
            onEdgesDelete={onEdgesDelete}
            onSelectionChange={onSelectionChange}
            defaultEdgeOptions={{ type: 'smoothstep' }}
            fitView
            fitViewOptions={{ padding: 0.3 }}
            minZoom={0.25}
            maxZoom={1.8}
            deleteKeyCode={['Backspace', 'Delete']}
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={22}
              size={1.3}
              color="#cdd4ce"
            />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              nodeColor={(node) => {
                const status = (node.data as { status?: NodeRunStatus }).status
                if (status === 'running') return '#167a70'
                if (status === 'failed') return '#b54c42'
                if (status === 'succeeded') return '#5e7d45'
                return '#b8c0b9'
              }}
              maskColor="rgba(237, 240, 235, 0.72)"
            />
          </ReactFlow>
        ) : (
          <div className="canvas-placeholder">
            <LoaderCircle className="spin" size={18} />
            <span>加载工作流</span>
          </div>
        )}
        <div className="canvas-hint">
          <Unplug size={13} />
          <span>拖动端口连线</span>
        </div>
      </main>

      <aside
        id="inspector-panel"
        className={classNames('inspector', !inspectorOpen && 'collapsed')}
      >
        {inspectorOpen ? (
          <>
            <div
              className="inspector-resize-handle"
              role="separator"
              aria-label="调整检查器宽度"
              aria-orientation="vertical"
              aria-valuemin={INSPECTOR_MIN_WIDTH}
              aria-valuemax={INSPECTOR_MAX_WIDTH}
              aria-valuenow={inspectorWidth}
              tabIndex={0}
              onKeyDown={resizeInspectorWithKeyboard}
              onPointerDown={startInspectorResize}
              onPointerMove={moveInspectorResize}
              onPointerUp={finishInspectorResize}
              onPointerCancel={finishInspectorResize}
              onLostPointerCapture={finishInspectorResize}
            />
            {renderInspectorTabs()}
            {inspectorTab === 'selection' && renderSelectionInspector()}
            {inspectorTab === 'workflow' && renderWorkflowInspector()}
            {inspectorTab === 'api' && renderApiInspector()}
          </>
        ) : (
          <button
            className="inspector-expand"
            title="展开检查器"
            aria-controls="inspector-panel"
            onClick={() => setInspectorOpen(true)}
          >
            <PanelRightOpen size={16} />
            <span>检查器</span>
          </button>
        )}
      </aside>

      <section id="drawer-panel" className="bottom-drawer">
        {drawerOpen && (
          <div
            className="drawer-resize-handle"
            role="separator"
            aria-label="调整底部面板高度"
            aria-orientation="horizontal"
            aria-valuemin={DRAWER_MIN_HEIGHT}
            aria-valuemax={DRAWER_MAX_HEIGHT}
            aria-valuenow={drawerHeight}
            tabIndex={0}
            onKeyDown={resizeDrawerWithKeyboard}
            onPointerDown={startDrawerResize}
            onPointerMove={moveDrawerResize}
            onPointerUp={finishDrawerResize}
            onPointerCancel={finishDrawerResize}
            onLostPointerCapture={finishDrawerResize}
          />
        )}
        <div className="drawer-tabs">
          {(
            [
              ['sdl', 'SDL', FileCode2],
              ['issues', `问题 ${issues.length || ''}`, CircleAlert],
              ['events', `事件 ${runEvents.length || ''}`, Terminal],
              ['result', '结果', Braces],
            ] as Array<[DrawerTab, string, typeof FileCode2]>
          ).map(([value, label, Icon]) => (
            <button
              key={value}
              className={drawerTab === value && drawerOpen ? 'active' : ''}
              onClick={() => {
                setDrawerTab(value)
                setDrawerOpen(true)
              }}
            >
              <Icon size={14} />
              {label}
            </button>
          ))}
          <button
            className="drawer-toggle"
            title={drawerOpen ? '收起面板' : '展开面板'}
            aria-controls="drawer-panel"
            onClick={() => setDrawerOpen(!drawerOpen)}
          >
            {drawerOpen ? (
              <PanelBottomClose size={15} />
            ) : (
              <PanelBottomOpen size={15} />
            )}
          </button>
        </div>
        {drawerOpen && (
          <div className="drawer-content">
            {drawerTab === 'sdl' && draft && (
              <SdlEditor
                draft={draft}
                onImported={(detail) => {
                  setDraft(detail)
                  setDirty(false)
                  setIssues([])
                  setSelection(null)
                  fileUploadIdsRef.current = {}
                  setRunInputs(initialRunInputs(detail.sdl.workflow.inputs))
                  queryClient.invalidateQueries({ queryKey: ['projects'] })
                  showNotice('success', `SDL 已导入 revision ${detail.revision}`)
                }}
                onError={(message) => showNotice('error', message)}
              />
            )}
            {drawerTab === 'issues' && (
              <div className="issues-list">
                {issues.length === 0 ? (
                  <div className="drawer-empty">
                    <CheckCircle2 size={17} />
                    当前没有校验问题
                  </div>
                ) : (
                  issues.map((issue, index) => (
                    <button
                      key={`${issue.code}-${index}`}
                      className={`issue-row issue-${issue.severity}`}
                      onClick={() => {
                        if (issue.node_id) {
                          setSelection({ kind: 'node', id: issue.node_id })
                          setInspectorTab('selection')
                        } else if (issue.edge_id) {
                          setSelection({ kind: 'edge', id: issue.edge_id })
                          setInspectorTab('selection')
                        }
                      }}
                    >
                      <CircleAlert size={14} />
                      <code>{issue.code}</code>
                      <span>{issue.message}</span>
                      <small>{issue.path}</small>
                    </button>
                  ))
                )}
              </div>
            )}
            {drawerTab === 'events' && (
              <div className="event-view">
                <div className="replay-toolbar">
                  <div className="replay-buttons">
                    <button
                      className="icon-button"
                      title="回到开始"
                      disabled={runEvents.length === 0}
                      onClick={() => {
                        setReplayPlaying(false)
                        setReplaySeq(0)
                      }}
                    >
                      <SkipBack size={14} />
                    </button>
                    <button
                      className="icon-button"
                      title="上一步"
                      disabled={runEvents.length === 0}
                      onClick={() => {
                        setReplayPlaying(false)
                        setReplaySeq(previousReplaySeq(runEvents, replayUntilSeq))
                      }}
                    >
                      <ChevronDown
                        size={14}
                        style={{ transform: 'rotate(90deg)' }}
                      />
                    </button>
                    <button
                      className="icon-button replay-play"
                      title={replayPlaying ? '暂停回放' : '播放回放'}
                      disabled={runEvents.length === 0}
                      onClick={() => {
                        if (!replayPlaying && replayUntilSeq >= replayMaxSeq) {
                          setReplaySeq(0)
                        }
                        setReplayPlaying((value) => !value)
                      }}
                    >
                      {replayPlaying ? (
                        <Pause size={14} />
                      ) : (
                        <Play size={14} fill="currentColor" />
                      )}
                    </button>
                    <button
                      className="icon-button"
                      title="下一步"
                      disabled={runEvents.length === 0}
                      onClick={() => {
                        setReplayPlaying(false)
                        const next = nextReplaySeq(runEvents, replayUntilSeq)
                        setReplaySeq(next ?? replayMaxSeq)
                      }}
                    >
                      <ChevronUp
                        size={14}
                        style={{ transform: 'rotate(90deg)' }}
                      />
                    </button>
                    <button
                      className="small-button"
                      title="跳到运行末尾"
                      disabled={runEvents.length === 0}
                      onClick={() => {
                        setReplayPlaying(false)
                        setReplaySeq(null)
                      }}
                    >
                      <SkipForward size={14} />
                      实时
                    </button>
                  </div>
                  <input
                    className="replay-slider"
                    type="range"
                    min={0}
                    max={Math.max(replayMaxSeq, 1)}
                    value={replayUntilSeq}
                    aria-label="回放进度"
                    disabled={runEvents.length === 0}
                    onChange={(event) => {
                      setReplayPlaying(false)
                      const value = Number(event.target.value)
                      setReplaySeq(
                        value >= replayMaxSeq ? null : value,
                      )
                    }}
                  />
                  <span className="replay-position">
                    {String(replayUntilSeq).padStart(3, '0')} /{' '}
                    {String(replayMaxSeq).padStart(3, '0')}
                  </span>
                  <select
                    className="replay-speed"
                    value={replaySpeed}
                    aria-label="回放速度"
                    onChange={(event) =>
                      setReplaySpeed(Number(event.target.value))
                    }
                  >
                    <option value={250}>0.25s</option>
                    <option value={500}>0.5s</option>
                    <option value={1000}>1s</option>
                    <option value={2000}>2s</option>
                  </select>
                </div>
                <div className="event-console">
                  {replayState.events.length === 0 ? (
                    <div className="drawer-empty">
                      <Terminal size={17} />
                      尚无运行事件
                    </div>
                  ) : (
                    replayState.events.map((event) => (
                      <div
                        className={`event-row event-${event.type}`}
                        key={`${event.run_id}-${event.seq}`}
                      >
                        <span>{String(event.seq).padStart(3, '0')}</span>
                        <time>{formatTime(event.ts)}</time>
                        <code>{event.type}</code>
                        <span>
                          {event.node_id ?? event.edge_id ?? 'workflow'}
                        </span>
                        <small>{JSON.stringify(event.data)}</small>
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}
            {drawerTab === 'result' && (
              <div className="result-view">
                {currentRunError && (
                  <RunErrorPanel message={currentRunError} />
                )}
                <div className="result-grid">
                  <div>
                    <span className="eyebrow">RUN RESULT</span>
                    <pre>
                      {JSON.stringify(
                        currentRun && 'result' in currentRun
                          ? currentRun.result
                          : null,
                        null,
                        2,
                      )}
                    </pre>
                  </div>
                  <div>
                    <div className="result-heading">
                      <span className="eyebrow">RUN ARTIFACTS</span>
                      <button
                        className="icon-button"
                        type="button"
                        title="打开生成物目录"
                        disabled={!currentRun}
                        onClick={() => void openRunFolder(currentRun)}
                      >
                        <FolderOpen size={14} />
                      </button>
                    </div>
                    <div className="artifact-list">
                      {runArtifacts(currentRun).length ? (
                        runArtifacts(currentRun).map((artifact) => (
                          <div className="artifact-row" key={artifact.path}>
                            <FileCode2 size={13} />
                            <span title={artifact.path}>{artifact.path}</span>
                            <small>{formatFileSize(artifact.size)}</small>
                          </div>
                        ))
                      ) : (
                        <span className="result-empty">尚无运行产物</span>
                      )}
                    </div>
                    {replayState.artifacts.length > 0 && (
                      <details className="generated-files">
                        <summary>
                          回放产物 {replayState.artifacts.length}
                        </summary>
                        <div className="artifact-list">
                          {replayState.artifacts.map((artifact) => (
                            <div
                              className="artifact-row"
                              key={`${artifact.seq}-${artifact.path}`}
                            >
                              <div>
                                <strong>{artifact.port}</strong>
                                <span>{artifact.type}</span>
                              </div>
                              <code>
                                runs/
                                {currentRun?.run_directory ??
                                  currentRun?.run_id ??
                                  'current'}
                                /workspace/{artifact.path}
                              </code>
                              <small>
                                {artifact.agentId ?? 'workflow'}
                                {artifact.batchId
                                  ? ` · ${artifact.batchId}`
                                  : ''}
                                {artifact.bytes !== null
                                  ? ` · ${artifact.bytes} B`
                                  : ''}
                              </small>
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                    {generatedFiles.length > 0 && (
                      <details className="generated-files">
                        <summary>生成文件</summary>
                        <pre>{generatedFiles.join('\n')}</pre>
                      </details>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  )
}

function JsonConditionEditor({
  value,
  onChange,
}: {
  value: ConditionNode | null | undefined
  onChange: (value: ConditionNode) => void
}) {
  const [text, setText] = useState(() =>
    JSON.stringify(value ?? { op: 'exists', left: '$.run.step' }, null, 2),
  )
  const [invalid, setInvalid] = useState(false)
  return (
    <>
      <textarea
        rows={8}
        value={text}
        className={invalid ? 'field-invalid' : ''}
        onChange={(event) => {
          setText(event.target.value)
          try {
            const parsed = JSON.parse(event.target.value)
            if (parsed && typeof parsed === 'object' && typeof parsed.op === 'string') {
              onChange(parsed as ConditionNode)
              setInvalid(false)
            } else {
              setInvalid(true)
            }
          } catch {
            setInvalid(true)
          }
        }}
        spellCheck={false}
      />
    </>
  )
}

export default App
